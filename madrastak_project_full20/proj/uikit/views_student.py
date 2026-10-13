"""صفحات مساندة للطالب في الواجهة الجديدة (تجميع/عرض فقط — لا تغيّر منطق الصلاحيات القائم).

كل الصفحات تستخدم نفس دوال الرؤية والصلاحيات الموجودة في learning.services ونفس مساعد student.views.ws.
"""
from django.contrib import messages
from django.db.models import Count, Q
from django.templatetags.static import static
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import student_required
from accounts.models import TeacherProfile
from billing.models import Enrollment
from learning import services as svc
from learning.models import Attempt, ErrorRecord, Lesson, LessonProgress, Quiz
from student.views import ws


@student_required
def content_entry(request):
    """تبويب «محتوى الدروس»: أول ما يظهر هو دورات المعلم الذي اشترك معه الطالب."""
    enrolls = list(Enrollment.objects.filter(student=request.user, teacher__user__is_active=True).select_related("teacher__user"))
    code = request.session.get("current_teacher")
    pick = next((e for e in enrolls if e.teacher.teacher_code == code), None)
    if not pick:
        pick = next((e for e in enrolls if e.is_active), None) or (enrolls[0] if enrolls else None)
    if pick:
        return redirect("student:workspace", code=pick.teacher.teacher_code)
    messages.info(request, "اختر معلمك أولًا وفعّل اشتراكك لتظهر لك دورات المعلم.")
    return redirect("student:teachers")


