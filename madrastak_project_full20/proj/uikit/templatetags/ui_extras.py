"""وسوم وفلاتر القوالب للواجهة الجديدة.

كل القيم المعروضة هنا تُحسب من قاعدة البيانات الفعلية (لا أرقام ثابتة). لا شيء هنا يكتب في قاعدة البيانات.
"""
import calendar
import math
import re
from datetime import timedelta

from django import template
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import TeacherProfile
from billing.models import Enrollment
from learning import services as svc
from learning.models import (Attempt, Attendance, Course, ErrorRecord, Lesson, LessonProgress, Question, Quiz,
                             StudentAnswer, Unit, VocabularyWord)

register = template.Library()

AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


# ------------------------------------------------------------------ فلاتر بسيطة
@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except Exception:
        return None


@register.filter
def fmt_minutes(m):
    """يحوّل عدد الدقائق إلى نص مقروء: «45 دقيقة» أو «2.5 ساعة»."""
    try:
        m = int(m or 0)
    except (TypeError, ValueError):
        return "—"
    if m <= 0:
        return "—"
    if m < 60:
        return f"{m} دقيقة"
    h = round(m / 60, 1)
    return f"{h:g} ساعة"


@register.filter
def hours_only(m):
    try:
        m = int(m or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{round(m / 60, 1):g}"


@register.filter
def lines(text):
    """يقسّم النص إلى أسطر غير فارغة (لعرض النقاط والأمثلة)."""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


@register.filter
def clean_bullet(line):
    return re.sub(r"^\s*(?:[-•*▪●]+|\d+[\.\)])\s*", "", line or "").strip()


@register.filter
def split_example(line):
    """«She drinks tea — هي تشرب الشاي» → ("She drinks tea", "هي تشرب الشاي")."""
    line = clean_bullet(line)
    for sep in (" — ", " – ", " | ", " - ", " ⇒ ", " => "):
        if sep in line:
            a, b = line.split(sep, 1)
            return (a.strip(), b.strip())
    m = re.match(r"^([A-Za-z0-9\"'“”.,;:!?()\-\s]+?)\s+([\u0600-\u06FF].*)$", line)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (line, "")


@register.filter
def duration_between(start, end):
    """المدة بين وقتين بصيغة mm:ss."""
    if not start or not end:
        return "—"
    secs = max(0, int((end - start).total_seconds()))
    return f"{secs // 60:02d}:{secs % 60:02d}"


@register.filter
def letter(i):
    try:
        return "أبجدهوز"[int(i)]
    except Exception:
        return ""


@register.filter
def any_type(rows, course_type):
    """هل توجد دورة من هذا النوع ضمن صفوف مساحة المعلم؟"""
    try:
        return any(r["c"].course_type == course_type for r in rows)
    except Exception:
        return False


@register.filter
def times_range(n):
    try:
        return range(1, int(n) + 1)
    except Exception:
        return range(0)


# ------------------------------------------------------------------ تقديرات الوقت
def lesson_minutes(lesson):
    """وقت الدرس التقديري (بالدقائق) = قراءة الشرح + مدد الاختبارات/التدريبات + مراجعة المفردات."""
    words = sum(len((s.content or "").split()) for s in lesson.sections.all())
    read = max(1, round(words / 180)) if words else 0
    quiz_min = 0
    for q in svc.visible_quizzes_qs(lesson=lesson):
        n = q.questions.count()
        if not n:
            continue
        quiz_min += q.duration_minutes or n
    vocab_min = math.ceil(lesson.vocabulary.count() / 2) if lesson.vocabulary.exists() else 0
    return read + quiz_min + vocab_min


def _visible_lessons_minutes(qs):
    return sum(lesson_minutes(l) for l in qs)


# ------------------------------------------------------------------ معلومات الدرس/الوحدة/الدورة للطالب
@register.simple_tag
def lesson_info(lesson, student):
    p = LessonProgress.objects.filter(student=student, lesson=lesson).first()
    quizzes = [q for q in svc.visible_quizzes_qs(lesson=lesson) if q.questions.exists()]
    tests = [q for q in quizzes if q.kind in ("quiz", "exam")]
    exercises = [q for q in quizzes if q.kind == "homework"]
    attempted = Attempt.objects.filter(student=student, quiz__in=quizzes, submitted_at__isnull=False).values("quiz_id").distinct().count() if quizzes else 0
    total = 2 + len(quizzes)
    done = (1 if p else 0) + (1 if p and p.completed else 0) + attempted
    percent = min(100, round(done * 100 / total)) if total else 0
    if p and p.needs_review:
        status, label = "review", "يحتاج مراجعة"
    elif p and p.completed:
        status, label = "done", "مكتمل"
    elif p:
        status, label = "progress", "قيد الدراسة"
    else:
        status, label = "new", "لم يبدأ"
    return {
        "percent": percent, "status": status, "status_label": label,
        "tests": len(tests), "exercises": len(exercises),
        "has_video": bool(lesson.video_file or lesson.video_url),
        "has_text": lesson.sections.exists(),
        "vocab": lesson.vocabulary.count(),
        "minutes": lesson_minutes(lesson),
    }


@register.simple_tag
def unit_stats(unit, student):
    lessons = svc.visible_lessons_qs(unit=unit)
    quizzes = svc.visible_quizzes_qs(unit=unit)
    n_tests = sum(1 for q in quizzes.filter(kind__in=("quiz", "exam")) if q.questions.exists())
    n_hw = sum(1 for q in quizzes.filter(kind="homework") if q.questions.exists())
    return {"lessons": lessons.count(), "tests": n_tests, "homework": n_hw,
            "minutes": _visible_lessons_minutes(lessons), "progress": svc.unit_progress(student, unit)}


@register.simple_tag
def course_stats(course, student):
    lessons = svc.visible_lessons_qs(course=course)
    quizzes = svc.visible_quizzes_qs(course=course)
    n_tests = sum(1 for q in quizzes.filter(kind__in=("quiz", "exam")) if q.questions.exists())
    n_hw = sum(1 for q in quizzes.filter(kind="homework") if q.questions.exists())
    return {"lessons": lessons.count(), "units": svc.visible_units(course).count(), "tests": n_tests, "homework": n_hw,
            "minutes": _visible_lessons_minutes(lessons), "progress": svc.course_progress(student, course),
            "vocab": VocabularyWord.objects.filter(lesson__in=lessons).count()}


@register.simple_tag
def course_schedule(course, student):
    """خطة الدراسة: الاختبارات والواجبات المنشورة في الدورة مرتبة بالموعد (أو الأحدث)."""
    now = timezone.now()
    out = []
    done = set(Attempt.objects.filter(student=student, quiz__course=course, submitted_at__isnull=False).values_list("quiz_id", flat=True))
    for q in svc.visible_quizzes_qs(course=course).select_related("lesson", "unit"):
        if not q.questions.exists():
            continue
        out.append({"q": q, "done": q.id in done, "late": bool(q.deadline and q.deadline < now and q.id not in done)})
    out.sort(key=lambda r: (r["q"].deadline is None, r["q"].deadline or now, r["q"].id))
    return out[:8]


@register.simple_tag
def teacher_public_stats(teacher):
    now = timezone.now()
    return {
        "students": Enrollment.objects.filter(teacher=teacher, status="active", expires_at__gt=now).count(),
        "courses": svc.visible_courses(teacher).count(),
        "lessons": svc.visible_lessons_qs(teacher=teacher).count(),
        "tests": svc.visible_quizzes_qs(teacher=teacher).count(),
        "units": sum(svc.visible_units(c).count() for c in svc.visible_courses(teacher)),
    }


# ------------------------------------------------------------------ الصفحة الرئيسية للطالب
def _pct_avg(attempts):
    pcts = [a.percent for a in attempts]
    return round(sum(pcts) / len(pcts)) if pcts else None


def _design_y(pct):
    """إحداثي Y لنقطة نتيجة داخل مخطط تصميم الطالب (viewBox 900x180).
    خطوط الشبكة في التصميم: 100%→20 ، 75%→65 ، 50%→110 ، 0%→155؛ فنُسقط النتيجة بين هذه الخطوط بالضبط
    حتى تتطابق النقطة مع الرقم المكتوب على المحور."""
    p = max(0, min(100, pct))
    if p <= 50:
        y = 155 - p * 0.9
    else:
        y = 110 - (p - 50) * 1.8
    return round(y, 1)


@register.simple_tag
def student_home_data(student):
    now = timezone.now()
    enrolls = list(Enrollment.objects.filter(student=student).select_related("teacher__user"))
    active = [e for e in enrolls if e.is_active]
    nearest = min(active, key=lambda e: e.expires_at) if active else None
    since30, since60 = now - timedelta(days=30), now - timedelta(days=60)

    done_lessons = LessonProgress.objects.filter(student=student, completed=True)
    attempts = list(Attempt.objects.filter(student=student, submitted_at__isnull=False).select_related("quiz__course__teacher__user").order_by("submitted_at"))
    last30 = [a for a in attempts if a.submitted_at >= since30]
    prev30 = [a for a in attempts if since60 <= a.submitted_at < since30]
    avg_all, avg30, avgprev = _pct_avg(attempts), _pct_avg(last30), _pct_avg(prev30)

    # الاختبارات/الواجبات القادمة
    active_ids = [e.teacher_id for e in active]
    done_quiz_ids = {a.quiz_id for a in attempts}
    upcoming = []
    for q in svc.visible_quizzes_qs().filter(course__teacher_id__in=active_ids).exclude(id__in=done_quiz_ids).select_related("course__teacher__user", "lesson"):
        n = q.questions.count()
        if not n or (q.deadline and q.deadline < now):
            continue
        upcoming.append({"q": q, "n": n, "teacher": q.course.teacher, "when": q.deadline})
    upcoming.sort(key=lambda r: (r["when"] is None, r["when"] or now, r["q"].id))
    upcoming = upcoming[:4]

    # التقويم الدراسي للشهر الحالي
    local = timezone.localtime(now)
    y, m = local.year, local.month
    events = {}
    for q in svc.visible_quizzes_qs().filter(course__teacher_id__in=active_ids, deadline__isnull=False):
        d = timezone.localtime(q.deadline)
        if d.year == y and d.month == m and q.questions.exists():
            events[d.day] = events.get(d.day, 0) + 1
    weeks = calendar.Calendar(firstweekday=5).monthdayscalendar(y, m)
    cal = [[{"day": d, "today": d == local.day, "events": events.get(d, 0)} if d else None for d in wk] for wk in weeks]

    # مخطط الأداء (آخر 7 محاولات)
    pts_src = attempts[-7:]
    pts, path, area = [], "", ""
    n = len(pts_src)
    for i, a in enumerate(pts_src):
        x = 200 if n == 1 else round(10 + i * (380 / (n - 1)), 1)
        y_ = round(190 - a.percent * 1.7, 1)
        pts.append({"x": x, "y": y_, "p": a.percent, "label": timezone.localtime(a.submitted_at).strftime("%d/%m"), "title": a.quiz.title})
    if pts:
        path = "M " + " L ".join(f"{p['x']} {p['y']}" for p in pts)
        area = path + f" L {pts[-1]['x']} 200 L {pts[0]['x']} 200 Z"

    # مخطط الأداء بإحداثيات تصميم الصفحة الرئيسية الجديد (viewBox 900x180)
    dpts = []
    for i, a in enumerate(pts_src):
        dx = 465 if n == 1 else round(90 + i * (750 / (n - 1)), 1)
        dpts.append({"x": dx, "y": _design_y(a.percent), "p": a.percent, "title": a.quiz.title})
    dline = " ".join(f"{p['x']},{p['y']}" for p in dpts)
    darea = f"{dline} {dpts[-1]['x']},155 {dpts[0]['x']},155" if dpts else ""
    # التسميات تُعرض بترتيب RTL (الأول يظهر يمينًا = الأحدث)
    dlabels = [p["label"] for p in reversed(pts)]

    # آخر المحاضرات
    recent = []
    for p in LessonProgress.objects.filter(student=student).select_related("lesson__unit__course__teacher").order_by("-last_viewed")[:4]:
        l = p.lesson
        if not svc.visible_lessons_qs().filter(pk=l.pk).exists():
            continue
        recent.append({"l": l, "p": p, "teacher": l.unit.course.teacher, "minutes": lesson_minutes(l)})
    week_views = LessonProgress.objects.filter(student=student, last_viewed__gte=now - timedelta(days=7)).count()

    delta = (avg30 - avgprev) if (avg30 is not None and avgprev is not None) else None
    if avg30 is None:
        level = ""
    elif avg30 >= 85:
        level = "ممتاز جداً"
    elif avg30 >= 70:
        level = "جيد جداً"
    elif avg30 >= 50:
        level = "مقبول"
    else:
        level = "يحتاج تحسين"
    return {
        "days_left": nearest.days_left if nearest else 0, "expires_at": nearest.expires_at if nearest else None,
        "nearest_teacher": nearest.teacher if nearest else None, "active_count": len(active), "enroll_count": len(enrolls),
        "lectures_done": done_lessons.count(), "lectures_30": done_lessons.filter(completed_at__gte=since30).count(),
        "exams_done": len(attempts), "exams_30": len(last30),
        "avg_all": avg_all, "avg30": avg30, "delta": delta, "level": level,
        "upcoming": upcoming, "calendar": cal, "month_name": AR_MONTHS[m - 1], "year": y,
        "points": pts, "path": path, "area": area,
        "dpoints": dpts, "dline": dline, "darea": darea, "dlabels": dlabels,
        "recent": recent, "week_views": week_views,
        "open_errors": ErrorRecord.objects.filter(student=student, reviewed=False, resolved=False).count(),
    }


@register.simple_tag
def student_focus_data(student, lesson=None):
    """بيانات «بطاقة المعلم والمادة» في الرئيسية: المعلم صاحب آخر درس لم يكتمل، وإلا أقرب اشتراك فعّال، وإلا أول اشتراك.
    كل الأرقام حقيقية من قاعدة البيانات (التقدم، الواجبات، الحضور، الأسئلة المجابة)."""
    enrolls = list(Enrollment.objects.filter(student=student, teacher__user__is_active=True).select_related("teacher__user"))
    if not enrolls:
        return None
    enr = None
    if lesson is not None:
        tid = lesson.unit.course.teacher_id
        enr = next((e for e in enrolls if e.teacher_id == tid), None)
    if enr is None:
        active = [e for e in enrolls if e.is_active]
        enr = min(active, key=lambda e: e.expires_at) if active else enrolls[0]
    t = enr.teacher
    pr = svc.teacher_progress(student, t)
    hw = svc.visible_quizzes_qs(teacher=t).filter(kind="homework")
    hw_ids = [q.id for q in hw if q.questions.exists()]
    hw_done = Attempt.objects.filter(student=student, quiz_id__in=hw_ids, submitted_at__isnull=False).values("quiz_id").distinct().count() if hw_ids else 0
    att = Attendance.objects.filter(student=student, teacher=t)
    att_total = att.count()
    att_pct = round(att.filter(present=True).count() * 100 / att_total) if att_total else None
    answered = StudentAnswer.objects.filter(attempt__student=student, attempt__quiz__course__teacher=t, attempt__submitted_at__isnull=False).count()
    return {"teacher": t, "enrollment": enr, "done": pr["done"], "total": pr["total"], "percent": pr["percent"],
            "hw_done": hw_done, "hw_total": len(hw_ids), "attendance": att_pct, "answers": answered}


@register.simple_tag
def student_profile_stats(student):
    attempts = list(Attempt.objects.filter(student=student, submitted_at__isnull=False))
    secs = sum(max(0, int((a.submitted_at - a.started_at).total_seconds())) for a in attempts)
    read = 0
    for p in LessonProgress.objects.filter(student=student, completed=True).select_related("lesson"):
        words = sum(len((s.content or "").split()) for s in p.lesson.sections.all())
        read += max(1, round(words / 180)) if words else 0
    minutes = round(secs / 60) + read
    passed = sum(1 for a in attempts if a.percent >= 50)
    return {"hours": round(minutes / 60, 1), "attempts": len(attempts), "passed": passed,
            "lessons": LessonProgress.objects.filter(student=student, completed=True).count(),
            "avg": _pct_avg(attempts)}


# ------------------------------------------------------------------ المدرّس
@register.simple_tag
def teacher_avg_score(teacher):
    agg = Attempt.objects.filter(quiz__course__teacher=teacher, submitted_at__isnull=False).aggregate(s=Sum("score"), t=Sum("total"))
    if agg["t"]:
        return round(agg["s"] * 100 / agg["t"])
    return None


@register.simple_tag
def teacher_courses_overview(teacher, limit=4):
    now = timezone.now()
    n_students = Enrollment.objects.filter(teacher=teacher, status="active", expires_at__gt=now).count()
    rows = []
    for c in Course.objects.filter(teacher=teacher).order_by("order", "id")[:limit]:
        lessons = Lesson.objects.filter(unit__course=c)
        total = lessons.count()
        done = LessonProgress.objects.filter(lesson__in=lessons, completed=True).count()
        completion = min(100, round(done * 100 / (total * n_students))) if total and n_students else 0
        rows.append({"c": c, "units": c.units.count(), "lessons": total, "students": n_students, "completion": completion})
    return rows


@register.simple_tag
def enrollment_metrics(enrollment):
    """تقدّم الطالب ومتوسط درجاته مع مدرّسه (لجداول المدرّس)."""
    s, t = enrollment.student, enrollment.teacher
    pr = svc.teacher_progress(s, t)
    st = svc.teacher_stats_for_student(s, t)
    return {"pr": pr, "st": st}


# ------------------------------------------------------------------ تاريخ الصفحة الجديدة للنتيجة
@register.filter
def res_dt(value):
    """تاريخ ووقت بصيغة 2025/08/15 - 10:42 (توقيت القاهرة، نظام 12 ساعة)."""
    if not value:
        return "—"
    v = timezone.localtime(value) if timezone.is_aware(value) else value
    return v.strftime("%Y/%m/%d - %I:%M")


@register.filter
def res_ampm(value):
    """ص / م حسب وقت التاريخ."""
    if not value:
        return ""
    v = timezone.localtime(value) if timezone.is_aware(value) else value
    return "ص" if v.hour < 12 else "م"


@register.filter
def res_period(value):
    """صباحاً / مساءً حسب وقت التاريخ."""
    if not value:
        return ""
    v = timezone.localtime(value) if timezone.is_aware(value) else value
    return "صباحاً" if v.hour < 12 else "مساءً"


@register.filter
def notif_cat(n):
    """تصنيف الإشعار لعرضه بتصميم صفحة الإشعارات: course / lecture / lesson / quiz / update / alert."""
    title = (n.title or "").strip()
    if n.kind == "quiz":
        return "quiz"
    if title.startswith("دورة جديدة"):
        return "course"
    if title.startswith("وحدة جديدة"):
        return "lecture"
    if title.startswith("درس جديد"):
        return "lesson"
    if n.kind == "content":
        return "update"
    return "alert"
