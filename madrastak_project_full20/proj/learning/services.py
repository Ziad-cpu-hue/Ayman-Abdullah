"""منطق الوصول والتقدّم والنشر: القلب الذي يربط لوحة المدرّس بلوحة الطالب."""
from django.db.models import Avg, Count, Q
from django.utils import timezone

from billing.models import Enrollment
from comms.services import notify_many

from .models import (Attempt, Course, ErrorRecord, Lesson, LessonProgress, Quiz, Unit, vis_q)


# ---------- الرؤية (ما يراه الطالب) ----------
def visible_courses(teacher):
    return Course.objects.filter(teacher=teacher).filter(vis_q())


def visible_units(course):
    return course.units.filter(vis_q())


def visible_lessons_qs(teacher=None, course=None, unit=None):
    qs = Lesson.objects.filter(vis_q(), vis_q("unit__"), vis_q("unit__course__"))
    if teacher:
        qs = qs.filter(unit__course__teacher=teacher)
    if course:
        qs = qs.filter(unit__course=course)
    if unit:
        qs = qs.filter(unit=unit)
    return qs


def visible_quizzes_qs(teacher=None, course=None, lesson=None, unit=None):
    qs = Quiz.objects.filter(vis_q(), vis_q("course__"))
    qs = qs.filter(Q(unit__isnull=True) | vis_q("unit__"))
    qs = qs.filter(Q(lesson__isnull=True) | vis_q("lesson__"))
    if teacher:
        qs = qs.filter(course__teacher=teacher)
    if course:
        qs = qs.filter(course=course)
    if lesson:
        qs = qs.filter(lesson=lesson)
    if unit:
        qs = qs.filter(unit=unit)
    return qs


# ---------- الاشتراك والوصول ----------
def get_enrollment(student, teacher):
    return Enrollment.objects.filter(student=student, teacher=teacher).first()


def has_active_access(student, teacher):
    e = get_enrollment(student, teacher)
    return bool(e and e.is_active)


def can_open_lesson(student, lesson):
    """الدرس يُفتح إن كان مجانيًا/تجريبيًا أو للطالب اشتراك نشط مع مدرّسه."""
    if lesson.course.is_free or lesson.is_free_preview:
        return True
    return has_active_access(student, lesson.teacher)


def can_open_quiz(student, quiz):
    if quiz.course.is_free:
        return True
    if quiz.lesson_id and quiz.lesson.is_free_preview:
        return True
    return has_active_access(student, quiz.teacher)


# ---------- التقدّم ----------
def teacher_progress(student, teacher):
    total = visible_lessons_qs(teacher=teacher).count()
    done = LessonProgress.objects.filter(student=student, completed=True, lesson__in=visible_lessons_qs(teacher=teacher)).count()
    return {"total": total, "done": done, "percent": round(done * 100 / total) if total else 0}


def course_progress(student, course):
    lessons = visible_lessons_qs(course=course)
    total = lessons.count()
    done = LessonProgress.objects.filter(student=student, completed=True, lesson__in=lessons).count()
    return {"total": total, "done": done, "percent": round(done * 100 / total) if total else 0}


def unit_progress(student, unit):
    lessons = visible_lessons_qs(unit=unit)
    total = lessons.count()
    done = LessonProgress.objects.filter(student=student, completed=True, lesson__in=lessons).count()
    return {"total": total, "done": done, "percent": round(done * 100 / total) if total else 0}


def lesson_status(student, lesson, progress_map):
    p = progress_map.get(lesson.id)
    if p and p.needs_review:
        return "review"
    if p and p.completed:
        return "done"
    if p:
        return "progress"
    return "new"


def touch_lesson(student, lesson):
    p, _ = LessonProgress.objects.get_or_create(student=student, lesson=lesson)
    p.last_viewed = timezone.now()
    p.save(update_fields=["last_viewed"])
    return p


def complete_lesson(student, lesson):
    p, _ = LessonProgress.objects.get_or_create(student=student, lesson=lesson)
    if not p.completed:
        p.completed = True
        p.completed_at = timezone.now()
    p.needs_review = False
    p.last_viewed = timezone.now()
    p.save()
    return p


