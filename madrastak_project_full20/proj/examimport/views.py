from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import teacher_required
from accounts.utils import serve_private
from learning.models import Choice, Question, Quiz
from learning.services import notify_students_of

from .forms import BatchUploadForm, ImportedQuestionEditForm
from .models import ImportBatch, ImportedQuestion, ImportFile
from .services import run_import_batch


def me(request):
    return request.user.teacher_profile


def owned_quiz(request, pk):
    return get_object_or_404(Quiz, pk=pk, course__teacher=me(request))


def owned_batch(request, pk):
    return get_object_or_404(ImportBatch, pk=pk, teacher=me(request))


@teacher_required
def batch_new(request, quiz_pk):
    """شاشة رفع الملف: بداية نظام «استيراد الأسئلة» (بديل الإدخال اليدوي)."""
    quiz = owned_quiz(request, quiz_pk)
    form = BatchUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        batch = ImportBatch.objects.create(teacher=me(request), quiz=quiz)
        for i, f in enumerate(form.cleaned_data["files"]):
            ImportFile.objects.create(batch=batch, file=f, order=i)
        run_import_batch(batch)
        if batch.status == "failed":
            messages.error(request, batch.error_message or "تعذّرت معالجة الملف.")
        else:
            messages.success(request, "تم تحليل الملف. راجع الأسئلة المستخرجة قبل إضافتها للاختبار.")
        return redirect("examimport:batch_review", pk=batch.pk)
    return render(request, "examimport/batch_new.html", {"form": form, "quiz": quiz})


@teacher_required
def batch_review(request, pk):
    """شاشة المراجعة: تعرض كل سؤال بجانب نصه الأصلي، مع حالته ودرجة الثقة به."""
    batch = owned_batch(request, pk)
    questions = batch.questions.prefetch_related("choices")
    return render(request, "examimport/batch_review.html", {
        "batch": batch, "questions": questions, "counts": batch.counts,
    })


@teacher_required
@require_POST
def batch_reprocess(request, pk):
    batch = owned_batch(request, pk)
    run_import_batch(batch)
    messages.info(request, "أُعيدت معالجة الملف من جديد.")
    return redirect("examimport:batch_review", pk=batch.pk)


@teacher_required
@require_POST
def batch_apply(request, pk):
    """يحوّل الأسئلة المستوردة (غير المستبعدة والصالحة) إلى أسئلة حقيقية داخل الاختبار."""
    batch = owned_batch(request, pk)
    if batch.status == "applied":
        messages.info(request, "سبق أن أُضيفت أسئلة هذه الدفعة إلى الاختبار.")
        return redirect("teacher:quiz_detail", pk=batch.quiz_id)

    candidates = list(batch.questions.exclude(status="rejected").prefetch_related("choices"))
    added = 0
    for iq in candidates:
        choices = list(iq.choices.all())
        if not iq.text.strip() or len(choices) < 2 or not iq.correct_index or iq.correct_index > len(choices):
            continue
        q = Question.objects.create(quiz=batch.quiz, text=iq.text, qtype=iq.qtype)
        for i, c in enumerate(choices, start=1):
            Choice.objects.create(question=q, text=c.text or f"اختيار {i}", is_correct=(i == iq.correct_index), order=i)
        added += 1

    batch.status = "applied"
    batch.applied_at = timezone.now()
    batch.save(update_fields=["status", "applied_at"])

    skipped = len(candidates) - added
    quiz = batch.quiz
    # زر «إضافة الاختبار للطلاب» يعني ما يقوله حرفيًا: بمجرد اعتماد الأسئلة يجب أن
    # يظهر الاختبار فورًا للطلاب، تمامًا كما لو ضغط المدرّس زر «نشر الاختبار» يدويًا.
    # بدون هذا كان الاختبار يبقى بحالة «مسودة» ولا يظهر أبدًا في لوحة الطالب.
    if added and quiz.status not in ("published",):
        quiz.status = "published"
        quiz.publish_at = None
        quiz.save(update_fields=["status", "publish_at"])
        label = "واجب" if quiz.kind == "homework" else "اختبار"
        notify_students_of(quiz.teacher, f"{label} جديد: {quiz.title}", quiz.location,
                            f"/student/t/{quiz.teacher.teacher_code}/", "quiz")

    if added and not skipped:
        messages.success(request, f"تمت إضافة {added} سؤالًا ونشر الاختبار للطلاب.")
    elif added and skipped:
        messages.warning(request, f"تمت إضافة {added} سؤالًا ونُشر الاختبار. تم تجاوز {skipped} سؤالًا غير مكتمل (راجعها وعدّلها ثم أعد المحاولة).")
    else:
        messages.warning(request, "لم تتم إضافة أي سؤال، ولذلك لم يُنشر الاختبار بعد. راجع الأسئلة وحدّد نصًا وإجابة صحيحة لكل سؤال قبل الإضافة.")
    return redirect("teacher:quiz_detail", pk=batch.quiz_id)


@teacher_required
def question_edit(request, pk):
    """تعديل يدوي لسؤال مستورَد قبل اعتماده، مع عرض النص الأصلي بجانبه للمقارنة."""
    iq = get_object_or_404(ImportedQuestion, pk=pk, batch__teacher=me(request))
    form = ImportedQuestionEditForm(request.POST or None, iq=iq)
    if request.method == "POST" and form.is_valid():
        form.save(iq)
        messages.success(request, "تم حفظ التعديلات.")
        return redirect("examimport:batch_review", pk=iq.batch_id)
    return render(request, "examimport/question_edit.html", {"form": form, "iq": iq})


@teacher_required
@require_POST
def question_reject(request, pk):
    """استبعاد سؤال من الاستيراد (لن يُضاف عند اعتماد الدفعة)، مع إمكانية التراجع."""
    iq = get_object_or_404(ImportedQuestion, pk=pk, batch__teacher=me(request))
    iq.status = "needs_review" if iq.status == "rejected" else "rejected"
    iq.save(update_fields=["status"])
    return redirect("examimport:batch_review", pk=iq.batch_id)


@teacher_required
def batch_file(request, pk):
    """يعرض/يُحمّل الملف الأصلي المرفوع (وسيلة عرض «الملف الأصلي بجانب المستخرَج»)."""
    f = get_object_or_404(ImportFile, pk=pk, batch__teacher=me(request))
    return serve_private(request, f.file, download_name=f.file.name.rsplit("/", 1)[-1])
