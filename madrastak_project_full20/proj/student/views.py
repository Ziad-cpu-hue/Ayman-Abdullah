import json
import random
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db.models import Avg, Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import student_required
from accounts.forms import PasswordChangeForm2, ProfileForm
from accounts.models import SiteSetting, StudentProfile, TeacherProfile, User
from accounts.utils import audit, normalize_phone, serve_private
from billing.models import Enrollment, Payment
from comms.models import Announcement, ChatMessage, Conversation, Notification
from comms.services import notify, notify_many
from learning import services as svc
from learning.models import (Attempt, ErrorRecord, Lesson, LessonFile, LessonProgress, LessonSection, Question, Quiz,
                             SavedQuestion, StudentAnswer, Unit, VocabularyProgress, VocabularyWord)

from .forms import MessageForm, SubscribeForm


# ------------------------------------------------------------------ مساعدات
def ws(request, code):
    """يجهّز سياق مساحة المدرّس: المدرّس، الاشتراك، وهل الوصول نشط."""
    t = get_object_or_404(TeacherProfile.objects.select_related("user"), teacher_code=code, user__is_active=True)
    enr = Enrollment.objects.filter(student=request.user, teacher=t).first()
    request.session["current_teacher"] = code
    return t, enr, bool(enr and enr.is_active)


def need_enrollment(request, t, enr):
    if not enr:
        messages.info(request, "اشترك مع هذا المدرّس أولًا لتفتح هذه الصفحة.")
        return redirect("student:workspace", code=t.teacher_code)
    return None


def need_active(request, t, active):
    if not active:
        messages.warning(request, "هذه الصفحة تتطلب اشتراكًا نشطًا مع المدرّس.")
        return redirect("student:subscribe", code=t.teacher_code)
    return None


def pmap(student, lessons):
    return {p.lesson_id: p for p in LessonProgress.objects.filter(student=student, lesson__in=lessons)}


def continue_lesson(student, teacher=None):
    qs = LessonProgress.objects.filter(student=student, completed=False).select_related("lesson__unit__course__teacher__user")
    if teacher:
        qs = qs.filter(lesson__unit__course__teacher=teacher)
    for p in qs.order_by("-last_viewed")[:10]:
        l = p.lesson
        if svc.visible_lessons_qs().filter(pk=l.pk).exists():
            return l
    return None


def next_new_lesson(student, teacher):
    done = LessonProgress.objects.filter(student=student, completed=True).values_list("lesson_id", flat=True)
    return svc.visible_lessons_qs(teacher=teacher).exclude(id__in=done).order_by("unit__course__order", "unit__order", "order", "id").first()


def ordered_lessons(course):
    return list(svc.visible_lessons_qs(course=course).order_by("unit__order", "unit_id", "order", "id"))


# ------------------------------------------------------------------ الرئيسية
@student_required
def home(request):
    u = request.user
    enrolls = list(Enrollment.objects.filter(student=u).select_related("teacher__user"))
    cards, totals = [], []
    for e in enrolls:
        pr = svc.teacher_progress(u, e.teacher)
        st = svc.teacher_stats_for_student(u, e.teacher)
        cards.append({"e": e, "t": e.teacher, "pr": pr, "st": st})
        totals.append(pr["percent"])
    active_ids = [e.teacher_id for e in enrolls if e.is_active]
    now = timezone.now()
    done_quiz_ids = set(Attempt.objects.filter(student=u, submitted_at__isnull=False).values_list("quiz_id", flat=True))
    upcoming = [q for q in svc.visible_quizzes_qs().filter(course__teacher_id__in=active_ids, kind="homework", deadline__gte=now)
                .exclude(id__in=done_quiz_ids).select_related("course__teacher__user").order_by("deadline")[:6] if q.questions.exists()]
    upcoming_exams = [q for q in svc.visible_quizzes_qs().filter(course__teacher_id__in=active_ids, kind__in=("exam", "quiz")).exclude(id__in=done_quiz_ids)
                      .select_related("course__teacher__user", "lesson")[:6] if q.questions.exists()]
    atts = list(Attempt.objects.filter(student=u, submitted_at__isnull=False).select_related("quiz__course__teacher__user")[:30])
    overall_pct = round(sum(a.percent for a in atts) / len(atts)) if atts else 0
    chart = [{"p": a.percent, "l": a.submitted_at.strftime("%d/%m")} for a in reversed(atts[:10])]
    cont = continue_lesson(u)
    teacher_ids = [e.teacher_id for e in enrolls]
    announcements = Announcement.objects.filter(Q(teacher_id__in=teacher_ids) | Q(teacher__isnull=True, audience__in=("all", "students")))[:4]
    ctx = {
        "cards": cards, "attempts": atts[:8], "chart": chart, "upcoming": upcoming, "upcoming_exams": upcoming_exams,
        "cont": cont, "announcements": announcements,
        "overview": {
            "teachers": len(enrolls), "active": len(active_ids),
            "tasks": len(upcoming), "quizzes": len(upcoming_exams),
            "progress": round(sum(totals) / len(totals)) if totals else 0,
            "avg": overall_pct, "attempts": len(atts),
            "lessons_done": LessonProgress.objects.filter(student=u, completed=True).count(),
            "errors": ErrorRecord.objects.filter(student=u, reviewed=False, resolved=False).count(),
        },
        "recent_notifs": Notification.objects.filter(user=u)[:5],
    }
    return render(request, "student/home.html", ctx)


