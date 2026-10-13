"""صفحات مساندة للمدرّس في الواجهة الجديدة.

هذه الصفحات إضافات جديدة فوق النظام القائم: تنشئ/تقرأ من نفس الجداول (Course, Unit, Lesson, LessonSection, Quiz,
Question, Choice, VocabularyWord, ImportBatch...) وباستخدام نفس قواعد الملكية والنشر والإشعارات، دون تعديل أي منطق قائم.
"""
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import teacher_required
from accounts.pagination import paginate
from billing.models import Enrollment
from examimport.forms import BatchUploadForm
from examimport.models import ImportBatch, ImportFile
from examimport.services import run_import_batch
from learning import services as svc
from learning.models import (Attempt, Choice, Course, ErrorRecord, Lesson, LessonSection, Question, Quiz, Unit,
                             VocabularyWord)
from learning.services import notify_students_of

from . import ai, vocab_extract
from .forms import QuizWizardForm, UnitWizardForm


def me(request):
    return request.user.teacher_profile


def _tree(t):
    courses = []
    for c in Course.objects.filter(teacher=t).order_by("order", "id"):
        units = []
        for u in c.units.all():
            units.append({"u": u, "lessons": list(u.lessons.all())})
        courses.append({"c": c, "units": units})
    return courses


@teacher_required
def picker(request, kind):
    """اختيار الدرس (للمفردات/الشرح الذكي) قبل فتح الصفحة المخصصة له."""
    titles = {"vocab": ("إضافة مفردات الدرس", "اختر الدرس الذي ستضيف إليه المفردات", "uikit:ai_vocab"),
              "script": ("النصوص والشرح", "اختر الدرس الذي ستضيف إليه الشرح", "uikit:ai_script")}
    if kind not in titles:
        return redirect("teacher:courses")
    title, sub, url_name = titles[kind]
    tree = _tree(me(request))
    if not any(u["lessons"] for c in tree for u in c["units"]):
        messages.info(request, "لا توجد دروس بعد. أنشئ وحدة ودرسًا أولًا.")
        return redirect(reverse("uikit:unit_wizard") + "?mode=lesson")
    return render(request, "teacher/picker.html", {"tree": tree, "title": title, "sub": sub, "url_name": url_name, "kind": kind})


# ------------------------------------------------------------------ معالج الوحدة + الدروس
@teacher_required
def unit_wizard(request):
    t = me(request)
    mode = "lesson" if (request.GET.get("mode") or request.POST.get("mode")) == "lesson" else "unit"
    if not Course.objects.filter(teacher=t).exists():
        messages.info(request, "أنشئ دورة أولًا ثم أضف إليها الوحدات والدروس.")
        return redirect("teacher:course_new")
    initial = {}
    if request.GET.get("course", "").isdigit():
        initial["course"] = int(request.GET["course"])
    if request.GET.get("unit", "").isdigit():
        un = Unit.objects.filter(pk=int(request.GET["unit"]), course__teacher=t).first()
        if un:
            initial.update({"course": un.course_id, "unit_mode": "existing", "unit_existing": un.pk})
    elif mode == "lesson":
        initial.setdefault("unit_mode", "existing" if Unit.objects.filter(course__teacher=t).exists() else "new")
    form = UnitWizardForm(request.POST or None, teacher=t, initial=initial)
    titles = request.POST.getlist("lesson_title")
    summaries = request.POST.getlist("lesson_summary")
    summaries += [""] * (len(titles) - len(summaries))
    lesson_rows = [{"title": a.strip(), "summary": b.strip()} for a, b in zip(titles, summaries)]
    if request.method == "POST" and form.is_valid():
        rows = [r for r in lesson_rows if r["title"]]
        if mode == "lesson" and not rows:
            form.add_error(None, "أضف درسًا واحدًا على الأقل (اكتب اسم الدرس).")
        else:
            cd = form.cleaned_data
            with transaction.atomic():
                if cd["unit_mode"] == "existing":
                    unit, created_unit = cd["unit_existing"], False
                else:
                    unit = Unit.objects.create(course=cd["course"], title=cd["unit_title"].strip(), description=cd["unit_description"].strip(),
                                               order=cd["course"].units.count() + 1,
                                               status="published" if cd["publish_unit"] else "draft")
                    created_unit = True
                base = unit.lessons.count()
                for i, r in enumerate(rows, start=1):
                    Lesson.objects.create(unit=unit, title=r["title"][:160], summary=r["summary"], order=base + i, status="draft")
            if created_unit and unit.status == "published":
                notify_students_of(t, f"وحدة جديدة: {unit.title}", unit.course.title, f"/student/t/{t.teacher_code}/unit/{unit.pk}/")
            parts = []
            if created_unit:
                parts.append("الوحدة")
            if rows:
                parts.append(f"{len(rows)} درس")
            messages.success(request, "تم إنشاء " + " و".join(parts) + " بنجاح. أضف الآن شرح الدرس والمفردات ثم انشر الدرس ليظهر للطلاب.")
            return redirect("teacher:unit_detail", pk=unit.pk)
    if not lesson_rows:
        lesson_rows = [{"title": "", "summary": ""}]
    unit_map = {}
    for u in Unit.objects.filter(course__teacher=t).order_by("course_id", "order", "id"):
        unit_map.setdefault(u.course_id, []).append({"id": u.pk, "title": u.title})
    return render(request, "teacher/unit_wizard.html", {"form": form, "mode": mode, "lesson_rows": lesson_rows, "unit_map": unit_map})


