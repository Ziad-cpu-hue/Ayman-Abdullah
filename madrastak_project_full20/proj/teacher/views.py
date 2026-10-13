from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import teacher_required
from accounts.forms import PasswordChangeForm2
from accounts.models import User
from accounts.pagination import paginate
from accounts.utils import audit, serve_private
from billing.models import Enrollment
from comms.models import Announcement, ChatMessage, Conversation, Ticket
from comms.services import notify, notify_many
from examimport.models import ImportBatch, ImportFile
from examimport.services import run_import_batch
from learning.ai import run_mapping_for_quiz
from learning.models import (Attempt, Attendance, Choice, Course, ErrorRecord, Lesson, LessonFile, LessonProgress,
                             LessonSection, Question, QuestionMapping, Quiz, StudentAnswer, Unit, VocabularyWord)
from learning.services import notify_students_of

from .forms import (AttendanceForm, CourseForm, FileForm, LessonForm, QuestionForm, QuizForm, SectionForm,
                    TeacherProfileForm, UnitForm, VocabForm)


def me(request):
    return request.user.teacher_profile


def owned_course(request, pk):
    return get_object_or_404(Course, pk=pk, teacher=me(request))


def owned_unit(request, pk):
    return get_object_or_404(Unit, pk=pk, course__teacher=me(request))


def owned_lesson(request, pk):
    return get_object_or_404(Lesson, pk=pk, unit__course__teacher=me(request))


def owned_quiz(request, pk):
    return get_object_or_404(Quiz, pk=pk, course__teacher=me(request))


# ------------------------------------------------------------------ الرئيسية
@teacher_required
def home(request):
    t = me(request)
    courses = Course.objects.filter(teacher=t).annotate(n_units=Count("units", distinct=True), n_lessons=Count("units__lessons", distinct=True))
    active_enr = Enrollment.objects.filter(teacher=t, status="active", expires_at__gt=timezone.now())
    stats = {
        "students": Enrollment.objects.filter(teacher=t).values("student").distinct().count(),
        "active_students": active_enr.count(),
        "courses": courses.count(),
        "lessons": Lesson.objects.filter(unit__course__teacher=t).count(),
        "quizzes": Quiz.objects.filter(course__teacher=t, kind__in=("quiz", "exam")).count(),
        "homework": Quiz.objects.filter(course__teacher=t, kind="homework").count(),
    }
    since = timezone.now() - timedelta(days=14)
    recent_students = Enrollment.objects.filter(teacher=t, created_at__gte=since).select_related("student", "student__student_profile").order_by("-created_at")[:6]
    recent_attempts = Attempt.objects.filter(quiz__course__teacher=t, submitted_at__gte=since).select_related("student", "quiz").order_by("-submitted_at")[:6]
    recent_lessons = Lesson.objects.filter(unit__course__teacher=t, status="published", updated_at__gte=since).order_by("-updated_at")[:4]
    unread_msgs = ChatMessage.objects.filter(conversation__teacher=t).exclude(sender__teacher_profile=t).filter(is_read=False).count()
    last_lesson = Lesson.objects.filter(unit__course__teacher=t).order_by("-updated_at").first()
    return render(request, "teacher/home.html", {
        "t": t, "courses": courses[:6], "stats": stats, "recent_students": recent_students, "recent_attempts": recent_attempts,
        "recent_lessons": recent_lessons, "unread_msgs": unread_msgs, "last_lesson": last_lesson,
    })


# ------------------------------------------------------------------ محتوى المنهج: الدورات
@teacher_required
def courses(request):
    t = me(request)
    qs = (Course.objects.filter(teacher=t)
          .annotate(n_units=Count("units", distinct=True), n_lessons=Count("units__lessons", distinct=True)))
    stats = {
        "courses": qs.count(),
        "units": Unit.objects.filter(course__teacher=t).count(),
        "lessons": Lesson.objects.filter(unit__course__teacher=t).count(),
        "students": Enrollment.objects.filter(teacher=t).values("student").distinct().count(),
    }
    return render(request, "teacher/courses.html", {"courses": qs, "stats": stats})


@teacher_required
def course_new(request):
    t = me(request)
    form = CourseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        c.teacher = t
        c.save()
        messages.success(request, "تم إنشاء الدورة. أضف الوحدات الآن.")
        return redirect("teacher:course_detail", pk=c.pk)
    return render(request, "teacher/course_form.html", {"form": form, "is_new": True})


@teacher_required
def course_detail(request, pk):
    c = owned_course(request, pk)
    units = c.units.annotate(n_lessons=Count("lessons"))
    unit_quizzes = c.quizzes.filter(unit__isnull=True, lesson__isnull=True)
    n_students = Enrollment.objects.filter(teacher=c.teacher, status="active", expires_at__gt=timezone.now()).count() if not c.is_free else "—"
    total_lessons = Lesson.objects.filter(unit__course=c).count()
    total_quizzes = Quiz.objects.filter(course=c, kind__in=("quiz", "exam")).count()
    total_hw = Quiz.objects.filter(course=c, kind="homework").count()
    avg_completion = None
    lp = LessonProgress.objects.filter(lesson__unit__course=c, completed=True).count()
    return render(request, "teacher/course_detail.html", {"c": c, "units": units, "unit_quizzes": unit_quizzes, "n_students": n_students,
                                                          "total_lessons": total_lessons, "total_quizzes": total_quizzes, "total_hw": total_hw, "lp": lp})


@teacher_required
def course_edit(request, pk):
    c = owned_course(request, pk)
    form = CourseForm(request.POST or None, request.FILES or None, instance=c)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ تعديلات الدورة.")
        return redirect("teacher:course_detail", pk=c.pk)
    return render(request, "teacher/course_form.html", {"form": form, "is_new": False, "c": c})


@teacher_required
@require_POST
def course_delete(request, pk):
    c = owned_course(request, pk)
    c.delete()
    messages.success(request, "تم حذف الدورة.")
    return redirect("teacher:courses")


@teacher_required
@require_POST
def course_publish(request, pk):
    c = owned_course(request, pk)
    was_draft = c.status != "published"
    c.status = "published"
    c.publish_at = None
    c.save()
    if was_draft:
        notify_students_of(c.teacher, f"دورة جديدة: {c.title}", "أضاف مدرّسك دورة جديدة.", f"/student/t/{c.teacher.teacher_code}/course/{c.pk}/")
    messages.success(request, "تم نشر الدورة.")
    return redirect("teacher:course_detail", pk=c.pk)