# ------------------------------------------------------------------ المدرّسون والانضمام
@student_required
def teachers(request):
    q = request.GET.get("q", "").strip()
    qs = TeacherProfile.objects.filter(user__is_active=True).select_related("user")
    if q:
        qs = qs.filter(Q(user__full_name__icontains=q) | Q(subject__icontains=q) | Q(teacher_code__iexact=q))
    mine = {e.teacher_id: e for e in Enrollment.objects.filter(student=request.user)}
    rows = [{"t": t, "e": mine.get(t.id)} for t in qs.filter(Q(is_listed=True) | Q(id__in=mine.keys()))]
    return render(request, "student/teachers.html", {"rows": rows, "q": q})


@student_required
@require_POST
def join(request):
    code = (request.POST.get("code") or "").strip().upper().replace(" ", "")
    if TeacherProfile.objects.filter(teacher_code=code, user__is_active=True).exists():
        return redirect("student:workspace", code=code)
    messages.error(request, "لا يوجد مدرّس بهذا الرقم. تأكد من الرقم الذي أعطاك إياه مدرّسك.")
    return redirect("student:teachers")


# ------------------------------------------------------------------ مساحة المدرّس: المحتوى
@student_required
def workspace(request, code):
    t, enr, active = ws(request, code)
    u = request.user
    rows = []
    for c in svc.visible_courses(t):
        pr = svc.course_progress(u, c)
        rows.append({"c": c, "pr": pr, "units": svc.visible_units(c).count(), "open": active or c.is_free})
    st = svc.teacher_stats_for_student(u, t) if enr else None
    ctx = {"t": t, "enr": enr, "active": active, "rows": rows, "site_s": SiteSetting.get(),
           "progress": svc.teacher_progress(u, t), "stats": st,
           "cont": continue_lesson(u, t) if enr else None,
           "next_lesson": next_new_lesson(u, t) if active else None}
    return render(request, "student/workspace.html", ctx)


@student_required
def course(request, code, pk):
    t, enr, active = ws(request, code)
    c = get_object_or_404(svc.visible_courses(t), pk=pk)
    rows = []
    for un in svc.visible_units(c):
        pr = svc.unit_progress(request.user, un)
        rows.append({"u": un, "pr": pr, "quizzes": svc.visible_quizzes_qs(unit=un).count()})
    return render(request, "student/course.html", {"t": t, "enr": enr, "active": active, "c": c, "rows": rows,
                                                   "pr": svc.course_progress(request.user, c), "open": active or c.is_free})


@student_required
def unit(request, code, pk):
    t, enr, active = ws(request, code)
    un = get_object_or_404(Unit, pk=pk, course__teacher=t)
    if not svc.visible_units(un.course).filter(pk=un.pk).exists() or not svc.visible_courses(t).filter(pk=un.course_id).exists():
        raise Http404
    lessons = list(svc.visible_lessons_qs(unit=un))
    pm = pmap(request.user, lessons)
    open_all = active or un.course.is_free
    rows = []
    for i, l in enumerate(lessons, 1):
        rows.append({"l": l, "n": i, "status": svc.lesson_status(request.user, l, pm), "locked": not (open_all or l.is_free_preview),
                     "quiz": svc.visible_quizzes_qs(lesson=l).exists()})
    unit_quizzes = svc.visible_quizzes_qs(unit=un).filter(lesson__isnull=True)
    return render(request, "student/unit.html", {"t": t, "enr": enr, "active": active, "un": un, "rows": rows, "pr": svc.unit_progress(request.user, un),
                                                 "unit_quizzes": unit_quizzes, "open": open_all})