# ------------------------------------------------------------------ معالج الاختبار
def _resolve_target(t, target):
    kind, _, pk = (target or "").partition(":")
    if kind == "lesson":
        l = get_object_or_404(Lesson, pk=pk, unit__course__teacher=t)
        return l.unit.course, l.unit, l
    if kind == "unit":
        u = get_object_or_404(Unit, pk=pk, course__teacher=t)
        return u.course, u, None
    c = get_object_or_404(Course, pk=pk, teacher=t)
    return c, None, None


@teacher_required
def quiz_wizard(request):
    t = me(request)
    if not Course.objects.filter(teacher=t).exists():
        messages.info(request, "أنشئ دورة أولًا ثم أضف إليها الاختبارات.")
        return redirect("teacher:course_new")
    initial = {"kind": request.GET.get("kind") if request.GET.get("kind") in ("quiz", "exam", "homework") else "quiz"}
    for key in ("lesson", "unit", "course"):
        if request.GET.get(key, "").isdigit():
            initial["target"] = f"{key}:{request.GET[key]}"
            break
    form = QuizWizardForm(request.POST or None, request.FILES or None, teacher=t, initial=initial)
    files_error = ""
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        src = cd["question_source"]
        files_form = None
        if src in ("pdf", "image"):
            files_form = BatchUploadForm(request.POST, request.FILES)
            if not files_form.is_valid():
                files_error = " ".join(e for errs in files_form.errors.values() for e in errs)
        if not files_error:
            course, unit, lesson = _resolve_target(t, cd["target"])
            with transaction.atomic():
                quiz = Quiz(course=course, unit=unit, lesson=lesson, title=cd["title"].strip(), description=(cd["description"] or "").strip(),
                            kind=cd["kind"], duration_minutes=cd["duration_minutes"] or 0, max_attempts=cd["max_attempts"] or 0,
                            deadline=cd["deadline"] if cd["kind"] == "homework" else None, status="draft")
                quiz.save()
                for order, q in enumerate(cd["questions"], start=1):
                    texts = [o for _, o in q["options"]]
                    is_tf = len(texts) == 2 and {x.strip().lower() for x in texts} <= {"true", "false", "صح", "خطأ", "صحيح", "خاطئ"}
                    qtype = "essay" if q.get("type") == "essay" else ("tf" if is_tf else "mcq")
                    qq = Question.objects.create(quiz=quiz, text=q["text"], qtype=qtype, hint=q["hint"],
                                                 explanation=q["explanation"], order=order)
                    for i, (idx, text) in enumerate(q["options"], start=1):
                        Choice.objects.create(question=qq, text=text, is_correct=(idx == q["correct"]), order=i)
                batch = None
                if files_form is not None:
                    batch = ImportBatch.objects.create(teacher=t, quiz=quiz)
                    for i, f in enumerate(files_form.cleaned_data["files"]):
                        ImportFile.objects.create(batch=batch, file=f, order=i)
            if batch is not None:
                run_import_batch(batch)
                if batch.status == "failed":
                    messages.error(request, batch.error_message or "تعذّرت معالجة الملف.")
                else:
                    messages.success(request, "تم إنشاء الاختبار وتحليل الملف. راجع الأسئلة المستخرجة قبل إضافتها للاختبار.")
                return redirect("examimport:batch_review", pk=batch.pk)
            if cd["publish"] and quiz.questions.exists():
                quiz.status = "published"
                quiz.save()
                label = "واجب" if quiz.kind == "homework" else "اختبار"
                notify_students_of(t, f"{label} جديد: {quiz.title}", quiz.location, f"/student/t/{t.teacher_code}/", "quiz")
            messages.success(request, "تم حفظ الاختبار" + (" ونشره للطلاب." if quiz.status == "published" else " كمسودة. انشره من صفحة الاختبار عندما تكون جاهزًا."))
            return redirect("teacher:quiz_detail", pk=quiz.pk)
    return render(request, "teacher/quiz_wizard.html", {"form": form, "files_error": files_error,
                                                        "questions_json": (request.POST.get("questions_json") or "[]") if request.method == "POST" else "[]"})