# ------------------------------------------------------------------ الوحدات
@teacher_required
def unit_new(request, pk):
    c = owned_course(request, pk)
    form = UnitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        u = form.save(commit=False)
        u.course = c
        if not u.order:
            u.order = (c.units.count() + 1)
        u.save()
        messages.success(request, "تمت إضافة الوحدة. أضف الدروس الآن.")
        return redirect("teacher:unit_detail", pk=u.pk)
    return render(request, "teacher/unit_form.html", {"form": form, "c": c, "is_new": True})


@teacher_required
def unit_detail(request, pk):
    u = owned_unit(request, pk)
    lessons = u.lessons.all()
    quizzes = u.quizzes.filter(lesson__isnull=True)
    return render(request, "teacher/unit_detail.html", {"u": u, "lessons": lessons, "quizzes": quizzes})


@teacher_required
def unit_edit(request, pk):
    u = owned_unit(request, pk)
    form = UnitForm(request.POST or None, instance=u)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم الحفظ.")
        return redirect("teacher:unit_detail", pk=u.pk)
    return render(request, "teacher/unit_form.html", {"form": form, "c": u.course, "is_new": False, "u": u})


@teacher_required
@require_POST
def unit_delete(request, pk):
    u = owned_unit(request, pk)
    c = u.course
    u.delete()
    messages.success(request, "تم حذف الوحدة.")
    return redirect("teacher:course_detail", pk=c.pk)


@teacher_required
@require_POST
def unit_publish(request, pk):
    u = owned_unit(request, pk)
    u.status = "published"
    u.publish_at = None
    u.save()
    notify_students_of(u.course.teacher, f"وحدة جديدة: {u.title}", u.course.title, f"/student/t/{u.course.teacher.teacher_code}/unit/{u.pk}/")
    messages.success(request, "تم نشر الوحدة.")
    return redirect("teacher:unit_detail", pk=u.pk)


# ------------------------------------------------------------------ الدروس
@teacher_required
def lesson_new(request, pk):
    u = owned_unit(request, pk)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    form = LessonForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        l = form.save(commit=False)
        l.unit = u
        if not l.order:
            l.order = (u.lessons.count() + 1)
        # لم يعد هناك خطوة نشر منفصلة: الدرس يُنشر فور إنشائه من نافذة الإضافة السريعة.
        l.status = "published"
        l.publish_at = timezone.now()
        l.save()
        notify_students_of(l.teacher, f"درس جديد: {l.title}", l.unit.title, f"/student/t/{l.teacher.teacher_code}/lesson/{l.pk}/")
        if is_ajax:
            return JsonResponse({"ok": True, "id": l.pk, "title": l.title})
        messages.success(request, "تمت إضافة الدرس ونشره تلقائيًا. أضف الشرح والمكوّنات الآن.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    if request.method == "POST" and is_ajax:
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)
    return render(request, "teacher/lesson_form.html", {"form": form, "u": u, "is_new": True})


@teacher_required
def lesson_detail(request, pk):
    l = owned_lesson(request, pk)
    sections = list(l.sections.all())
    for s in sections:
        words = len((s.content or "").split())
        s.word_count = words
        s.read_minutes = max(1, round(words / 180))
    files = list(l.files.all())
    vocab = list(l.vocabulary.all())
    quizzes = list(l.quizzes.all())
    total_words = sum(s.word_count for s in sections)
    has_video = bool(l.video_file or l.video_url)
    total_items = len(sections) + len(files) + len(vocab) + len(quizzes) + (1 if has_video else 0)
    unit_lessons = list(l.unit.lessons.all())
    try:
        idx = [x.pk for x in unit_lessons].index(l.pk)
        next_lesson = unit_lessons[idx + 1] if idx + 1 < len(unit_lessons) else None
    except ValueError:
        next_lesson = None
    return render(request, "teacher/lesson_detail.html", {
        "l": l, "sections": sections, "files": files, "vocab": vocab, "quizzes": quizzes,
        "completeness": l.completeness(), "total_words": total_words, "has_video": has_video,
        "total_items": total_items, "next_lesson": next_lesson,
    })


@teacher_required
def lesson_edit(request, pk):
    l = owned_lesson(request, pk)
    form = LessonForm(request.POST or None, request.FILES or None, instance=l)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم الحفظ.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    return render(request, "teacher/lesson_form.html", {"form": form, "u": l.unit, "is_new": False, "l": l})


@teacher_required
@require_POST
def video_delete(request, pk):
    """يحذف فيديو الدرس (الرابط أو الملف المرفوع) فقط، دون حذف بقية عناصر الدرس."""
    l = owned_lesson(request, pk)
    if l.video_file:
        l.video_file.delete(save=False)
    l.video_file = ""
    l.video_url = ""
    l.save(update_fields=["video_file", "video_url"])
    messages.success(request, "تم حذف فيديو الدرس.")
    return redirect("teacher:lesson_detail", pk=l.pk)


@teacher_required
@require_POST
def lesson_delete(request, pk):
    l = owned_lesson(request, pk)
    u = l.unit
    l.delete()
    messages.success(request, "تم حذف الدرس.")
    return redirect("teacher:unit_detail", pk=u.pk)