@student_required
def lesson(request, code, pk):
    t, enr, active = ws(request, code)
    l = get_object_or_404(svc.visible_lessons_qs(teacher=t), pk=pk)
    if not svc.can_open_lesson(request.user, l):
        return render(request, "student/lesson_locked.html", {"t": t, "enr": enr, "active": active, "l": l})
    svc.touch_lesson(request.user, l)
    ids = [x.id for x in ordered_lessons(l.course)]
    idx = ids.index(l.id) if l.id in ids else 0
    prev_l = Lesson.objects.filter(pk=ids[idx - 1]).first() if idx > 0 else None
    next_l = Lesson.objects.filter(pk=ids[idx + 1]).first() if idx + 1 < len(ids) else None
    if next_l and not svc.can_open_lesson(request.user, next_l):
        next_l = next_l  # يظهر الزر لكن يقود لصفحة القفل
    prog = LessonProgress.objects.filter(student=request.user, lesson=l).first()
    quizzes = svc.visible_quizzes_qs(lesson=l)
    qinfo = []
    for q in quizzes:
        n = q.questions.count()
        last = Attempt.objects.filter(student=request.user, quiz=q, submitted_at__isnull=False).first()
        qinfo.append({"q": q, "n": n, "last": last})
    return render(request, "student/lesson.html", {
        "t": t, "enr": enr, "active": active, "l": l, "sections": l.sections.all(), "files": l.files.all(),
        "vocab_count": l.vocabulary.count(), "prev_l": prev_l, "next_l": next_l, "prog": prog, "qinfo": qinfo,
        "position": idx + 1, "total": len(ids), "preview": False,
    })


@student_required
@require_POST
def lesson_complete(request, code, pk):
    t, enr, active = ws(request, code)
    l = get_object_or_404(svc.visible_lessons_qs(teacher=t), pk=pk)
    if not svc.can_open_lesson(request.user, l):
        return redirect("student:lesson", code=code, pk=pk)
    svc.complete_lesson(request.user, l)
    ids = [x.id for x in ordered_lessons(l.course)]
    idx = ids.index(l.id) if l.id in ids else -1
    if idx >= 0 and idx + 1 < len(ids):
        messages.success(request, "أحسنت! تم تسجيل إكمال الدرس.")
        return redirect("student:lesson", code=code, pk=ids[idx + 1])
    messages.success(request, "أكملت آخر درس في هذه الدورة. عمل رائع! 🎉")
    return redirect("student:course", code=code, pk=l.course.pk)


@student_required
def lesson_file(request, pk):
    f = get_object_or_404(LessonFile, pk=pk)
    l = f.lesson
    if not svc.visible_lessons_qs().filter(pk=l.pk).exists() or not svc.can_open_lesson(request.user, l):
        raise Http404
    return serve_private(request, f.file, download_name=f.file.name.split("/")[-1])


@student_required
def lesson_video(request, pk):
    l = get_object_or_404(Lesson, pk=pk)
    if not svc.visible_lessons_qs().filter(pk=l.pk).exists() or not svc.can_open_lesson(request.user, l):
        raise Http404
    return serve_private(request, l.video_file)


# ------------------------------------------------------------------ الاختبارات
def _quiz_or_404(t, pk):
    return get_object_or_404(svc.visible_quizzes_qs(teacher=t).select_related("course", "lesson", "unit"), pk=pk)


