"""نظام استيراد الاختبارات: يحوّل ملف PDF/صورة رفعه المدرّس إلى أسئلة إلكترونية منظّمة
تنتظر مراجعته قبل إضافتها فعليًا إلى الاختبار (Quiz) في تطبيق learning."""
from django.db import models

from accounts.models import TeacherProfile, private_storage
from learning.models import Question, Quiz

SOURCE_TYPES = [
    ("pdf_text", "PDF نصي"),
    ("pdf_scanned", "PDF ممسوح ضوئيًا (صور)"),
    ("image", "صورة واحدة"),
    ("images", "مجموعة صور"),
]

BATCH_STATUS = [
    ("uploaded", "تم الرفع"),
    ("processing", "قيد المعالجة"),
    ("ready", "جاهز للمراجعة"),
    ("failed", "فشلت المعالجة"),
    ("applied", "تم إنشاء الاختبار"),
]

QUESTION_STATUS = [
    ("ready", "جاهز"),
    ("needs_review", "يحتاج مراجعة"),
    ("unparsed", "تعذّر تحليله"),
    ("rejected", "مستبعد"),
]


class ImportBatch(models.Model):
    """دفعة استيراد واحدة: ملف أو مجموعة ملفات رفعها المدرّس لاستخراج أسئلة منها."""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="import_batches")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="import_batches",
                              help_text="الاختبار الذي ستُضاف إليه الأسئلة المستوردة بعد المراجعة")
    source_type = models.CharField("نوع الملف", max_length=12, choices=SOURCE_TYPES, default="pdf_text")
    status = models.CharField("الحالة", max_length=12, choices=BATCH_STATUS, default="uploaded")
    error_message = models.TextField("رسالة الخطأ", blank=True)
    raw_text = models.TextField("النص المستخرج (للمراجعة والتشخيص)", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"استيراد #{self.pk} — {self.quiz.title}"

    @property
    def counts(self):
        qs = self.questions
        return {
            "total": qs.count(),
            "ready": qs.filter(status="ready").count(),
            "needs_review": qs.filter(status="needs_review").count(),
            "unparsed": qs.filter(status="unparsed").count(),
            "rejected": qs.filter(status="rejected").count(),
        }


class ImportFile(models.Model):
    """ملف واحد ضمن دفعة الاستيراد (صفحة/صورة أو ملف PDF كامل)."""
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="files")
    file = models.FileField("الملف", upload_to="imports/", storage=private_storage)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]


class ImportedQuestion(models.Model):
    """سؤال مستخرَج من الملف بانتظار مراجعة المدرّس، مستقل عن نموذج Question الفعلي
    حتى يوافق المدرّس على المحتوى ويتم تحويله إلى سؤال حقيقي داخل الاختبار."""
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveIntegerField(default=0)
    raw_block = models.TextField("النص الأصلي كما ورد في الملف", blank=True)
    text = models.TextField("نص السؤال المستخرج", blank=True)
    qtype = models.CharField("نوع السؤال", max_length=5, choices=Question.TYPES, default="mcq")
    correct_index = models.PositiveSmallIntegerField("رقم الاختيار الصحيح (1 = الأول)", null=True, blank=True)
    confidence = models.PositiveSmallIntegerField("درجة الثقة بدقة التحليل (%)", default=0)
    status = models.CharField("الحالة", max_length=14, choices=QUESTION_STATUS, default="needs_review")
    notes = models.CharField("ملاحظات المراجعة", max_length=300, blank=True)
    page_number = models.PositiveIntegerField("رقم الصفحة التقريبي", null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return (self.text or self.raw_block)[:60]


class ImportedChoice(models.Model):
    """اختيار واحد ضمن سؤال مستورَد."""
    question = models.ForeignKey(ImportedQuestion, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=400, blank=True)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