@teacher_required
@require_POST
def lesson_publish(request, pk):
    l = owned_lesson(request, pk)
    if not l.sections.exists():
        messages.error(request, "أضف الشرح النصي قبل نشر الدرس.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    l.status = "published"
    l.publish_at = None
    l.save()
    notify_students_of(l.teacher, f"درس جديد: {l.title}", l.unit.title, f"/student/t/{l.teacher.teacher_code}/lesson/{l.pk}/")
    messages.success(request, "تم نشر الدرس، وسيصل إشعار للطلاب المشتركين.")
    return redirect("teacher:lesson_detail", pk=l.pk)


@teacher_required
def lesson_preview(request, pk):
    l = owned_lesson(request, pk)
    quizzes = l.quizzes.all()
    qinfo = [{"q": q, "n": q.questions.count(), "last": None} for q in quizzes]
    return render(request, "student/lesson.html", {"t": l.teacher, "enr": None, "active": True, "l": l, "sections": l.sections.all(),
                                                   "files": l.files.all(), "vocab_count": l.vocabulary.count(), "prev_l": None, "next_l": None,
                                                   "prog": None, "qinfo": qinfo, "position": 1, "total": 1, "preview": True})


@teacher_required
def preview_video(request, pk):
    l = owned_lesson(request, pk)
    return serve_private(request, l.video_file)


@teacher_required
def preview_file(request, pk):
    f = get_object_or_404(LessonFile, pk=pk, lesson__unit__course__teacher=me(request))
    return serve_private(request, f.file, download_name=f.file.name.split("/")[-1])


# ------------------------------------------------------------------ الأقسام
@teacher_required
def section_new(request, pk):
    l = owned_lesson(request, pk)
    form = SectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        s = form.save(commit=False)
        s.lesson = l
        if not s.order:
            s.order = l.sections.count() + 1
        s.save()
        messages.success(request, "تمت إضافة القسم.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    return render(request, "teacher/section_form.html", {"form": form, "l": l, "is_new": True})


@teacher_required
def section_edit(request, pk):
    s = get_object_or_404(LessonSection, pk=pk, lesson__unit__course__teacher=me(request))
    form = SectionForm(request.POST or None, instance=s)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم الحفظ.")
        return redirect("teacher:lesson_detail", pk=s.lesson.pk)
    return render(request, "teacher/section_form.html", {"form": form, "l": s.lesson, "is_new": False, "s": s})


@teacher_required
@require_POST
def section_delete(request, pk):
    s = get_object_or_404(LessonSection, pk=pk, lesson__unit__course__teacher=me(request))
    l = s.lesson
    s.delete()
    messages.success(request, "تم الحذف.")
    return redirect("teacher:lesson_detail", pk=l.pk)


# ------------------------------------------------------------------ الملفات والمفردات
@teacher_required
def file_new(request, pk):
    l = owned_lesson(request, pk)
    form = FileForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        f = form.save(commit=False)
        f.lesson = l
        f.save()
        messages.success(request, "تمت إضافة الملف.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    return render(request, "teacher/file_form.html", {"form": form, "l": l, "is_new": True})


@teacher_required
def file_edit(request, pk):
    f = get_object_or_404(LessonFile, pk=pk, lesson__unit__course__teacher=me(request))
    l = f.lesson
    form = FileForm(request.POST or None, request.FILES or None, instance=f)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ تعديلات الملف.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    return render(request, "teacher/file_form.html", {"form": form, "l": l, "f": f, "is_new": False})


@teacher_required
@require_POST
def file_delete(request, pk):
    f = get_object_or_404(LessonFile, pk=pk, lesson__unit__course__teacher=me(request))
    l = f.lesson
    f.delete()
    messages.success(request, "تم حذف الملف.")
    return redirect("teacher:lesson_detail", pk=l.pk)


@teacher_required
def vocab_new(request, pk):
    l = owned_lesson(request, pk)
    form = VocabForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        w = form.save(commit=False)
        w.lesson = l
        w.save()
        messages.success(request, "تمت إضافة الكلمة.")
        if "add_another" in request.POST:
            return redirect("teacher:vocab_new", pk=l.pk)
        return redirect("teacher:lesson_detail", pk=l.pk)
    return render(request, "teacher/vocab_form.html", {"form": form, "l": l, "is_new": True})


@teacher_required
def vocab_edit(request, pk):
    w = get_object_or_404(VocabularyWord, pk=pk, lesson__unit__course__teacher=me(request))
    l = w.lesson
    form = VocabForm(request.POST or None, instance=w)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ تعديلات الكلمة.")
        return redirect("teacher:lesson_detail", pk=l.pk)
    return render(request, "teacher/vocab_form.html", {"form": form, "l": l, "w": w, "is_new": False})


@teacher_required
@require_POST
def vocab_delete(request, pk):
    w = get_object_or_404(VocabularyWord, pk=pk, lesson__unit__course__teacher=me(request))
    l = w.lesson
    w.delete()
    messages.success(request, "تم الحذف.")
    return redirect("teacher:lesson_detail", pk=l.pk)


@teacher_required
@require_POST
def vocab_delete_all(request, pk):
    """يحذف كل مفردات الدرس دفعة واحدة (للتخلّص من قائمة كاملة مستوردة بالخطأ)."""
    l = owned_lesson(request, pk)
    n = l.vocabulary.count()
    if n:
        with transaction.atomic():
            l.vocabulary.all().delete()
        messages.success(request, f"تم حذف كل مفردات الدرس ({n} كلمة).")
    else:
        messages.info(request, "لا توجد مفردات في هذا الدرس لحذفها.")
    return redirect("teacher:lesson_detail", pk=l.pk)


# ------------------------------------------------------------------ الاختبارات والأسئلة
@teacher_required
def quiz_new_lesson(request, pk):
    l = owned_lesson(request, pk)
    form = QuizForm(request.POST or None, initial={"kind": "quiz"})
    if request.method == "POST" and form.is_valid():
        q = form.save(commit=False)
        q.course, q.lesson = l.unit.course, l
        q.save()
        messages.success(request, "تم إنشاء الاختبار. أضف الأسئلة الآن يدويًا أو من ملف PDF — كلها تُجمَّع في هذا الاختبار.")
        return redirect(reverse("teacher:quiz_edit", args=[q.pk]) + "#questions")
    return render(request, "teacher/quiz_form.html", {"form": form, "l": l, "is_new": True})


@teacher_required
def quiz_new_unit(request, pk):
    u = owned_unit(request, pk)
    form = QuizForm(request.POST or None, initial={"kind": "exam"})
    if request.method == "POST" and form.is_valid():
        q = form.save(commit=False)
        q.course, q.unit = u.course, u
        q.save()
        messages.success(request, "تم إنشاء الاختبار. أضف الأسئلة الآن يدويًا أو من ملف PDF — كلها تُجمَّع في هذا الاختبار.")
        return redirect(reverse("teacher:quiz_edit", args=[q.pk]) + "#questions")
    return render(request, "teacher/quiz_form.html", {"form": form, "u": u, "is_new": True})


@teacher_required
def quiz_new_course(request, pk):
    c = owned_course(request, pk)
    form = QuizForm(request.POST or None, initial={"kind": "exam"})
    if request.method == "POST" and form.is_valid():
        q = form.save(commit=False)
        q.course = c
        q.save()
        messages.success(request, "تم إنشاء الاختبار. أضف الأسئلة الآن يدويًا أو من ملف PDF — كلها تُجمَّع في هذا الاختبار.")
        return redirect(reverse("teacher:quiz_edit", args=[q.pk]) + "#questions")
    return render(request, "teacher/quiz_form.html", {"form": form, "c": c, "is_new": True})


@teacher_required
def quiz_detail(request, pk):
    q = owned_quiz(request, pk)
    questions = q.questions.prefetch_related("choices")
    return render(request, "teacher/quiz_detail.html", {"q": q, "questions": questions,
                                                        "questions_data": [_question_json(x) for x in questions]})


@teacher_required
def quiz_edit(request, pk):
    q = owned_quiz(request, pk)
    was_published = q.status == "published"
    form = QuizForm(request.POST or None, instance=q)
    if request.method == "POST" and form.is_valid():
        form.save()
        # «حفظ وإنهاء» = نشر فوري للطلاب (بدون خطوة نشر منفصلة)، بشرط وجود سؤال واحد على الأقل.
        if q.status == "archived":
            messages.success(request, "تم حفظ الاختبار.")
        elif not q.questions.exists():
            messages.warning(request, "تم حفظ الاختبار كمسودة لأنه لا يحتوي أسئلة بعد. أضف سؤالًا واحدًا على الأقل ثم اضغط «حفظ وإنهاء» لينشر.")
        elif was_published:
            messages.success(request, "تم حفظ الاختبار.")
        else:
            q.status = "published"
            q.publish_at = None
            q.save(update_fields=["status", "publish_at"])
            label = "واجب" if q.kind == "homework" else "اختبار"
            notify_students_of(q.teacher, f"{label} جديد: {q.title}", q.location, f"/student/t/{q.teacher.teacher_code}/", "quiz")
            messages.success(request, f"تم حفظ ال{label} ونشره للطلاب.")
        return redirect(reverse("teacher:quiz_detail", args=[q.pk]) + "#questions")
    return render(request, "teacher/quiz_form.html", {
        "form": form, "l": q.lesson, "u": q.unit, "c": q.course, "is_new": False, "q": q,
        "questions_data": [_question_json(x) for x in q.questions.prefetch_related("choices")],
    })


@teacher_required
@require_POST
def quiz_delete(request, pk):
    q = owned_quiz(request, pk)
    back = ("teacher:lesson_detail", q.lesson_id) if q.lesson_id else (("teacher:unit_detail", q.unit_id) if q.unit_id else ("teacher:course_detail", q.course_id))
    q.delete()
    messages.success(request, "تم حذف الاختبار.")
    return redirect(back[0], pk=back[1])


@teacher_required
@require_POST
def quiz_publish(request, pk):
    q = owned_quiz(request, pk)
    if not q.questions.exists():
        messages.error(request, "أضف سؤالًا واحدًا على الأقل قبل النشر.")
        return redirect("teacher:quiz_detail", pk=q.pk)
    q.status = "published"
    q.publish_at = None
    q.save()
    label = "واجب" if q.kind == "homework" else "اختبار"
    notify_students_of(q.teacher, f"{label} جديد: {q.title}", q.location, f"/student/t/{q.teacher.teacher_code}/", "quiz")
    messages.success(request, f"تم نشر ال{label}.")
    return redirect("teacher:quiz_detail", pk=q.pk)


@teacher_required
def quiz_preview(request, pk):
    q = owned_quiz(request, pk)
    questions = list(q.questions.prefetch_related("choices"))
    return render(request, "student/quiz.html", {"t": q.teacher, "q": q, "questions": questions, "seconds": 0,
                                                 "total": sum(x.points for x in questions), "attempt_no": 1, "preview": True})


@teacher_required
def question_new(request, pk):
    q = owned_quiz(request, pk)
    lesson_obj = q.lesson or (q.unit.lessons.first() if q.unit_id else None)
    form = QuestionForm(request.POST or None, request.FILES or None, lesson=lesson_obj)
    if request.method == "POST" and form.is_valid():
        form.save(q)
        messages.success(request, "تمت إضافة السؤال.")
        if "add_another" in request.POST:
            return redirect("teacher:question_new", pk=q.pk)
        return redirect("teacher:quiz_detail", pk=q.pk)
    return render(request, "teacher/question_form.html", {"form": form, "q": q})


@teacher_required
@require_POST
def question_delete(request, pk):
    qq = get_object_or_404(Question, pk=pk, quiz__course__teacher=me(request))
    quiz = qq.quiz
    qq.delete()
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "count": quiz.questions.count()})
    messages.success(request, "تم حذف السؤال.")
    return redirect("teacher:quiz_detail", pk=quiz.pk)


# ------------------------------------------------ إضافة الأسئلة داخل صفحة تعديل الاختبار
# كل المسارات التالية تكتب في نفس الاختبار (Quiz) مباشرةً: سواء أضاف المدرّس السؤال يدويًا
# (اختيار من متعدد / مقالي) أو استورده من PDF، يصير كله أسئلة حقيقية في اختبار واحد متّصل.
_TF_WORDS = {"true", "false", "صح", "خطأ", "صحيح", "خاطئ"}


def _next_order(quiz):
    last = quiz.questions.order_by("-order", "-id").first()
    return (last.order + 1) if last else 1


def _question_json(qq):
    return {
        "id": qq.pk, "text": qq.text, "qtype": qq.qtype, "points": qq.points,
        "choices": [{"text": c.text, "is_correct": c.is_correct} for c in qq.choices.all()],
    }


def _clean_choices(raw_options, correct):
    """يُنقّي الاختيارات ويتحقق منها. يعيد (قائمة [(نص, صحيح?)], رسالة خطأ)."""
    opts = [str(o or "").strip() for o in (raw_options or [])][:4]
    filled = [(i, o) for i, o in enumerate(opts) if o]
    if len(filled) < 2:
        return None, "أضف اختيارين على الأقل."
    try:
        correct = int(correct)
    except (TypeError, ValueError):
        correct = -1
    if correct not in [i for i, _ in filled]:
        return None, "حدّد الإجابة الصحيحة من الاختيارات المكتوبة."
    return [(o, i == correct) for i, o in filled], ""


def _create_question(quiz, text, qtype, choices, order, points=1):
    if qtype != "essay" and len(choices) == 2 and {c[0].strip().lower() for c in choices} <= _TF_WORDS:
        qtype = "tf"
    qq = Question.objects.create(quiz=quiz, text=text, qtype=qtype, points=points, order=order)
    if qtype != "essay":
        for i, (ctext, ok) in enumerate(choices, start=1):
            Choice.objects.create(question=qq, text=ctext[:400], is_correct=ok, order=i)
    return qq


@teacher_required
@require_POST
def quiz_question_add(request, pk):
    """إضافة سؤال يدوي (اختيار من متعدد أو مقالي) إلى نفس الاختبار."""
    quiz = owned_quiz(request, pk)
    text = (request.POST.get("text") or "").strip()
    qtype = request.POST.get("qtype", "mcq")
    if qtype not in ("mcq", "essay"):
        return JsonResponse({"ok": False, "error": "نوع السؤال غير صالح."}, status=400)
    if not text:
        return JsonResponse({"ok": False, "error": "اكتب نص السؤال."}, status=400)
    try:
        points = max(1, int(request.POST.get("points") or 1))
    except ValueError:
        points = 1
    choices = []
    if qtype == "mcq":
        choices, err = _clean_choices(request.POST.getlist("options"), request.POST.get("correct"))
        if err:
            return JsonResponse({"ok": False, "error": err}, status=400)
    with transaction.atomic():
        qq = _create_question(quiz, text, qtype, choices, _next_order(quiz), points)
    return JsonResponse({"ok": True, "question": _question_json(qq), "count": quiz.questions.count()})


@teacher_required
@require_POST
def question_update(request, pk):
    """تعديل سؤال موجود (نص، درجة، اختيارات، إجابة صحيحة). تُحدَّث الاختيارات في مكانها."""
    qq = get_object_or_404(Question, pk=pk, quiz__course__teacher=me(request))
    text = (request.POST.get("text") or "").strip()
    if not text:
        return JsonResponse({"ok": False, "error": "اكتب نص السؤال."}, status=400)
    try:
        points = max(1, int(request.POST.get("points") or qq.points))
    except ValueError:
        points = qq.points
    new_choices = []
    if qq.qtype != "essay":
        new_choices, err = _clean_choices(request.POST.getlist("options"), request.POST.get("correct"))
        if err:
            return JsonResponse({"ok": False, "error": err}, status=400)
    with transaction.atomic():
        qq.text, qq.points = text, points
        if qq.qtype != "essay":
            qq.qtype = "tf" if len(new_choices) == 2 and {c[0].strip().lower() for c in new_choices} <= _TF_WORDS else "mcq"
        qq.save()
        if qq.qtype != "essay":
            old = list(qq.choices.all())
            for i, (ctext, ok) in enumerate(new_choices, start=1):
                if i <= len(old):
                    c = old[i - 1]
                    c.text, c.is_correct, c.order = ctext[:400], ok, i
                    c.save()
                else:
                    Choice.objects.create(question=qq, text=ctext[:400], is_correct=ok, order=i)
            for c in old[len(new_choices):]:
                c.delete()
    qq = Question.objects.prefetch_related("choices").get(pk=qq.pk)
    return JsonResponse({"ok": True, "question": _question_json(qq)})


@teacher_required
@require_POST
def quiz_pdf_parse(request, pk):
    """يرفع PDF ويحلّله فورًا ويعيد الأسئلة المكتشفة (بدون إضافتها) لتُعرض وتُراجَع في نفس الصفحة."""
    from django.conf import settings
    quiz = owned_quiz(request, pk)
    f = request.FILES.get("pdf_file")
    if not f:
        return JsonResponse({"ok": False, "error": "اختر ملف PDF أولًا."}, status=400)
    if not f.name.lower().endswith(".pdf"):
        return JsonResponse({"ok": False, "error": "الملف يجب أن يكون بصيغة PDF."}, status=400)
    max_mb = getattr(settings, "MAX_IMPORT_FILE_MB", 15)
    if f.size > max_mb * 1024 * 1024:
        return JsonResponse({"ok": False, "error": f"حجم الملف أكبر من {max_mb} ميجابايت."}, status=400)
    batch = ImportBatch.objects.create(teacher=me(request), quiz=quiz)
    ImportFile.objects.create(batch=batch, file=f, order=0)
    run_import_batch(batch)
    if batch.status == "failed":
        return JsonResponse({"ok": False, "error": batch.error_message or "تعذّرت معالجة ملف PDF."}, status=422)
    items = []
    for iq in batch.questions.prefetch_related("choices"):
        ch = list(iq.choices.all())
        items.append({
            "id": iq.pk, "text": iq.text, "qtype": iq.qtype, "status": iq.status, "confidence": iq.confidence,
            "notes": iq.notes, "page": iq.page_number,
            "choices": [c.text for c in ch][:4],
            "correct": iq.correct_index if iq.correct_index and iq.correct_index <= len(ch) else None,
        })
    return JsonResponse({"ok": True, "batch": batch.pk, "filename": f.name, "questions": items})


@teacher_required
@require_POST
def quiz_pdf_apply(request, pk):
    """يضيف الأسئلة التي اعتمدها المدرّس من نتيجة تحليل الـ PDF إلى نفس الاختبار (دون نشره تلقائيًا)."""
    import json
    quiz = owned_quiz(request, pk)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        batch = get_object_or_404(ImportBatch, pk=int(data.get("batch")), teacher=me(request), quiz=quiz)
        items = data.get("questions") or []
        assert isinstance(items, list)
    except Http404:
        raise
    except Exception:
        return JsonResponse({"ok": False, "error": "تعذّر قراءة الأسئلة المرسلة."}, status=400)
    if batch.status == "applied":
        return JsonResponse({"ok": False, "error": "سبق إضافة أسئلة هذا الملف إلى الاختبار."}, status=409)
    if not items:
        return JsonResponse({"ok": False, "error": "حدّد سؤالًا واحدًا على الأقل لإضافته."}, status=400)
    prepared = []
    for n, it in enumerate(items, start=1):
        text = str((it or {}).get("text", "")).strip()
        if not text:
            return JsonResponse({"ok": False, "error": f"السؤال {n}: اكتب نص السؤال."}, status=400)
        try:
            correct = int((it or {}).get("correct") or 0) - 1
        except (TypeError, ValueError):
            correct = -1
        choices, err = _clean_choices((it or {}).get("choices"), correct)
        if err:
            return JsonResponse({"ok": False, "error": f"السؤال {n}: {err}"}, status=400)
        prepared.append((text, choices))
    created = []
    with transaction.atomic():
        order = _next_order(quiz)
        for text, choices in prepared:
            created.append(_create_question(quiz, text, "mcq", choices, order))
            order += 1
        batch.status = "applied"
        batch.applied_at = timezone.now()
        batch.save(update_fields=["status", "applied_at"])
    return JsonResponse({"ok": True, "questions": [_question_json(x) for x in created], "count": quiz.questions.count()})


# ------------------------------------------------------------- الربط الذكي بالشرح
@teacher_required
@require_POST
def quiz_run_mapping(request, pk):
    """يشغّل نظام الربط الذكي (System Two): يحلّل كل أسئلة الاختبار مقابل أقسام
    شرح دروسه، ويحفظ اقتراحات جاهزة لمراجعة المدرّس واعتمادها."""
    q = owned_quiz(request, pk)
    if not q.questions.exists():
        messages.error(request, "أضف أسئلة للاختبار أولًا قبل تشغيل الربط الذكي.")
        return redirect("teacher:quiz_detail", pk=q.pk)
    run = run_mapping_for_quiz(q)
    if run.status == "done":
        if run.matched_count:
            messages.success(request, f"تم اقتراح ربط {run.matched_count} سؤالًا بأقسام الشرح. راجع الاقتراحات واعتمدها.")
        else:
            messages.warning(request, "لم يُعثر على أقسام شرح مرتبطة بدروس هذا الاختبار لتنفيذ الربط.")
    else:
        messages.error(request, run.error_message or "تعذّر تنفيذ الربط الذكي.")
    return redirect("teacher:quiz_mapping", pk=q.pk)


@teacher_required
def quiz_mapping(request, pk):
    """شاشة مراجعة اقتراحات الربط الذكي بين أسئلة الاختبار وأقسام شرح الدرس."""
    q = owned_quiz(request, pk)
    mappings = (QuestionMapping.objects.filter(question__quiz=q)
                .select_related("question", "section__lesson")
                .prefetch_related("secondary_sections"))
    mapped_ids = set(mappings.values_list("question_id", flat=True))
    unmapped = q.questions.exclude(id__in=mapped_ids)

    lessons = set()
    if q.lesson_id:
        lessons.add(q.lesson)
    elif q.unit_id:
        lessons.update(q.unit.lessons.all())
    else:
        for unit in q.course.units.all():
            lessons.update(unit.lessons.all())
    all_sections = LessonSection.objects.filter(lesson__in=lessons).select_related("lesson")

    return render(request, "teacher/quiz_mapping.html", {
        "q": q, "mappings": mappings, "unmapped": unmapped, "all_sections": all_sections,
        "last_run": q.mapping_runs.first(),
    })


@teacher_required
@require_POST
def mapping_confirm(request, pk):
    """اعتماد اقتراح الربط (أو تغيير القسم قبل الاعتماد)، وكتابة الاعتماد النهائي
    في Question.section حتى تعمل به كل شاشات الطالب دون أي تعديل إضافي."""
    m = get_object_or_404(QuestionMapping, pk=pk, question__quiz__course__teacher=me(request))
    section_id = request.POST.get("section")
    if section_id and section_id.isdigit():
        sec = LessonSection.objects.filter(pk=int(section_id)).first()
        if sec:
            m.section = sec
    m.status = "confirmed"
    m.save()
    m.question.section = m.section
    m.question.save(update_fields=["section"])
    messages.success(request, "تم اعتماد الربط.")
    return redirect("teacher:quiz_mapping", pk=m.question.quiz_id)


@teacher_required
@require_POST
def mapping_reject(request, pk):
    m = get_object_or_404(QuestionMapping, pk=pk, question__quiz__course__teacher=me(request))
    m.status = "rejected"
    m.save(update_fields=["status"])
    if m.question.section_id == m.section_id:
        m.question.section = None
        m.question.save(update_fields=["section"])
    messages.info(request, "تم تجاهل هذا الاقتراح.")
    return redirect("teacher:quiz_mapping", pk=m.question.quiz_id)


# ------------------------------------------------------------------ الطلاب
@teacher_required
def students(request):
    t = me(request)
    qs = Enrollment.objects.filter(teacher=t).select_related("student", "student__student_profile")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(student__full_name__icontains=q) | Q(student__phone__icontains=q))
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status) if status != "active" else qs.filter(status="active", expires_at__gt=timezone.now())
    qs = qs.order_by("-created_at")
    if request.GET.get("export") == "csv":
        import csv
        from django.http import HttpResponse
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="students.csv"'
        resp.write("\ufeff")  # BOM ليفتح Excel النص العربي بشكل صحيح
        w = csv.writer(resp)
        w.writerow(["الاسم", "الهاتف", "الصف", "الحالة", "تاريخ الاشتراك"])
        for e in qs:
            sp = getattr(e.student, "student_profile", None)
            w.writerow([e.student.full_name, e.student.phone, sp.get_grade_display() if sp else "", e.display_label, e.created_at.strftime("%Y-%m-%d")])
        return resp
    page, qstr = paginate(request, qs, 25)
    return render(request, "teacher/students.html", {"page": page, "qs": qstr, "q": q, "status": status})