def teacher_stats_for_student(student, teacher):
    atts = Attempt.objects.filter(student=student, quiz__course__teacher=teacher, submitted_at__isnull=False)
    avg = atts.aggregate(a=Avg("score"), t=Avg("total"))
    pct = [a.percent for a in atts]
    return {
        "attempts": atts.count(),
        "avg_percent": round(sum(pct) / len(pct)) if pct else 0,
        "best": max(pct) if pct else 0,
        "worst": min(pct) if pct else 0,
        "errors_open": ErrorRecord.objects.filter(student=student, question__quiz__course__teacher=teacher, resolved=False, reviewed=False).count(),
        "errors_total": ErrorRecord.objects.filter(student=student, question__quiz__course__teacher=teacher).count(),
    }


# ---------- التصحيح ----------
def grade_attempt(attempt, post):
    """يصحّح المحاولة في الخادم ويسجّل الأخطاء. post: QueryDict فيه q_<id> = choice_id."""
    from .models import Choice, StudentAnswer
    quiz = attempt.quiz
    questions = list(quiz.questions.prefetch_related("choices"))
    score = correct = wrong = 0
    # الأسئلة المقالية لا تُصحَّح آليًا: تُحفظ إجابتها فقط ولا تدخل في الدرجة الكلية
    total = sum(q.points for q in questions if q.qtype != "essay")
    attempt.answers.all().delete()
    for q in questions:
        if q.qtype == "essay":
            StudentAnswer.objects.create(attempt=attempt, question=q, choice=None, is_correct=False,
                                         text_answer=(post.get(f"q_{q.id}") or "").strip())
            continue
        raw = post.get(f"q_{q.id}")
        choice = None
        if raw and raw.isdigit():
            choice = next((c for c in q.choices.all() if c.id == int(raw)), None)
        ok = bool(choice and choice.is_correct)
        StudentAnswer.objects.create(attempt=attempt, question=q, choice=choice, is_correct=ok)
        if ok:
            score += q.points
            correct += 1
            ErrorRecord.objects.filter(student=attempt.student, question=q).update(resolved=True)
        else:
            wrong += 1
            rec, created = ErrorRecord.objects.get_or_create(student=attempt.student, question=q, defaults={"choice": choice})
            if not created:
                rec.choice = choice
                rec.times_wrong += 1
                rec.resolved = False
                rec.reviewed = False
                rec.save()
    attempt.score, attempt.total, attempt.correct_count, attempt.wrong_count = score, total, correct, wrong
    attempt.submitted_at = timezone.now()
    attempt.save()
    if quiz.lesson_id:
        complete_lesson(attempt.student, quiz.lesson)
        if wrong and total and (score * 100 / total) < 60:
            LessonProgress.objects.filter(student=attempt.student, lesson=quiz.lesson).update(needs_review=True)
    return attempt


# ---------- الإشعارات عند النشر ----------
def notify_students_of(teacher, title, body, url, kind="content"):
    ids = Enrollment.objects.filter(teacher=teacher, status="active", expires_at__gt=timezone.now()).values_list("student_id", flat=True)
    from accounts.models import User
    notify_many(User.objects.filter(id__in=ids, is_active=True), title, body, url, kind)


def similar_question(question, exclude_ids=()):
    """سؤال مشابه: نفس القسم ثم نفس الدرس ثم نفس الدورة، مع تفضيل نفس الصعوبة."""
    from .models import Question
    base = Question.objects.filter(quiz__course=question.quiz.course).exclude(id=question.id).exclude(id__in=exclude_ids).exclude(qtype="essay")
    base = base.filter(quiz__status__in=("published", "scheduled"))
    for qs in (
        base.filter(section=question.section) if question.section_id else None,
        base.filter(quiz__lesson=question.quiz.lesson) if question.quiz.lesson_id else None,
        base,
    ):
        if qs is not None and qs.exists():
            same = qs.filter(difficulty=question.difficulty)
            return (same.first() or qs.first())
    return None
