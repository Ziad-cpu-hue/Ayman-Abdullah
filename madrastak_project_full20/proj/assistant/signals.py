"""إطلاق توليد شروح الأسئلة تلقائيًا عند إنشاء/تعديل الأسئلة في الاختبارات المنشورة."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from learning.models import Choice, Question, Quiz

DEBOUNCE_SECONDS = 8.0  # ننتظر حفظ الخيارات المرتبطة بالسؤال قبل التوليد


def _visible_quiz(quiz):
    return quiz.status in ("published", "scheduled")


def _dirty(question_id):
    from . import explain, llm
    from .models import QuestionExplanation
    try:
        q = (Question.objects.select_related("quiz__lesson", "section__lesson")
             .prefetch_related("choices").get(pk=question_id))
    except Question.DoesNotExist:
        return
    if not _visible_quiz(q.quiz) or not explain.eligible(q) or not llm.is_configured():
        return
    row = QuestionExplanation.objects.filter(question=q).first()
    if row is not None and row.status == "ready" and row.source_hash != explain.source_hash(q):
        QuestionExplanation.objects.filter(pk=row.pk).update(status="pending", attempts=0)  # نُخفي القديم فورًا
    elif row is not None and row.status == "failed":
        QuestionExplanation.objects.filter(pk=row.pk).update(status="pending", attempts=0)
    explain.enqueue([q.id], delay=DEBOUNCE_SECONDS)


@receiver(post_save, sender=Question)
def question_saved(sender, instance, **kwargs):
    if instance.qtype != "essay":
        transaction.on_commit(lambda: _dirty(instance.pk))


@receiver(post_save, sender=Choice)
@receiver(post_delete, sender=Choice)
def choice_changed(sender, instance, **kwargs):
    qid = instance.question_id
    transaction.on_commit(lambda: _dirty(qid))


@receiver(post_save, sender=Quiz)
def quiz_saved(sender, instance, **kwargs):
    """عند نشر الاختبار نبدأ توليد شروح أسئلته التي لا شرح لها."""
    if not _visible_quiz(instance):
        return

    def run():
        from . import explain, llm
        from .models import QuestionExplanation
        if not llm.is_configured():
            return
        done = set(QuestionExplanation.objects.filter(question__quiz=instance, status__in=("ready", "failed", "generating"))
                   .values_list("question_id", flat=True))
        ids = [q.id for q in instance.questions.exclude(qtype="essay") if q.id not in done]
        if ids:
            explain.enqueue(ids, delay=DEBOUNCE_SECONDS)

    transaction.on_commit(run)