@student_required
def quiz(request, code, pk):
    t, enr, active = ws(request, code)
    q = _quiz_or_404(t, pk)
    if not svc.can_open_quiz(request.user, q):
        messages.warning(request, "هذا الاختبار يتطلب اشتراكًا نشطًا.")
        return redirect("student:subscribe", code=code)
    questions = list(q.questions.prefetch_related("choices"))
    if not questions:
        messages.info(request, "لم يضف المدرّس أسئلة لهذا الاختبار بعد.")
        return redirect(request.META.get("HTTP_REFERER") or "student:workspace", code=code) if False else redirect("student:workspace", code=code)
    done = Attempt.objects.filter(student=request.user, quiz=q, submitted_at__isnull=False).count()
    if q.max_attempts and done >= q.max_attempts:
        messages.warning(request, "استنفدت عدد المحاولات المسموحة لهذا الاختبار.")
        last = Attempt.objects.filter(student=request.user, quiz=q, submitted_at__isnull=False).first()
        return redirect("student:result", code=code, pk=last.pk)
    if q.kind == "homework" and q.is_closed:
        messages.warning(request, "انتهى موعد تسليم هذا الواجب.")
        return redirect("student:assignments", code=code)
    attempt = Attempt.objects.filter(student=request.user, quiz=q, submitted_at__isnull=True).first()
    if request.method == "POST":
        if not attempt:
            attempt = Attempt.objects.create(student=request.user, quiz=q)
        svc.grade_attempt(attempt, request.POST)
        return redirect("student:result", code=code, pk=attempt.pk)
    if not attempt:
        attempt = Attempt.objects.create(student=request.user, quiz=q)
    seconds = 0
    if q.duration_minutes:
        seconds = max(30, int(q.duration_minutes * 60 - (timezone.now() - attempt.started_at).total_seconds()))
    return render(request, "student/quiz.html", {"t": t, "q": q, "questions": questions, "seconds": seconds, "total": sum(x.points for x in questions),
                                                 "attempt_no": done + 1, "preview": False})


@student_required
def result(request, code, pk):
    t, enr, active = ws(request, code)
    a = get_object_or_404(Attempt.objects.select_related("quiz__lesson", "quiz__course"), pk=pk, student=request.user, quiz__course__teacher=t, submitted_at__isnull=False)
    answers = list(a.answers.select_related("question__section__lesson__unit__course", "question__quiz__lesson__unit__course", "choice")
                   .prefetch_related("question__choices"))
    answers.sort(key=lambda x: (x.question.order, x.question_id))
    items, wrong = [], []
    skipped = 0
    for n, ans in enumerate(answers, 1):
        qq = ans.question
        if qq.qtype == "essay":
            # سؤال مقالي: لا يُصحَّح آليًا ولا يُحسب صحيحًا أو خاطئًا
            items.append({"n": n, "a": ans, "q": qq, "choices": [], "chosen": None, "correct": None,
                          "status": "essay", "earned": 0, "sim": None, "lesson": None})
            continue
        if ans.is_correct:
            status = "correct"
        elif ans.choice_id is None:
            status = "skipped"
            skipped += 1
        else:
            status = "wrong"
        lesson = (qq.section.lesson if qq.section_id else qq.quiz.lesson)
        sim = svc.similar_question(qq) if not ans.is_correct else None
        item = {"n": n, "a": ans, "q": qq, "choices": list(qq.choices.all()), "chosen": ans.choice, "correct": qq.correct_choice,
                "status": status, "earned": qq.points if ans.is_correct else 0, "sim": sim, "lesson": lesson}
        items.append(item)
        if not ans.is_correct:
            wrong.append(item)
    right = [i for i in items if i["status"] == "correct"]
    from assistant import explain  # شروح الأسئلة الجاهزة مسبقًا (آمن: أي خطأ لا يكسر الصفحة)
    explain.attach(items)
    prev = Attempt.objects.filter(student=request.user, quiz=a.quiz, submitted_at__isnull=False, submitted_at__lt=a.submitted_at).first()
    pct = a.percent
    offset = lambda circ: round(circ * (100 - min(max(pct, 0), 100)) / 100, 2)
    return render(request, "student/result.html", {"t": t, "a": a, "q": a.quiz, "wrong": wrong, "right": right, "prev": prev,
                                                   "items": items, "skipped_count": skipped, "wrong_only": a.wrong_count - skipped,
                                                   "question_count": len(items), "problem_count": len(wrong),
                                                   "ring_big": offset(238.76), "ring_small": offset(163.36),
                                                   "passed": pct >= 50,
                                                   "can_retry": not (a.quiz.max_attempts and Attempt.objects.filter(student=request.user, quiz=a.quiz, submitted_at__isnull=False).count() >= a.quiz.max_attempts)
                                                   and not (a.quiz.kind == "homework" and a.quiz.is_closed)})


