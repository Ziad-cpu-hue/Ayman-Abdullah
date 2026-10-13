"""ملف المستخدم الذي يراه المساعد: كل بيانات الطالب (عبر كل مدرّسيه) أو إحصاءات المدرّس.

يُبنى نصًا عربيًا مضغوطًا يُمرَّر للنموذج ليتمكن من التوصية وتحليل نقاط الضعف.
القواعد:
  * بيانات الطالب نفسه فقط — لا تُدرَج بيانات أي طالب آخر في ملف الطالب أبدًا.
  * لا تُدرَج نصوص أسئلة الاختبارات أو إجاباتها الصحيحة (حتى لا يتحول المساعد إلى أداة غش).
  * الدروس تُذكر بمعرّف [#L123] ليتمكن النظام من التحقق من التوصيات وتحويلها إلى روابط.
"""
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

MAX_CHARS_STUDENT = 3200
MAX_CHARS_TEACHER = 3000


def _pct(a):
    return a.percent if a.total else 0


def _lesson_of_question(q):
    if q.section_id and q.section.lesson_id:
        return q.section.lesson_id, q.section.title
    if q.quiz.lesson_id:
        return q.quiz.lesson_id, ""
    return None, ""


def student_snapshot(student, corpus):
    """يعيد (نص الملف, مجموعة معرّفات الدروس المتاحة للتوصية)."""
    from accounts.models import StudentProfile
    from billing.models import Enrollment
    from learning.models import (Attempt, Attendance, CenterAttendance, ErrorRecord, LessonProgress,
                                 VocabularyProgress)
    now = timezone.now()
    lines = []
    try:
        sp = student.student_profile
        lines.append(f"الاسم: {student.full_name} | الصف: {sp.get_grade_display()} | رقم الطالب (ID): {sp.student_number or '-'}")
    except StudentProfile.DoesNotExist:
        lines.append(f"الاسم: {student.full_name}")
    lines.append(f"التاريخ اليوم: {timezone.localdate().isoformat()}")

    enrolls = list(Enrollment.objects.filter(student=student).select_related("teacher__user"))
    if not enrolls:
        lines.append("الاشتراكات: لا يوجد أي اشتراك مع مدرّس حتى الآن.")
        return "\n".join(lines), set()
    lines.append("الاشتراكات:")
    for e in enrolls:
        extra = f"، متبقٍ {e.days_left} يوم" if e.is_active else ""
        lines.append(f"- {e.teacher.display_label} ({e.teacher.subject}): {e.display_label}{extra}")

    lesson_ids = set(corpus.lessons)
    prog = {p.lesson_id: p for p in LessonProgress.objects.filter(student=student, lesson_id__in=lesson_ids)}
    # ---- التقدم لكل دورة/وحدة
    by_course = {}
    for lid, m in corpus.lessons.items():
        by_course.setdefault(m["course"], []).append((lid, m))
    if by_course:
        lines.append("التقدم في الدروس المتاحة (مكتمل/الكل):")
    pending, review = [], []
    for course, items in by_course.items():
        done = sum(1 for lid, _ in items if prog.get(lid) and prog[lid].completed)
        lines.append(f"- {course}: {done}/{len(items)}")
        for lid, m in items:
            p = prog.get(lid)
            if p and p.needs_review:
                review.append((lid, m))
            elif not (p and p.completed):
                pending.append((lid, m, "بدأه ولم يكمله" if p else "لم يفتحه"))
    if review:
        lines.append("دروس وسمها الطالب للمراجعة: " + "؛ ".join(f"[#L{l}] {m['title']}" for l, m in review[:8]))
    if pending:
        lines.append("دروس لم تكتمل (بترتيب المنهج): " + "؛ ".join(f"[#L{l}] {m['title']} ({st})" for l, m, st in pending[:10])
                     + (f" … و{len(pending) - 10} أخرى" if len(pending) > 10 else ""))
    recent = (LessonProgress.objects.filter(student=student, lesson_id__in=lesson_ids).order_by("-last_viewed")[:3])
    if recent:
        lines.append("آخر دروس فتحها: " + "؛ ".join(f"[#L{p.lesson_id}] {corpus.lessons[p.lesson_id]['title']}" for p in recent))

    # ---- الاختبارات والدرجات
    atts = list(Attempt.objects.filter(student=student, submitted_at__isnull=False)
                .select_related("quiz__lesson", "quiz__course__teacher__user").order_by("-submitted_at")[:60])
    if atts:
        pcts = [_pct(a) for a in atts]
        lines.append(f"الاختبارات: {len(atts)} محاولة مُسلَّمة، المتوسط {round(sum(pcts) / len(pcts))}%")
        best = {}
        for a in atts:
            k = a.quiz_id
            if k not in best or _pct(a) > _pct(best[k]):
                best[k] = a
        weak = sorted((a for a in best.values() if _pct(a) < 70), key=_pct)[:6]
        if weak:
            lines.append("اختبارات أفضل نتيجة فيها أقل من 70%: " + "؛ ".join(
                f"{a.quiz.title} ({_pct(a)}%" + (f"، درس [#L{a.quiz.lesson_id}]" if a.quiz.lesson_id in lesson_ids else "") + ")"
                for a in weak))
        lines.append("آخر النتائج: " + "؛ ".join(f"{a.quiz.title} {_pct(a)}% ({timezone.localtime(a.submitted_at).date().isoformat()})"
                                                   for a in atts[:4]))
    else:
        lines.append("الاختبارات: لم يسلّم أي اختبار بعد.")

    # ---- الأخطاء غير المحلولة مجمّعة حسب الدرس/القسم (بدون نص الأسئلة)
    errs = (ErrorRecord.objects.filter(student=student, resolved=False)
            .select_related("question__quiz", "question__section"))
    agg = {}
    for er in errs:
        lid, sec = _lesson_of_question(er.question)
        key = (lid, sec)
        agg[key] = agg.get(key, 0) + er.times_wrong
    if agg:
        top = sorted(agg.items(), key=lambda kv: -kv[1])[:7]
        parts = []
        for (lid, sec), n in top:
            if lid in lesson_ids:
                parts.append(f"[#L{lid}] {corpus.lessons[lid]['title']}" + (f" — قسم «{sec}»" if sec else "") + f" ({n} خطأ)")
            else:
                parts.append((sec or "اختبار عام") + f" ({n} خطأ)")
        lines.append(f"أسئلة أخطأ فيها ولم يحلّها بعد: {sum(agg.values())} خطأ. أكثر المواضع تعثرًا: " + "؛ ".join(parts))

    # ---- المفردات المتعثّر فيها
    vp = (VocabularyProgress.objects.filter(student=student, wrong_count__gt=0, correct_streak__lt=2)
          .select_related("word").order_by("-wrong_count")[:10])
    if vp:
        lines.append("مفردات يحتاج مراجعتها: " + "، ".join(f"{p.word.word}" for p in vp))

    # ---- الحضور (آخر 30 يومًا)
    since = timezone.localdate() - timedelta(days=30)
    for label, model in (("المنصة", Attendance), ("السنتر", CenterAttendance)):
        rows = list(model.objects.filter(student=student, date__gte=since).order_by("-date"))
        if rows:
            ab = [r for r in rows if not r.present]
            txt = f"الحضور ({label}) آخر 30 يومًا: حضر {len(rows) - len(ab)} وغاب {len(ab)}"
            if ab:
                txt += " (أيام الغياب: " + "، ".join(r.date.isoformat() for r in ab[:5]) + ")"
            lines.append(txt)

    # ---- الواجبات القادمة
    try:
        from learning.models import Quiz
        from learning.services import visible_quizzes_qs
        teacher_ids = [e.teacher_id for e in enrolls if e.is_active]
        submitted = set(Attempt.objects.filter(student=student, submitted_at__isnull=False).values_list("quiz_id", flat=True))
        hw = visible_quizzes_qs().filter(course__teacher_id__in=teacher_ids, kind="homework").exclude(id__in=submitted)[:6]
        due = [q for q in hw if not q.is_closed]
        if due:
            lines.append("واجبات لم يسلّمها: " + "؛ ".join(
                q.title + (f" (آخر موعد {timezone.localtime(q.deadline).date().isoformat()})" if q.deadline else "") for q in due))
    except Exception:
        pass

    text = "\n".join(lines)
    if len(text) > MAX_CHARS_STUDENT:
        text = text[:MAX_CHARS_STUDENT].rsplit("\n", 1)[0] + "\n…(تم اختصار باقي الملف)"
    return text, lesson_ids