# ------------------------------------------------------------------ إدارة المحتوى الحالي (جدول مجمّع)
@teacher_required
def content_manage(request):
    """شاشة تجميعية بسيطة لكل محتوى المدرّس (دروس + اختبارات + نصوص الشرح) — قراءة فقط من الجداول القائمة."""
    t = me(request)
    typ = request.GET.get("type", "all")
    q = request.GET.get("q", "").strip()
    rows = []
    if typ in ("all", "lesson"):
        for l in Lesson.objects.filter(unit__course__teacher=t).select_related("unit__course"):
            rows.append({"type": "lesson", "label": "محاضرة", "pk": l.pk, "title": l.title, "unit": l.unit.title, "course": l.unit.course.title,
                         "date": l.updated_at, "status": l.status_label, "open": f"/teacher/lesson/{l.pk}/"})
    if typ in ("all", "quiz"):
        for z in Quiz.objects.filter(course__teacher=t).select_related("unit", "lesson__unit", "course"):
            unit_title = z.unit.title if z.unit_id else (z.lesson.unit.title if z.lesson_id else "—")
            rows.append({"type": "quiz", "label": z.get_kind_display(), "pk": z.pk, "title": z.title, "unit": unit_title, "course": z.course.title,
                         "date": z.created_at, "status": z.status_label, "open": f"/teacher/quiz/{z.pk}/"})
    if typ in ("all", "text"):
        for s in LessonSection.objects.filter(lesson__unit__course__teacher=t).select_related("lesson__unit__course"):
            rows.append({"type": "text", "label": "نص", "pk": s.pk, "title": f"{s.title} — {s.lesson.title}", "unit": s.lesson.unit.title,
                         "course": s.lesson.unit.course.title, "date": s.lesson.updated_at, "status": s.get_kind_display(),
                         "open": f"/teacher/section/{s.pk}/edit/"})
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["title"].lower() or ql in r["unit"].lower() or ql in r["course"].lower()]
    rows.sort(key=lambda r: r["date"], reverse=True)
    counts = {
        "all": Lesson.objects.filter(unit__course__teacher=t).count() + Quiz.objects.filter(course__teacher=t).count() + LessonSection.objects.filter(lesson__unit__course__teacher=t).count(),
        "lesson": Lesson.objects.filter(unit__course__teacher=t).count(),
        "quiz": Quiz.objects.filter(course__teacher=t).count(),
        "text": LessonSection.objects.filter(lesson__unit__course__teacher=t).count(),
    }
    page, qstr = paginate(request, rows, 15)
    return render(request, "teacher/manage_content.html", {"page": page, "qs": qstr, "type": typ, "q": q, "counts": counts, "total": len(rows)})