@teacher_required
def student_detail(request, pk):
    import math

    t = me(request)
    enr = get_object_or_404(Enrollment.objects.select_related("student__student_profile"), pk=pk, teacher=t)
    s = enr.student
    from learning.services import course_progress, teacher_progress, teacher_stats_for_student
    pr = teacher_progress(s, t)
    stats = teacher_stats_for_student(s, t)
    atts = Attempt.objects.filter(student=s, quiz__course__teacher=t, submitted_at__isnull=False).select_related("quiz", "quiz__course").order_by("-submitted_at")[:15]
    errors = ErrorRecord.objects.filter(student=s, question__quiz__course__teacher=t, resolved=False)[:10]

    # ---- أيام الاشتراك المتبقية
    now = timezone.now()
    remaining_days = max(0, (enr.expires_at - now).days) if enr.is_active and enr.expires_at else 0
    plan_start = enr.started_at or enr.created_at
    plan_days = max((enr.expires_at - plan_start).days, 1) if enr.expires_at else None

    # ---- الأداء لكل دورة من دورات المدرّس (بديل واقعي عن "الأداء حسب المادة" لأن كل مدرّس هنا يدرّس مادة واحدة فقط)
    palette = ["#3F8BF9", "#36CE8F", "#8299DF", "#FBB459", "#E87881", "#4AC7C2"]
    courses_perf = []
    for i, c in enumerate(Course.objects.filter(teacher=t, status="published").order_by("order", "-created_at")[:6]):
        cp = course_progress(s, c)
        c_atts = [a.percent for a in Attempt.objects.filter(student=s, quiz__course=c, submitted_at__isnull=False)]
        avg = round(sum(c_atts) / len(c_atts)) if c_atts else cp["percent"]
        courses_perf.append({"title": c.title, "percent": avg, "color": palette[i % len(palette)]})

    # ---- محادثة الطالب مع المدرّس
    conv = Conversation.objects.filter(teacher=t, student=s).first()
    conv_messages = conv.messages.select_related("sender").order_by("-created_at")[:5] if conv else []

    # ---- توزيع الأنشطة (بيانات حقيقية: دروس مكتملة / اختبارات محلولة / رسائل للمدرّس)
    msgs_from_student = conv.messages.filter(sender=s).count() if conv else 0
    donut_raw = [
        {"label": "دروس مكتملة", "count": pr["done"], "color": "#3F8BF9"},
        {"label": "اختبارات محلولة", "count": stats["attempts"], "color": "#36CE8F"},
        {"label": "رسائل للمعلم", "count": msgs_from_student, "color": "#E87881"},
    ]
    donut_total = sum(d["count"] for d in donut_raw) or 0
    circumference = 2 * math.pi * 64
    offset = 0.0
    donut = []
    for d in donut_raw:
        frac = (d["count"] / donut_total) if donut_total else 0
        seg_len = frac * circumference
        donut.append({**d, "percent": round(frac * 100), "dasharray": f"{seg_len:.2f} {circumference:.2f}", "dashoffset": f"{-offset:.2f}"})
        offset += seg_len

    # ---- اتجاه النشاط خلال آخر 7 أيام (عدد محاولات الاختبارات يوميًا)
    days = [(now - timedelta(days=i)).date() for i in range(6, -1, -1)]
    daily_counts = []
    for d in days:
        daily_counts.append(Attempt.objects.filter(student=s, quiz__course__teacher=t, submitted_at__date=d).count())
    max_count = max(daily_counts) or 1
    w, h, pad = 350, 140, 15
    step = w / (len(days) - 1) if len(days) > 1 else 0
    points = []
    for i, cnt in enumerate(daily_counts):
        x = round(i * step, 1)
        y = round((h - pad) - (cnt / max_count) * (h - pad - 20), 1)
        points.append({"x": x, "y": y, "label": days[i].strftime("%m/%d")})
    line_path = "M " + " L ".join(f"{p['x']} {p['y']}" for p in points) if points else ""
    area_path = (line_path + f" L {points[-1]['x']} {h} L {points[0]['x']} {h} Z") if points else ""

    # ---- نسبة الحضور
    att_qs = Attendance.objects.filter(teacher=t, student=s)
    att_total = att_qs.count()
    attendance_rate = round(att_qs.filter(present=True).count() * 100 / att_total) if att_total else None

    resolved_ratio = round((stats["errors_total"] - stats["errors_open"]) * 100 / stats["errors_total"]) if stats["errors_total"] else 100
    strengths = [{"label": "إتمام الدروس", "percent": pr["percent"]},
                 {"label": "متوسط الدرجات", "percent": stats["avg_percent"]},
                 {"label": "تصحيح الأخطاء", "percent": resolved_ratio}]
    if attendance_rate is not None:
        strengths.insert(1, {"label": "الالتزام بالحضور", "percent": attendance_rate})

    total_quizzes = Quiz.objects.filter(course__teacher=t, status="published", kind__in=("quiz", "exam")).count()

    gauge_circumference = round(circumference, 2)
    gauge_dashoffset = round(circumference * (1 - pr["percent"] / 100), 2)

    return render(request, "teacher/student_detail.html", {
        "enr": enr, "s": s, "pr": pr, "stats": stats, "atts": atts, "errors": errors, "payments": enr.payments.all(),
        "remaining_days": remaining_days, "plan_days": plan_days, "courses_perf": courses_perf,
        "conv": conv, "conv_messages": conv_messages, "donut": donut, "donut_total": donut_total,
        "chart_points": points, "chart_line_path": line_path, "chart_area_path": area_path,
        "attendance_rate": attendance_rate, "strengths": strengths, "total_quizzes": total_quizzes,
        "gauge_circumference": gauge_circumference, "gauge_dashoffset": gauge_dashoffset,
    })