@student_required
def assignments(request, code):
    t, enr, active = ws(request, code)
    if (r := need_enrollment(request, t, enr)):
        return r
    now = timezone.now()
    quizzes = svc.visible_quizzes_qs(teacher=t).select_related("lesson", "unit", "course")
    atts = {}
    for a in Attempt.objects.filter(student=request.user, quiz__in=quizzes, submitted_at__isnull=False).order_by("submitted_at"):
        atts.setdefault(a.quiz_id, []).append(a)
    homework, exams = [], []
    for q in quizzes:
        n = q.questions.count()
        if not n:
            continue
        mine = atts.get(q.id, [])
        best = max((a.percent for a in mine), default=None)
        last = mine[-1] if mine else None
        if q.kind == "homework":
            if mine:
                state = ("completed", "مكتمل", "green")
            elif q.is_closed:
                state = ("late", "فات موعده", "red")
            elif q.deadline and q.deadline - now < timedelta(days=2):
                state = ("soon", "موعده قريب", "amber")
            else:
                state = ("available", "متاح", "blue")
            homework.append({"q": q, "n": n, "best": best, "last": last, "state": state, "tries": len(mine)})
        else:
            exams.append({"q": q, "n": n, "best": best, "last": last, "tries": len(mine)})
    return render(request, "student/assignments.html", {"t": t, "enr": enr, "active": active, "homework": homework, "exams": exams})


# ------------------------------------------------------------------ بنك الأسئلة
def _bank_qs(request, t):
    qs = Question.objects.filter(quiz__in=svc.visible_quizzes_qs(teacher=t)).exclude(qtype="essay").select_related("quiz__lesson", "quiz__unit", "section")
    g = request.GET
    if g.get("unit", "").isdigit():
        qs = qs.filter(quiz__unit_id=int(g["unit"]))
    if g.get("lesson", "").isdigit():
        qs = qs.filter(quiz__lesson_id=int(g["lesson"]))
    if g.get("difficulty") in ("easy", "medium", "hard"):
        qs = qs.filter(difficulty=g["difficulty"])
    if g.get("qtype") in ("mcq", "tf"):
        qs = qs.filter(qtype=g["qtype"])
    if g.get("saved") == "1":
        qs = qs.filter(id__in=SavedQuestion.objects.filter(student=request.user).values_list("question_id", flat=True))
    if g.get("wrong") == "1":
        qs = qs.filter(id__in=ErrorRecord.objects.filter(student=request.user, resolved=False).values_list("question_id", flat=True))
    return qs


def _bank_access(request, t, enr, active):
    return need_enrollment(request, t, enr) or need_active(request, t, active)


@student_required
def bank(request, code):
    t, enr, active = ws(request, code)
    if (r := _bank_access(request, t, enr, active)):
        return r
    qs = _bank_qs(request, t)
    units = svc.visible_units_for_teacher(t) if hasattr(svc, "visible_units_for_teacher") else Unit.objects.filter(course__in=svc.visible_courses(t)).filter(Q(status="published") | Q(status="scheduled", publish_at__lte=timezone.now()))
    lessons = svc.visible_lessons_qs(teacher=t)
    saved = set(SavedQuestion.objects.filter(student=request.user).values_list("question_id", flat=True))
    solved = set(StudentAnswer.objects.filter(attempt__student=request.user, is_correct=True).values_list("question_id", flat=True))
    return render(request, "student/bank.html", {"t": t, "questions": qs[:60], "count": qs.count(), "units": units, "lessons": lessons,
                                                 "saved": saved, "solved": solved, "g": request.GET, "qstr": request.GET.urlencode()})


@student_required
def bank_start(request, code):
    t, enr, active = ws(request, code)
    if (r := _bank_access(request, t, enr, active)):
        return r
    ids = list(_bank_qs(request, t).values_list("id", flat=True))
    if not ids:
        messages.info(request, "لا توجد أسئلة مطابقة للفلاتر المختارة.")
        return redirect("student:bank", code=code)
    from django.urls import reverse
    return redirect(reverse("student:bank_question", args=[code, random.choice(ids)]) + "?" + request.GET.urlencode())