@teacher_required
@require_POST
def content_delete(request):
    t = me(request)
    kind, pk = request.POST.get("kind"), request.POST.get("pk", "")
    if not pk.isdigit():
        return redirect("uikit:content_manage")
    pk = int(pk)
    if kind == "lesson":
        get_object_or_404(Lesson, pk=pk, unit__course__teacher=t).delete()
    elif kind == "quiz":
        get_object_or_404(Quiz, pk=pk, course__teacher=t).delete()
    elif kind == "text":
        get_object_or_404(LessonSection, pk=pk, lesson__unit__course__teacher=t).delete()
    else:
        return redirect("uikit:content_manage")
    messages.success(request, "تم حذف العنصر.")
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/teacher/") else "uikit:content_manage")


# ------------------------------------------------------------------ الذكاء الاصطناعي: المفردات
@teacher_required
def ai_vocab(request, lesson_pk=None):
    if lesson_pk is None:
        return picker(request, "vocab")
    t = me(request)
    lesson = get_object_or_404(Lesson.objects.select_related("unit__course"), pk=lesson_pk, unit__course__teacher=t)
    ctx = {"lesson": lesson, "existing": lesson.vocabulary.count(), "provider": ai.provider_label(), "stage": "upload",
           "rows": [], "max_mb": getattr(settings, "MAX_IMPORT_FILE_MB", 15)}
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "extract":
            ctx["raw_text"] = request.POST.get("raw_text", "")
            files = request.FILES.getlist("files")
            pasted = request.POST.get("raw_text", "").strip()
            err = ""
            if files:
                fform = BatchUploadForm(request.POST, request.FILES)
                if not fform.is_valid():
                    err = " ".join(e for errs in fform.errors.values() for e in errs)
            if not err and (files or pasted):
                try:
                    rows, note = vocab_extract.extract_vocabulary(files, pasted)
                except RuntimeError as e:
                    err = str(e)
                except Exception:
                    err = "تعذّرت قراءة الملف. تأكد أنه PDF أو صورة واضحة."
            if err:
                messages.error(request, err)
            elif not (files or pasted):
                messages.error(request, "ارفع ملف PDF/صورة يحتوي على الكلمات، أو الصق الكلمات في المربع.")
            elif not rows:
                messages.warning(request, "لم يتمكّن النظام من استخراج كلمات إنجليزية من هذا المحتوى. جرّب ملفًا أوضح أو الصق الكلمات يدويًا.")
            else:
                ctx.update(stage="review", rows=rows, note=note,
                           enrich_url=reverse("uikit:ai_vocab_enrich", args=[lesson.pk]),
                           auto_enrich=ctx["provider"] != "heuristic")
        elif action == "save":
            words = request.POST.getlist("word")
            meanings = request.POST.getlist("meaning")
            examples = request.POST.getlist("example")
            ex_trs = request.POST.getlist("example_translation")
            meanings += [""] * (len(words) - len(meanings))
            examples += [""] * (len(words) - len(examples))
            ex_trs += [""] * (len(words) - len(ex_trs))
            replace = request.POST.get("mode") == "replace"
            added = 0
            # حماية: لا نحذف المفردات الحالية في وضع «الاستبدال» إلا إن وُجدت كلمة صالحة واحدة على الأقل
            replace = replace and any(w.strip() for w in words)
            with transaction.atomic():
                if replace:
                    lesson.vocabulary.all().delete()
                have = {w.lower() for w in lesson.vocabulary.values_list("word", flat=True)}
                order = lesson.vocabulary.count()
                for w, m, e, et in zip(words, meanings, examples, ex_trs):
                    w = w.strip()[:100]
                    if not w or w.lower() in have:
                        continue
                    have.add(w.lower())
                    order += 1
                    VocabularyWord.objects.create(lesson=lesson, word=w, meaning=(m.strip() or "—")[:200], example=e.strip()[:300],
                                                  example_translation=et.strip()[:300], order=order)
                    added += 1
            if added:
                messages.success(request, f"تمت إضافة {added} كلمة إلى مفردات الدرس، وستظهر للطلاب في صفحة المفردات.")
            else:
                messages.warning(request, "لم تُضف أي كلمة (الكلمات مكرّرة أو فارغة).")
            return redirect("teacher:lesson_detail", pk=lesson.pk)
    return render(request, "teacher/vocab_import.html", ctx)