# ------------------------------------------------------------------ الاشتراكات
@teacher_required
def subscriptions(request):
    t = me(request)
    now = timezone.now()
    all_e = Enrollment.objects.filter(teacher=t)
    stats = {"total": all_e.count(), "active": all_e.filter(status="active", expires_at__gt=now).count(),
             "expired": all_e.filter(status="active", expires_at__lte=now).count(),
             "pending": all_e.filter(status="pending").count(),
             "new_month": all_e.filter(created_at__gte=now - timedelta(days=30)).count()}
    page, qstr = paginate(request, all_e.select_related("student").order_by("-created_at"), 25)
    return render(request, "teacher/subscriptions.html", {"page": page, "qs": qstr, "stats": stats})


# ------------------------------------------------------------------ الحضور والغياب
def _fmt_login(dt, day):
    """آخر دخول بصيغة 08:45 ص (ويُضاف اليوم/الشهر لو كان في يوم غير اليوم المحدد)."""
    if not dt:
        return ""
    local = timezone.localtime(dt)
    txt = f"{local.hour % 12 or 12:02d}:{local.minute:02d} {'ص' if local.hour < 12 else 'م'}"
    if local.date() != day:
        txt = f"{local.day:02d}/{local.month:02d} {txt}"
    return txt


def _att_row(user, profile, status, day, enroll_pk=None):
    return {
        "id": user.pk,
        "num": profile.student_number if profile and profile.student_number else 0,
        "name": user.full_name,
        "grade": profile.get_grade_display().replace("الصف ", "") if profile else "",
        "status": status,
        "login": _fmt_login(user.last_login, day),
        "url": reverse("teacher:student_detail", args=[enroll_pk]) if enroll_pk else "",
    }