def teacher_snapshot(teacher, corpus):
    """ملخص للمدرّس عن دوراته وطلابه (طلابه هو فقط)."""
    from billing.models import Enrollment
    from learning.models import Attempt, Attendance, CenterAttendance, Course, ErrorRecord, LessonProgress
    now = timezone.now()
    lines = [f"المدرّس: {teacher.user.full_name} | المادة: {teacher.subject}", f"التاريخ اليوم: {timezone.localdate().isoformat()}"]
    courses = list(Course.objects.filter(teacher=teacher).annotate(n_units=Count("units", distinct=True)))
    lines.append(f"الدورات: {len(courses)}، الدروس: {len(corpus.lessons)}")
    for c in courses[:8]:
        lines.append(f"- {c.title}: {c.n_units} وحدة، الحالة: {c.status_label}")

    active = list(Enrollment.objects.filter(teacher=teacher, status="active", expires_at__gt=now).select_related("student"))
    pending = Enrollment.objects.filter(teacher=teacher, status="pending").count()
    lines.append(f"الطلاب المشتركون حاليًا: {len(active)}، طلبات قيد المراجعة/الدفع: {pending}")
    sids = [e.student_id for e in active]
    atts = Attempt.objects.filter(quiz__course__teacher=teacher, submitted_at__isnull=False)
    if atts.exists():
        rows = (atts.values("quiz__title", "quiz__lesson_id").annotate(n=Count("id"), s=Avg("score"), t=Avg("total")))
        scored = sorted(((round(r["s"] * 100 / r["t"]) if r["t"] else 0, r) for r in rows), key=lambda x: x[0])
        lines.append(f"إجمالي محاولات الاختبارات المُسلَّمة: {atts.count()}")
        lines.append("أضعف الاختبارات متوسطًا: " + "؛ ".join(
            f"{r['quiz__title']} ({p}%، {r['n']} محاولة)" + (f" [#L{r['quiz__lesson_id']}]" if r["quiz__lesson_id"] in corpus.lessons else "")
            for p, r in scored[:5]))
    errs = (ErrorRecord.objects.filter(question__quiz__course__teacher=teacher, resolved=False)
            .values("question__section__title", "question__quiz__lesson_id").annotate(n=Count("id")).order_by("-n")[:5])
    if errs:
        lines.append("أكثر المواضع تعثّرًا للطلاب: " + "؛ ".join(
            ((r["question__section__title"] or "بدون قسم") + (f" في [#L{r['question__quiz__lesson_id']}]" if r["question__quiz__lesson_id"] in corpus.lessons else "")
             + f" ({r['n']} خطأ)") for r in errs))
    # طلاب يحتاجون متابعة
    total_lessons = len([l for l in corpus.lessons])
    if sids and total_lessons:
        done = dict(LessonProgress.objects.filter(student_id__in=sids, completed=True, lesson_id__in=list(corpus.lessons))
                    .values_list("student_id").annotate(n=Count("id")))
        avg = {r["student_id"]: r for r in Attempt.objects.filter(student_id__in=sids, quiz__course__teacher=teacher,
                                                                    submitted_at__isnull=False)
               .values("student_id").annotate(s=Avg("score"), t=Avg("total"))}
        risk = []
        for e in active:
            d = done.get(e.student_id, 0)
            a = avg.get(e.student_id)
            ap = round(a["s"] * 100 / a["t"]) if a and a["t"] else None
            if d == 0 or (ap is not None and ap < 50):
                risk.append(f"{e.student.full_name}" + (f" (متوسط {ap}%)" if ap is not None else " (لم يكمل أي درس)"))
        if risk:
            lines.append("طلاب يحتاجون متابعة: " + "؛ ".join(risk[:10]))
    since = timezone.localdate() - timedelta(days=30)
    for label, model in (("المنصة", Attendance), ("السنتر", CenterAttendance)):
        qs = model.objects.filter(teacher=teacher, date__gte=since)
        n = qs.count()
        if n:
            lines.append(f"الحضور ({label}) آخر 30 يومًا: {qs.filter(present=True).count()} حضور و{qs.filter(present=False).count()} غياب من {n} تسجيل")
    text = "\n".join(lines)
    if len(text) > MAX_CHARS_TEACHER:
        text = text[:MAX_CHARS_TEACHER].rsplit("\n", 1)[0] + "\n…(تم اختصار باقي الملف)"
    return text, set(corpus.lessons)