@teacher_required
@require_POST
def ai_vocab_enrich(request, lesson_pk):
    """يولّد (بالذكاء الاصطناعي) جملة مثال + ترجمتها لدفعة من الكلمات، ويصلح الترجمات المشكوك فيها. لا يحفظ شيئًا."""
    get_object_or_404(Lesson, pk=lesson_pk, unit__course__teacher=me(request))
    try:
        items = json.loads(request.body.decode("utf-8")).get("items", [])
    except (ValueError, AttributeError):
        return JsonResponse({"ok": False, "error": "طلب غير صالح."}, status=400)
    if not isinstance(items, list) or not items:
        return JsonResponse({"ok": False, "error": "لا توجد كلمات."}, status=400)
    results, err = vocab_extract.enrich(items)
    if err == "no_key":
        return JsonResponse({"ok": False, "error": "لم يُضبط مفتاح الذكاء الاصطناعي بعد."}, status=400)
    return JsonResponse({"ok": bool(results), "items": results, "error": err})


# ------------------------------------------------------------------ الذكاء الاصطناعي: هيكلة شرح الدرس
@teacher_required
def ai_script(request, lesson_pk=None):
    if lesson_pk is None:
        return picker(request, "script")
    t = me(request)
    lesson = get_object_or_404(Lesson.objects.select_related("unit__course"), pk=lesson_pk, unit__course__teacher=t)
    ctx = {"lesson": lesson, "existing": lesson.sections.count(), "provider": ai.provider_label(), "stage": "input",
           "kinds": LessonSection.KINDS, "text": "", "tab": "text"}
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "analyze":
            text = request.POST.get("lecture_text", "").strip()
            files = request.FILES.getlist("files")
            raw, err = text, ""
            ctx["tab"] = "file" if (files and not text) else "text"
            if files:
                fform = BatchUploadForm(request.POST, request.FILES)
                if not fform.is_valid():
                    err = " ".join(e for errs in fform.errors.values() for e in errs)
                else:
                    try:
                        extracted = ai.extract_text_from_uploads(files)
                        raw = (text + "\n\n" + extracted).strip()
                    except RuntimeError as e:
                        err = str(e)
                    except Exception:
                        err = "تعذّرت قراءة الملف. تأكد أنه PDF أو صورة واضحة."
            ctx["text"] = text
            if err:
                messages.error(request, err)
            elif not raw:
                messages.error(request, "اكتب أو الصق نص الشرح، أو ارفع ملفًا/صورة.")
            else:
                title, sections, used, note = ai.build_lesson_structure(raw)
                if not sections:
                    messages.warning(request, "لم يتمكّن النظام من هيكلة هذا النص. تأكد أنه يحتوي على شرح فعلي.")
                    ctx["text"] = raw
                else:
                    ctx.update(stage="review", sections=sections, ai_title=title, used=used, note=note)
        elif action == "save":
            kinds = request.POST.getlist("kind")
            titles = request.POST.getlist("title")
            contents = request.POST.getlist("content")
            allowed = {k for k, _ in LessonSection.KINDS}
            n = min(len(kinds), len(titles), len(contents))
            items = []
            for i in range(n):
                body = contents[i].strip()
                if not body:
                    continue
                items.append({"kind": kinds[i] if kinds[i] in allowed else "text", "title": (titles[i].strip() or "قسم")[:160], "content": body})
            if not items:
                messages.error(request, "لا توجد أقسام لحفظها.")
                return redirect("uikit:ai_script", lesson_pk=lesson.pk)
            with transaction.atomic():
                if request.POST.get("mode") == "replace":
                    lesson.sections.all().delete()
                order = lesson.sections.count()
                for it in items:
                    order += 1
                    LessonSection.objects.create(lesson=lesson, title=it["title"], kind=it["kind"], content=it["content"], order=order)
            msg = f"تم حفظ {len(items)} قسمًا في شرح الدرس، وسيراها الطالب في صفحة الشرح."
            if request.POST.get("publish") and lesson.status != "published":
                lesson.status, lesson.publish_at = "published", None
                lesson.save()
                notify_students_of(lesson.teacher, f"درس جديد: {lesson.title}", lesson.unit.title, f"/student/t/{lesson.teacher.teacher_code}/lesson/{lesson.pk}/")
                msg += " وتم نشر الدرس وإشعار الطلاب."
            messages.success(request, msg)
            return redirect("teacher:lesson_detail", pk=lesson.pk)
    return render(request, "teacher/script_import.html", ctx)