@teacher_required
def attendance(request):
    """صفحة الغياب والحضور: حضور المنصة (يدوي) + حضور السنتر (رفع PDF بأرقام الطلاب) في صفحة واحدة."""
    from accounts.models import StudentProfile
    from learning.models import CenterAttendance
    from . import center_service as svc
    from .center_views import _date, _grade, handle_center_post

    t = me(request)
    grade = _grade(request.GET.get("grade") or request.POST.get("grade"))
    d = _date(request.GET.get("date") or request.POST.get("date"))
    review = None

    if request.method == "POST":
        if request.POST.get("action") == "toggle":
            # تبديل حالة طالب في حضور المنصة (حفظ فوري)
            try:
                sid = int(request.POST.get("student_id") or 0)
            except ValueError:
                sid = 0
            ok = Enrollment.objects.filter(teacher=t, student_id=sid, status="active", expires_at__gt=timezone.now()).exists()
            if not ok:
                return JsonResponse({"ok": False}, status=404)
            present = request.POST.get("present") == "1"
            Attendance.objects.update_or_create(teacher=t, student_id=sid, date=d, defaults={"present": present})
            return JsonResponse({"ok": True, "present": present})
        review, response = handle_center_post(request, t, d, grade)
        if response:
            return response

    # ---- حضور المنصة: المشتركون حاليًا (غير المسجَّل يُعدّ حاضرًا كما كان في الصفحة القديمة)
    active = (Enrollment.objects.filter(teacher=t, status="active", expires_at__gt=timezone.now())
              .select_related("student", "student__student_profile"))
    if grade:
        active = active.filter(student__student_profile__grade=grade)
    existing = {a.student_id: a.present for a in Attendance.objects.filter(teacher=t, date=d)}
    platform_rows = []
    for e in active:
        prof = getattr(e.student, "student_profile", None)
        status = "present" if existing.get(e.student_id, True) else "absent"
        platform_rows.append(_att_row(e.student, prof, status, d, e.pk))
    platform_rows.sort(key=lambda r: (r["num"] or 10**9, r["name"]))

    # ---- حضور السنتر: من الكشف المرفوع (غير المسجَّل لا يظهر في أي قائمة)
    roster = list(svc.roster_queryset(t, grade).order_by("student_number"))
    recs = {r.student_id: r.present for r in CenterAttendance.objects.filter(teacher=t, date=d)}
    emap = {e.student_id: e.pk for e in Enrollment.objects.filter(teacher=t, student_id__in=[sp.user_id for sp in roster])}
    center_rows = []
    for sp in roster:
        if sp.user_id in recs:
            center_rows.append(_att_row(sp.user, sp, "present" if recs[sp.user_id] else "absent", d, emap.get(sp.user_id)))
        else:
            center_rows.append(_att_row(sp.user, sp, "none", d, emap.get(sp.user_id)))

    grade_label = ""
    for val, label in StudentProfile.GRADES:
        if val == grade:
            grade_label = label
    data = {"center": center_rows, "platform": platform_rows}
    return render(request, "teacher/attendance.html", {
        "d": d.isoformat(), "d_label": d.strftime("%Y/%m/%d"), "grade": grade, "grade_label": grade_label,
        "grades": StudentProfile.GRADES, "att_data": data,
        "review": review})