@student_required
def performance(request):
    """تقارير الأداء: ملخص عبر كل المعلمين المشترك معهم (يعتمد على نفس دوال التقدّم القائمة)."""
    import math
    from datetime import timedelta
    from comms.models import ChatMessage
    u = request.user
    now = timezone.now()
    enrolls = list(Enrollment.objects.filter(student=u).select_related("teacher__user"))
    cards = []
    for e in enrolls:
        pr = svc.teacher_progress(u, e.teacher)
        st = svc.teacher_stats_for_student(u, e.teacher)
        cards.append({"e": e, "t": e.teacher, "pr": pr, "st": st})
    atts = list(Attempt.objects.filter(student=u, submitted_at__isnull=False).select_related("quiz__course__teacher__user", "quiz__lesson").order_by("submitted_at"))
    all_pcts = [a.percent for a in atts]
    trend = None
    if len(all_pcts) >= 2:
        half = max(1, len(all_pcts) // 2)
        early, late = all_pcts[:half], all_pcts[half:]
        trend = round(sum(late) / len(late)) - round(sum(early) / len(early))
    weak = (ErrorRecord.objects.filter(student=u, resolved=False)
            .values("question__section__lesson__title", "question__quiz__lesson__title", "question__quiz__course__teacher__user__full_name", "question__quiz__course__teacher__display_name")
            .annotate(n=Count("id")).order_by("-n")[:6])
    weak_rows = [{"title": w["question__section__lesson__title"] or w["question__quiz__lesson__title"] or "اختبار عام",
                  "teacher": (w["question__quiz__course__teacher__display_name"] or "").strip() or w["question__quiz__course__teacher__user__full_name"],
                  "n": w["n"]} for w in weak]
    avg = round(sum(all_pcts) / len(all_pcts)) if all_pcts else None
    lessons_done = LessonProgress.objects.filter(student=u, completed=True).count()
    open_errors = ErrorRecord.objects.filter(student=u, reviewed=False, resolved=False).count()

    # ---- الاشتراك
    active = [e for e in enrolls if e.is_active]
    days_left = max([e.days_left for e in active], default=0)
    days_total = 30 * max(1, math.ceil(days_left / 30)) if days_left else 30
    sub_badge = "نشط" if active else ("لا اشتراك" if not enrolls else "منتهٍ")

    # ---- إجماليات المحتوى (للمعلمين المشترك معهم)
    t_ids = [e.teacher_id for e in enrolls]
    lessons_qs = svc.visible_lessons_qs().filter(unit__course__teacher_id__in=t_ids) if t_ids else Lesson.objects.none()
    lessons_total = max(lessons_qs.count(), lessons_done)
    video_qs = lessons_qs.filter(Q(video_url__gt="") | Q(video_file__gt="")) if t_ids else Lesson.objects.none()
    lectures_total = video_qs.count()
    lectures_done = LessonProgress.objects.filter(student=u, lesson__in=video_qs).count() if lectures_total else 0
    quizzes_total = 0
    for tid in t_ids:
        quizzes_total += svc.visible_quizzes_qs().filter(course__teacher_id=tid, questions__isnull=False).distinct().count()
    quizzes_done = len({a.quiz_id for a in atts})
    quizzes_total = max(quizzes_total, quizzes_done)
    pct = lambda a, b: round(a * 100 / b) if b else 0

    # ---- النقاط
    pts_earned = sum(a.score for a in atts)
    pts_total = sum(a.total for a in atts)

    # ---- أداء المواد (لكل معلم مشترك معه)
    colors = ["#3F8BF9", "#36CE8F", "#8299DF", "#FBB459", "#E87881", "#4AC7C2"]
    subjects = []
    for i, c in enumerate(cards[:8]):
        subjects.append({"label": c["t"].subject, "p": c["st"]["avg_percent"], "color": colors[i % 6], "code": c["t"].teacher_code,
                         "tip": f'{c["t"].display_label} — التقدّم {c["pr"]["percent"]}% • المتوسط {c["st"]["avg_percent"]}% • أخطاء {c["st"]["errors_open"]}',
                         "status": c["e"].display_label})

    # ---- الرسائل
    msgs_sent = ChatMessage.objects.filter(conversation__student=u, sender=u)
    n_msgs = msgs_sent.count()
    teachers_msgd = msgs_sent.values("conversation__teacher").distinct().count()
    recent_msgs = list(ChatMessage.objects.filter(conversation__student=u).select_related("conversation__teacher__user").order_by("-created_at")[:3])
    cur = request.session.get("current_teacher")
    pick = next((e for e in enrolls if e.teacher.teacher_code == cur), None) or next((e for e in enrolls if e.is_active), None) or (enrolls[0] if enrolls else None)
    msg_code = pick.teacher.teacher_code if pick else ""

    # ---- توزيع الأنشطة
    parts = [("مشاهدة محاضرات", lectures_done, "#3F8BF9"), ("حل اختبارات", len(atts), "#36CE8F"), ("قراءة المحتوى", lessons_done, "#FBB459"),
             ("التفاعل مع المعلم", n_msgs, "#E87881"), ("أخرى", 0, "#4AC7C2")]
    tot = sum(x[1] for x in parts)
    C, cum, donut = 402.12, 0.0, []
    for lbl, n, col in parts:
        ln = round(C * n / tot, 2) if tot else 0
        donut.append({"label": lbl, "n": n, "color": col, "len": ln, "off": -round(cum, 2)})
        cum += ln

    # ---- المدة الكلية (دقائق): مدة المحاولات + زمن قراءة الدروس المكتملة
    mins = 0
    for a in atts:
        if a.started_at and a.submitted_at:
            mins += min(180, max(0, int((a.submitted_at - a.started_at).total_seconds() // 60)))
    for l in Lesson.objects.filter(progress__student=u, progress__completed=True)[:300]:
        mins += l.reading_minutes

    # ---- نشاط المنصة (آخر 90 يوماً) + أحدث الأنشطة
    since = now - timedelta(days=90)
    day_counts = {}
    def bump(dt):
        d = timezone.localtime(dt).date().isoformat()
        day_counts[d] = day_counts.get(d, 0) + 1
    lp_recent = list(LessonProgress.objects.filter(student=u, completed=True, completed_at__isnull=False).select_related("lesson__unit__course__teacher__user").order_by("-completed_at")[:15])
    for a in atts:
        if a.submitted_at >= since:
            bump(a.submitted_at)
    for lp in lp_recent:
        if lp.completed_at >= since:
            bump(lp.completed_at)
    for m in msgs_sent.filter(created_at__gte=since):
        bump(m.created_at)
    today = timezone.localdate()
    activity = [[(today - timedelta(days=i)).isoformat(), day_counts.get((today - timedelta(days=i)).isoformat(), 0)] for i in range(89, -1, -1)]

    events = []
    for a in reversed(atts[-15:]):
        events.append({"dt": a.submitted_at, "kind": "quiz", "label": "حل اختبار", "subject": a.quiz.course.teacher.subject,
                       "text": f"{a.quiz.title} — {a.percent}% ({a.score}/{a.total})", "teacher": a.quiz.course.teacher.teacher_code, "pk": a.pk})
    for lp in lp_recent:
        l = lp.lesson
        events.append({"dt": lp.completed_at, "kind": "lesson", "label": "إكمال درس", "subject": l.unit.course.teacher.subject,
                       "text": l.title, "teacher": l.unit.course.teacher.teacher_code, "pk": l.pk})
    for m in msgs_sent.select_related("conversation__teacher").order_by("-created_at")[:15]:
        events.append({"dt": m.created_at, "kind": "msg", "label": "رسالة للمعلم", "subject": m.conversation.teacher.subject,
                       "text": (m.text or "صورة")[:60], "teacher": m.conversation.teacher.teacher_code, "pk": m.pk})
    events.sort(key=lambda x: x["dt"], reverse=True)
    events = events[:15]

    strengths = [("الالتزام بالمواعيد", pct(len(atts), Attempt.objects.filter(student=u).count())),
                 ("حل الاختبارات", avg or 0),
                 ("التفاعل مع المعلم", pct(teachers_msgd, len(enrolls))),
                 ("متابعة المحاضرات", pct(lectures_done, lectures_total)),
                 ("قراءة المحتوى", pct(lessons_done, lessons_total))]

    sp = getattr(u, "student_profile", None)
    grade_label = sp.get_grade_display() if sp else ""
    parent_phone = sp.parent_phone if sp else ""
    avatar_url = sp.avatar.url if (sp and sp.avatar) else static("ui/img/teacher-icon.png")
    return render(request, "student/performance.html", {
        "cards": cards, "atts": list(reversed(atts))[:15], "trend": trend,
        "avg": avg, "best": max(all_pcts) if all_pcts else None, "n_attempts": len(atts),
        "lessons_done": lessons_done, "open_errors": open_errors, "weak": weak_rows,
        # التصميم الجديد
        "days_left": days_left, "days_total": days_total, "sub_badge": sub_badge,
        "lessons_total": lessons_total, "lessons_pct": pct(lessons_done, lessons_total),
        "quizzes_done": quizzes_done, "quizzes_total": quizzes_total, "quizzes_pct": pct(quizzes_done, quizzes_total),
        "lectures_done": lectures_done, "lectures_total": lectures_total, "lectures_pct": pct(lectures_done, lectures_total),
        "pts_earned": pts_earned, "pts_total": pts_total, "gauge_off": round(402.12 * (1 - (avg or 0) / 100), 2),
        "subjects": subjects, "donut": donut, "donut_total": tot,
        "dur_h": mins // 60, "dur_m": mins % 60, "activity": activity, "events": events,
        "recent_msgs": recent_msgs, "msg_code": msg_code, "strengths": strengths,
        "range_from": timezone.localtime(u.date_joined).date(), "range_to": today,
        "avatar_url": avatar_url, "grade_label": grade_label, "parent_phone": parent_phone,
    })


@student_required
def search(request):
    """البحث في الدروس والاختبارات والدورات المتاحة للطالب (المعلمون المشترك معهم + الدورات المجانية)."""
    q = request.GET.get("q", "").strip()
    lessons, quizzes, courses = [], [], []
    if q:
        t_ids = list(Enrollment.objects.filter(student=request.user).values_list("teacher_id", flat=True))
        lqs = svc.visible_lessons_qs().filter(Q(unit__course__teacher_id__in=t_ids) | Q(unit__course__course_type="free"))
        lessons = list(lqs.filter(Q(title__icontains=q) | Q(summary__icontains=q)).select_related("unit__course__teacher__user")[:20])
        qqs = svc.visible_quizzes_qs().filter(Q(course__teacher_id__in=t_ids) | Q(course__course_type="free"))
        quizzes = [x for x in qqs.filter(Q(title__icontains=q) | Q(description__icontains=q)).select_related("course__teacher__user", "lesson")[:20] if x.questions.exists()]
        from learning.models import Course
        courses = [c for c in Course.objects.filter(Q(teacher_id__in=t_ids) | Q(course_type="free")).filter(Q(title__icontains=q) | Q(description__icontains=q)).select_related("teacher__user")[:12]
                   if svc.visible_courses(c.teacher).filter(pk=c.pk).exists()]
    return render(request, "student/search.html", {"q": q, "lessons": lessons, "quizzes": quizzes, "courses": courses})


def _lesson_for(request, code, pk):
    t, enr, active = ws(request, code)
    l = get_object_or_404(svc.visible_lessons_qs(teacher=t), pk=pk)
    return t, enr, active, l


@student_required
def lesson_video(request, code, pk):
    """صفحة فيديو الشرح للدرس. إن لم يوجد فيديو لا تُعرض صفحة وهمية."""
    t, enr, active, l = _lesson_for(request, code, pk)
    if not svc.can_open_lesson(request.user, l):
        return redirect("student:lesson", code=code, pk=pk)
    has_file = bool(l.video_file)
    embed = l.embed_url
    direct = ""
    if not has_file and not embed and l.video_url:
        direct = l.video_url
    if not (has_file or embed or direct):
        messages.info(request, "لم يضف المدرّس فيديو لهذا الدرس بعد.")
        return redirect("student:lesson", code=code, pk=pk)
    svc.touch_lesson(request.user, l)
    is_media = direct.lower().split("?")[0].endswith((".mp4", ".webm", ".ogg", ".mov"))
    return render(request, "student/lesson_video.html", {
        "t": t, "enr": enr, "active": active, "l": l, "has_file": has_file, "embed": embed, "direct": direct, "is_media": is_media,
        "prog": LessonProgress.objects.filter(student=request.user, lesson=l).first(),
    })


def _quiz_rows(request, l, kinds):
    rows = []
    for q in svc.visible_quizzes_qs(lesson=l).filter(kind__in=kinds).select_related("lesson", "unit", "course"):
        n = q.questions.count()
        mine = list(Attempt.objects.filter(student=request.user, quiz=q, submitted_at__isnull=False).order_by("submitted_at"))
        best = max((a.percent for a in mine), default=None)
        exhausted = bool(q.max_attempts and len(mine) >= q.max_attempts)
        closed = q.kind == "homework" and q.is_closed
        last = mine[-1] if mine else None
        last_pct = max(0, min(100, last.percent)) if last else None
        in_progress = Attempt.objects.filter(student=request.user, quiz=q, submitted_at__isnull=True).exists()
        rows.append({"q": q, "n": n, "points": q.total_points, "tries": len(mine), "best": best, "last": last,
                     "exhausted": exhausted, "closed": closed, "startable": bool(n) and not exhausted and not closed,
                     "last_pct": last_pct, "ring_offset": round(201.06 * (1 - last_pct / 100), 2) if last else 201.06,
                     "in_progress": in_progress and not mine})
    return rows


def _lesson_quizzes(request, code, pk, kinds, kind_name, label):
    t, enr, active, l = _lesson_for(request, code, pk)
    if not svc.can_open_lesson(request.user, l):
        return redirect("student:lesson", code=code, pk=pk)
    rows = _quiz_rows(request, l, kinds)
    can_take = svc.has_active_access(request.user, t) or l.course.is_free or l.is_free_preview
    done = sum(1 for r in rows if r["tries"])
    in_progress = sum(1 for r in rows if r["in_progress"])
    stats = {"total": len(rows), "done": done, "in_progress": in_progress, "not_taken": len(rows) - done - in_progress}
    return render(request, "student/lesson_quizzes.html", {
        "t": t, "enr": enr, "active": active, "l": l, "rows": rows, "kind_name": kind_name, "label": label, "can_take": can_take,
        "stats": stats,
    })


@student_required
def lesson_tests(request, code, pk):
    return _lesson_quizzes(request, code, pk, ("quiz", "exam"), "tests", "الاختبارات")


@student_required
def lesson_exercises(request, code, pk):
    return _lesson_quizzes(request, code, pk, ("homework",), "exercises", "التدريبات")