# ------------------------------------------------------------------ الطلاب: المركز والتقارير والبحث
@teacher_required
def students_hub(request):
    t = me(request)
    now = timezone.now()
    all_e = Enrollment.objects.filter(teacher=t)
    stats = {
        "total": all_e.count(),
        "active": all_e.filter(status="active", expires_at__gt=now).count(),
        "expired": all_e.filter(status="active", expires_at__lte=now).count(),
        "pending": all_e.filter(status="pending").count(),
        "new_month": all_e.filter(created_at__gte=now - timedelta(days=30)).count(),
    }
    falling = (all_e.filter(status="active", expires_at__gt=now).select_related("student")
               .annotate(errs=Count("student__errors", filter=Q(student__errors__question__quiz__course__teacher=t, student__errors__resolved=False)))
               .order_by("-errs")[:6])
    agg = (Attempt.objects.filter(quiz__course__teacher=t, submitted_at__isnull=False)
           .values("student_id", "student__full_name").annotate(s=Sum("score"), tot=Sum("total"), n=Count("id")))
    top = sorted(({"name": a["student__full_name"], "avg": round(a["s"] * 100 / a["tot"]) if a["tot"] else 0, "n": a["n"]} for a in agg),
                 key=lambda r: (-r["avg"], -r["n"]))[:6]
    recent = all_e.select_related("student").order_by("-created_at")[:6]
    return render(request, "teacher/students_hub.html", {"stats": stats, "falling": falling, "top": top, "recent": recent})


@teacher_required
def student_reports(request):
    t = me(request)
    qs = Enrollment.objects.filter(teacher=t).select_related("student", "student__student_profile")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(student__full_name__icontains=q) | Q(student__phone__icontains=q))
    page, qstr = paginate(request, qs.order_by("-created_at"), 15)
    return render(request, "teacher/student_reports.html", {"page": page, "qs": qstr, "q": q})


@teacher_required
def search(request):
    t = me(request)
    q = request.GET.get("q", "").strip()
    students, courses, lessons, quizzes = [], [], [], []
    if q:
        students = list(Enrollment.objects.filter(teacher=t).filter(Q(student__full_name__icontains=q) | Q(student__phone__icontains=q)).select_related("student")[:15])
        courses = list(Course.objects.filter(teacher=t).filter(Q(title__icontains=q) | Q(description__icontains=q))[:10])
        lessons = list(Lesson.objects.filter(unit__course__teacher=t).filter(Q(title__icontains=q) | Q(summary__icontains=q)).select_related("unit__course")[:15])
        quizzes = list(Quiz.objects.filter(course__teacher=t).filter(Q(title__icontains=q) | Q(description__icontains=q)).select_related("course")[:15])
    return render(request, "teacher/search.html", {"q": q, "students": students, "courses": courses, "lessons": lessons, "quizzes": quizzes})