# ------------------------------------------------------------------ التحليلات
@teacher_required
def analytics(request):
    t = me(request)
    now = timezone.now()
    courses_qs = Course.objects.filter(teacher=t)
    course_rows = []
    for c in courses_qs:
        lessons = Lesson.objects.filter(unit__course=c)
        total_l = lessons.count()
        done_l = LessonProgress.objects.filter(lesson__in=lessons, completed=True).count()
        n_students = Enrollment.objects.filter(teacher=t, status="active", expires_at__gt=now).count()
        completion = round(done_l * 100 / (total_l * n_students)) if total_l and n_students else 0
        avg_score = Attempt.objects.filter(quiz__course=c, submitted_at__isnull=False).aggregate(a=Avg("score"))["a"]
        course_rows.append({"c": c, "completion": min(completion, 100), "avg_score": round(avg_score) if avg_score else None})
    hard_q = (Question.objects.filter(quiz__course__teacher=t)
              .annotate(wrong=Count("student_answers", filter=Q(student_answers__is_correct=False)),
                        total=Count("student_answers")).filter(total__gte=1).order_by("-wrong")[:8])
    hard_lessons = (LessonProgress.objects.filter(lesson__unit__course__teacher=t)
                    .values("lesson__title").annotate(started=Count("id"), done=Count("id", filter=Q(completed=True))))
    hard_lessons = sorted(hard_lessons, key=lambda r: (r["done"] / r["started"] if r["started"] else 1))[:6]
    falling_behind = (Enrollment.objects.filter(teacher=t, status="active", expires_at__gt=now).select_related("student")
                       .annotate(errs=Count("student__errors", filter=Q(student__errors__question__quiz__course__teacher=t, student__errors__resolved=False)))
                       .order_by("-errs")[:6])
    return render(request, "teacher/analytics.html", {"course_rows": course_rows, "hard_q": hard_q, "hard_lessons": hard_lessons, "falling_behind": falling_behind})