@student_required
def bank_question(request, code, pk):
    t, enr, active = ws(request, code)
    if (r := _bank_access(request, t, enr, active)):
        return r
    q = get_object_or_404(_bank_qs(request, t).model.objects.filter(quiz__in=svc.visible_quizzes_qs(teacher=t)).prefetch_related("choices"), pk=pk)
    chosen = None
    correct = None
    if request.method == "POST":
        raw = request.POST.get("choice", "")
        chosen = next((c for c in q.choices.all() if str(c.id) == raw), None)
        correct = q.correct_choice
        ok = bool(chosen and chosen.is_correct)
        if ok:
            ErrorRecord.objects.filter(student=request.user, question=q).update(resolved=True)
        else:
            rec, created = ErrorRecord.objects.get_or_create(student=request.user, question=q, defaults={"choice": chosen})
            if not created:
                rec.choice, rec.times_wrong, rec.resolved, rec.reviewed = chosen, rec.times_wrong + 1, False, False
                rec.save()
    ids = list(_bank_qs(request, t).exclude(id=q.id).values_list("id", flat=True))
    lesson_obj = q.section.lesson if q.section_id else q.quiz.lesson
    return render(request, "student/bank_question.html", {
        "t": t, "q": q, "chosen": chosen, "correct": correct, "answered": request.method == "POST",
        "ok": bool(chosen and chosen.is_correct), "next_id": random.choice(ids) if ids else None, "sim": svc.similar_question(q),
        "lesson": lesson_obj, "qstr": request.GET.urlencode(), "is_saved": SavedQuestion.objects.filter(student=request.user, question=q).exists()})


@student_required
@require_POST
def bank_save(request, pk):
    q = get_object_or_404(Question, pk=pk)
    obj, created = SavedQuestion.objects.get_or_create(student=request.user, question=q)
    if not created:
        obj.delete()
    return redirect(request.POST.get("next") or "student:home")


# ------------------------------------------------------------------ أخطائي وتقدّمي
@student_required
def mistakes(request, code):
    t, enr, active = ws(request, code)
    if (r := need_enrollment(request, t, enr)):
        return r
    qs = ErrorRecord.objects.filter(student=request.user, question__quiz__course__teacher=t).select_related(
        "question__quiz__lesson__unit", "question__quiz__unit", "question__section__lesson", "choice").prefetch_related("question__choices")
    f = request.GET.get("f", "open")
    if f == "open":
        qs = qs.filter(reviewed=False, resolved=False)
    elif f == "reviewed":
        qs = qs.filter(reviewed=True)
    elif f == "resolved":
        qs = qs.filter(resolved=True)
    rows = []
    for e in qs[:80]:
        qq = e.question
        lesson_obj = qq.section.lesson if qq.section_id else qq.quiz.lesson
        rows.append({"e": e, "q": qq, "lesson": lesson_obj, "unit": (lesson_obj.unit if lesson_obj else qq.quiz.unit), "correct": qq.correct_choice, "sim": svc.similar_question(qq)})
    counts = {"open": ErrorRecord.objects.filter(student=request.user, question__quiz__course__teacher=t, reviewed=False, resolved=False).count(),
              "all": ErrorRecord.objects.filter(student=request.user, question__quiz__course__teacher=t).count()}
    return render(request, "student/mistakes.html", {"t": t, "rows": rows, "f": f, "counts": counts})


@student_required
@require_POST
def mistake_reviewed(request, code, pk):
    t, enr, active = ws(request, code)
    ErrorRecord.objects.filter(pk=pk, student=request.user, question__quiz__course__teacher=t).update(reviewed=True)
    messages.success(request, "تم تعليم الخطأ كمُراجَع.")
    return redirect(request.POST.get("next") or "student:mistakes", code=code) if not request.POST.get("next") else redirect(request.POST["next"])