# ------------------------------------------------------------------ الرسائل والإعلانات
@teacher_required
def conversations(request):
    t = me(request)
    qs = Conversation.objects.filter(teacher=t).select_related("student").annotate(
        unread=Count("messages", filter=Q(messages__is_read=False) & ~Q(messages__sender__teacher_profile=t)))
    return render(request, "teacher/conversations.html", {"convs": qs})


@teacher_required
def conversation_detail(request, pk):
    conv = get_object_or_404(Conversation, pk=pk, teacher=me(request))
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text:
            ChatMessage.objects.create(conversation=conv, sender=request.user, text=text)
            conv.updated_at = timezone.now()
            conv.save(update_fields=["updated_at"])
            notify(conv.student, f"ردّ عليك {conv.teacher.display_label}", text[:100], f"/student/t/{conv.teacher.teacher_code}/messages/", "message")
        return redirect("teacher:conversation_detail", pk=pk)
    conv.messages.exclude(sender__teacher_profile=me(request)).update(is_read=True)
    return render(request, "teacher/conversation_detail.html", {"conv": conv, "msgs": conv.messages.select_related("sender")})


@teacher_required
def announcements(request):
    t = me(request)
    items = Announcement.objects.filter(teacher=t)
    return render(request, "teacher/announcements.html", {"items": items})


@teacher_required
def announcement_new(request):
    t = me(request)
    if request.method == "POST":
        title, body = request.POST.get("title", "").strip(), request.POST.get("body", "").strip()
        if title and body:
            a = Announcement.objects.create(author=request.user, teacher=t, audience="students", title=title, body=body)
            notify_students_of(t, title, body[:150], f"/student/t/{t.teacher_code}/announcements/", "announcement")
            messages.success(request, "تم نشر الإعلان وإرسال إشعار للطلاب.")
            return redirect("teacher:announcements")
        messages.error(request, "اكتب عنوانًا ونصًا للإعلان.")
    return redirect("teacher:announcements")


# ------------------------------------------------------------------ الملف الشخصي والإعدادات
@teacher_required
def profile(request):
    t = me(request)
    form = TeacherProfileForm(request.POST or None, request.FILES or None, instance=t)
    if request.method == "POST" and form.is_valid():
        form.save()
        request.user.full_name = form.cleaned_data["full_name"]
        request.user.save(update_fields=["full_name"])
        messages.success(request, "تم حفظ بياناتك.")
        return redirect("teacher:profile")
    return render(request, "teacher/profile.html", {"form": form, "t": t})


@teacher_required
def settings_view(request):
    t = me(request)
    pform = PasswordChangeForm2(request.user, request.POST or None)
    if request.method == "POST" and pform.is_valid():
        request.user.set_password(pform.cleaned_data["new1"])
        request.user.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, "تم تغيير كلمة المرور.")
        return redirect("teacher:settings")
    return render(request, "teacher/settings.html", {"pform": pform, "t": t})