@student_required
def progress(request, code):
    t, enr, active = ws(request, code)
    if (r := need_enrollment(request, t, enr)):
        return r
    u = request.user
    pr = svc.teacher_progress(u, t)
    units = []
    done_units = 0
    for c in svc.visible_courses(t):
        for un in svc.visible_units(c):
            up = svc.unit_progress(u, un)
            units.append({"u": un, "c": c, "pr": up})
            if up["total"] and up["done"] == up["total"]:
                done_units += 1
    atts = list(Attempt.objects.filter(student=u, quiz__course__teacher=t, submitted_at__isnull=False).select_related("quiz").order_by("submitted_at"))
    pcts = [a.percent for a in atts]
    trend = None
    if len(pcts) >= 2:
        half = max(1, len(pcts) // 2)
        early, late = pcts[:half], pcts[half:]
        trend = round(sum(late) / len(late)) - round(sum(early) / len(early))
    stats = svc.teacher_stats_for_student(u, t)
    solved = StudentAnswer.objects.filter(attempt__student=u, attempt__quiz__course__teacher=t).count()
    reviewed = ErrorRecord.objects.filter(student=u, question__quiz__course__teacher=t, reviewed=True).count()
    weak = (ErrorRecord.objects.filter(student=u, question__quiz__course__teacher=t, resolved=False)
            .values("question__section__lesson__title", "question__quiz__lesson__title").annotate(n=Count("id")).order_by("-n")[:5])
    weak_rows = [{"title": w["question__section__lesson__title"] or w["question__quiz__lesson__title"] or "اختبار عام", "n": w["n"]} for w in weak]
    return render(request, "student/progress.html", {
        "t": t, "pr": pr, "units": units, "done_units": done_units, "total_units": len(units), "stats": stats, "solved": solved,
        "reviewed": reviewed, "atts": list(reversed(atts))[:12], "chart": [{"p": a.percent, "l": a.submitted_at.strftime("%d/%m")} for a in atts[-12:]],
        "trend": trend, "first_pct": pcts[0] if pcts else None, "last_pct": pcts[-1] if pcts else None, "weak": weak_rows})


# ------------------------------------------------------------------ المفردات
@student_required
def vocabulary(request, code):
    t, enr, active = ws(request, code)
    if (r := need_enrollment(request, t, enr)):
        return r
    if (r := need_active(request, t, active)):
        return r
    lessons_with_words = svc.visible_lessons_qs(teacher=t).filter(vocabulary__isnull=False).distinct()
    words = VocabularyWord.objects.filter(lesson__in=lessons_with_words).select_related("lesson")
    lid = request.GET.get("lesson", "")
    if lid.isdigit():
        words = words.filter(lesson_id=int(lid))
    mode = request.GET.get("mode", "learn")
    if mode not in ("learn", "practice", "test", "review"):
        mode = "learn"
    streaks = dict(VocabularyProgress.objects.filter(student=request.user, word__in=words).values_list("word_id", "correct_streak"))
    review_ids = set(VocabularyProgress.objects.filter(student=request.user, word__in=words, wrong_count__gt=0, correct_streak__lt=2).values_list("word_id", flat=True))
    if mode == "review":
        words = words.filter(id__in=review_ids)
    words = list(words)

    def _level(text):
        # لا يوجد حقل صعوبة للمفردات في قاعدة البيانات، فيُقدَّر المستوى من طول الكلمة (بدون أي تعديل على الموديلات).
        t_ = (text or "").strip()
        n = len(t_.replace(" ", ""))
        if " " in t_ or n >= 10:
            return "hard"
        return "medium" if n >= 7 else "easy"

    data = [{"id": w.id, "word": w.word, "meaning": w.meaning, "example": w.example,
             "example_translation": w.example_translation, "level": _level(w.word),
             "progress": min(streaks.get(w.id, 0), 5)} for w in words]
    return render(request, "student/vocabulary.html", {"t": t, "words": words, "mode": mode, "lessons": lessons_with_words, "lid": lid,
                                                       "data": data, "review_count": len(review_ids), "review_ids": review_ids})


@student_required
@require_POST
def vocab_record(request, code):
    t, enr, active = ws(request, code)
    try:
        results = json.loads(request.body).get("results", [])
    except Exception:
        return JsonResponse({"ok": False}, status=400)
    valid = set(VocabularyWord.objects.filter(lesson__unit__course__teacher=t).values_list("id", flat=True))
    for r in results:
        wid = r.get("id")
        if wid not in valid:
            continue
        vp, _ = VocabularyProgress.objects.get_or_create(student=request.user, word_id=wid)
        if r.get("ok"):
            vp.correct_streak += 1
        else:
            vp.wrong_count += 1
            vp.correct_streak = 0
        vp.save()
    return JsonResponse({"ok": True})


# ------------------------------------------------------------------ الرسائل والإعلانات
@student_required
def messages_view(request, code):
    t, enr, active = ws(request, code)
    if (r := need_enrollment(request, t, enr)):
        return r
    conv, _ = Conversation.objects.get_or_create(student=request.user, teacher=t)
    form = MessageForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not active:
            messages.warning(request, "التواصل مع المدرّس متاح للمشتركين اشتراكًا نشطًا.")
        elif form.is_valid():
            ChatMessage.objects.create(conversation=conv, sender=request.user, text=form.cleaned_data["text"], image=form.cleaned_data["image"] or "")
            conv.updated_at = timezone.now()
            conv.save(update_fields=["updated_at"])
            notify(t.user, f"رسالة جديدة من {request.user.full_name}", form.cleaned_data["text"][:100], f"/teacher/messages/{conv.pk}/", "message")
            return redirect("student:messages", code=code)
    conv.messages.exclude(sender=request.user).update(is_read=True)
    return render(request, "student/messages.html", {"t": t, "active": active, "conv": conv, "msgs": conv.messages.select_related("sender"), "form": form})


@student_required
def announcements(request, code):
    t, enr, active = ws(request, code)
    if (r := need_enrollment(request, t, enr)):
        return r
    items = Announcement.objects.filter(Q(teacher=t) | Q(teacher__isnull=True, audience__in=("all", "students")))[:40]
    return render(request, "student/announcements.html", {"t": t, "items": items})


# ------------------------------------------------------------------ الاشتراك والدفع
@student_required
def subscribe(request, code):
    t, enr, active = ws(request, code)
    site_s = SiteSetting.get()
    pending = enr.payments.filter(status="pending").first() if enr else None
    days_left = enr.days_left if enr else 0
    form = SubscribeForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if pending:
            messages.info(request, "لديك طلب قيد المراجعة بالفعل. سنُبلغك فور التفعيل.")
            return redirect("student:subscribe", code=code)
        if form.is_valid():
            if not enr:
                enr = Enrollment.objects.create(student=request.user, teacher=t)
            p = form.save(commit=False)
            p.enrollment, p.amount = enr, t.monthly_price
            p.sender_phone = normalize_phone(p.sender_phone)
            p.save()
            if enr.status in ("rejected", "suspended"):
                pass
            elif enr.status == "expired":
                enr.status = "pending"
                enr.save(update_fields=["status"])
            owners = User.objects.filter(Q(role="owner") | Q(role="staff", staff_role="finance"), is_active=True)
            notify_many(owners, "طلب تفعيل اشتراك جديد", f"{request.user.full_name} — {t.name} ({t.subject})", "/owner/payments/", "payment")
            audit(request, "طلب اشتراك", f"{request.user.phone} ← {t.teacher_code}", f"{p.amount} جنيه")
            messages.success(request, "تم استلام إيصال الدفع بنجاح. سيراجعه فريقنا ويفعّل اشتراكك قريبًا.")
            return redirect("student:subscribe", code=code)
    history = enr.payments.all() if enr else []
    return render(request, "student/subscribe.html", {"t": t, "enr": enr, "active": active, "pending": pending, "form": form, "site_s": site_s,
                                                      "history": history, "days_left": days_left})


@student_required
def subscriptions(request):
    enrolls = Enrollment.objects.filter(student=request.user).select_related("teacher__user").prefetch_related("payments")
    return render(request, "student/subscriptions.html", {"enrolls": enrolls})


# ------------------------------------------------------------------ الإشعارات والملف الشخصي
@student_required
def notifications(request):
    items = Notification.objects.filter(user=request.user)[:60]
    return render(request, "student/notifications.html", {"items": items})


@student_required
@require_POST
def notifications_read_all(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect("student:notifications")


@student_required
def notification_go(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.is_read = True
    n.save(update_fields=["is_read"])
    return redirect(n.url if n.url.startswith("/") else "student:notifications")


@student_required
def profile(request):
    sp = request.user.student_profile
    form = ProfileForm(request.POST or None, request.FILES or None, initial={"full_name": request.user.full_name, "parent_phone": sp.parent_phone, "grade": sp.grade})
    pform = PasswordChangeForm2(request.user, request.POST or None, prefix="pw")
    if request.method == "POST":
        if "pw-old" in request.POST:
            if pform.is_valid():
                request.user.set_password(pform.cleaned_data["new1"])
                request.user.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, "تم تغيير كلمة المرور.")
                return redirect("student:profile")
        elif form.is_valid():
            c = form.cleaned_data
            from accounts.translit import apply_name_policy
            request.user.full_name, name_ar = apply_name_policy(c["full_name"])
            fields = ["full_name"]
            if name_ar:
                request.user.name_ar = name_ar
                fields.append("name_ar")
            request.user.save(update_fields=fields)
            sp.parent_phone, sp.grade = c["parent_phone"], int(c["grade"])
            if c.get("avatar"):
                sp.avatar = c["avatar"]
            sp.save()
            messages.success(request, "تم حفظ بياناتك.")
            return redirect("student:profile")
    if request.method != "POST" or "pw-old" not in request.POST:
        pform = PasswordChangeForm2(request.user, prefix="pw")
    has_active = any(e.display_status == "active" for e in Enrollment.objects.filter(student=request.user))
    return render(request, "student/profile.html", {"form": form, "pform": pform, "sp": sp, "has_active_enrollment": has_active})
