"""المساعد الذكي — النماذج.

  * AIProviderKey    : مفاتيح مزوّدي الذكاء الاصطناعي (يديرها المالك) مع توزيع الحمل بينها.
  * AssistantSetting : إعدادات عامة (تشغيل/إيقاف، حدّ الرسائل في الساعة).
  * AssistantThread / AssistantMessage : محادثات الطالب أو المدرّس مع المساعد.
  * LessonFileText   : نص مستخرج من ملفات PDF المرفقة بالدروس (مخزّن مؤقتًا لتسريع البحث في المنهج).
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import private_storage

PROVIDERS = [
    ("groq", "Groq"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic (Claude)"),
    ("gemini", "Google Gemini"),
    ("openrouter", "OpenRouter"),
    ("deepseek", "DeepSeek"),
    ("custom", "مزوّد آخر (أدخل الرابط الكامل)"),
]

# القيم الافتراضية لكل مزوّد: الرابط الأساسي + أسلوب الواجهة + نموذج مقترح.
# النموذج قابل للتعديل لكل مفتاح لأن أسماء النماذج تتغيّر باستمرار عند المزوّدين.
PROVIDER_DEFAULTS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "style": "openai", "model": "openai/gpt-oss-120b"},
    "openai": {"base_url": "https://api.openai.com/v1", "style": "openai", "model": "gpt-4o-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "style": "anthropic", "model": "claude-sonnet-4-6"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "style": "openai", "model": "gemini-2.0-flash"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "style": "openai", "model": ""},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "style": "openai", "model": "deepseek-chat"},
    "custom": {"base_url": "", "style": "openai", "model": ""},
}


class AIProviderKey(models.Model):
    name = models.CharField("اسم المفتاح / المزوّد", max_length=80,
                            help_text="اسم تعريفي يظهر لك فقط، مثال: Groq – حساب 1")
    provider = models.CharField("المزوّد", max_length=12, choices=PROVIDERS, default="groq")
    base_url = models.CharField("الرابط (اختياري)", max_length=300, blank=True,
                                help_text="اتركه فارغًا لمزوّدي القائمة. للمزوّد الآخر أدخل الرابط الكامل (مثال: https://api.example.com/v1/chat/completions).")
    api_key = models.CharField("مفتاح API", max_length=400)
    model = models.CharField("اسم النموذج", max_length=120, blank=True,
                             help_text="اتركه فارغًا لاستخدام النموذج الافتراضي للمزوّد.")
    is_active = models.BooleanField("مفعّل", default=True)
    daily_limit = models.PositiveIntegerField("حدّ الطلبات اليومي (0 = بلا حدّ)", default=0)

    # إحصاءات وحالة التشغيل
    used_today = models.PositiveIntegerField(default=0)
    used_date = models.DateField(null=True, blank=True)
    total_requests = models.PositiveIntegerField(default=0)
    total_failures = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "مفتاح ذكاء اصطناعي"

    def __str__(self):
        return self.name

    # ---- خصائص مساعدة
    @property
    def defaults(self):
        return PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["custom"])

    @property
    def effective_model(self):
        return (self.model or "").strip() or self.defaults["model"]

    @property
    def masked_key(self):
        k = self.api_key or ""
        return ("•" * 8 + k[-4:]) if len(k) > 8 else "•" * len(k)

    @property
    def in_cooldown(self):
        return bool(self.cooldown_until and self.cooldown_until > timezone.now())

    def used_today_count(self):
        return self.used_today if self.used_date == timezone.localdate() else 0

    @property
    def over_daily_limit(self):
        return bool(self.daily_limit and self.used_today_count() >= self.daily_limit)

    @property
    def state(self):
        """active | cooldown | limit | off"""
        if not self.is_active:
            return "off"
        if self.over_daily_limit:
            return "limit"
        if self.in_cooldown:
            return "cooldown"
        return "active"


class AssistantSetting(models.Model):
    is_enabled = models.BooleanField("تشغيل المساعد الذكي", default=True)
    hourly_limit = models.PositiveIntegerField("الحدّ الأقصى لرسائل المستخدم الواحد في الساعة", default=30)

    class Meta:
        verbose_name = "إعدادات المساعد"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AssistantThread(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_threads")
    title = models.CharField(max_length=120, default="محادثة جديدة")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]


class AssistantMessage(models.Model):
    ROLES = [("user", "المستخدم"), ("assistant", "المساعد")]
    thread = models.ForeignKey(AssistantThread, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLES)
    content = models.TextField()
    mode = models.CharField(max_length=12, blank=True)  # curriculum | personal | not_found | system
    sources = models.JSONField(default=list, blank=True)   # [{"title":..., "url":...}]
    lessons = models.JSONField(default=list, blank=True)  # دروس مقترحة [{"title":..., "url":...}]
    meta = models.JSONField(default=dict, blank=True)     # وكيل المدرّس: {"links": [...], "actions": [...], "steps": [...]}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class LessonFileText(models.Model):
    """نص مستخرج من ملف مرفق بدرس (PDF/نصي) لاستخدامه في البحث داخل المنهج."""
    lesson_file = models.OneToOneField("learning.LessonFile", on_delete=models.CASCADE, related_name="extracted")
    text = models.TextField(blank=True)
    file_size = models.PositiveIntegerField(default=0)
    extracted_at = models.DateTimeField(auto_now=True)


class QuestionExplanation(models.Model):
    """شرح موسّع لكل سؤال بالعامية المصرية، يُولَّد مرة واحدة بالذكاء الاصطناعي ثم يراه كل الطلاب.
    source_hash: بصمة نص السؤال وخياراته وجزء الشرح المرتبط به؛ لو تغيّرت أُعيد التوليد تلقائيًا."""
    STATUS = [("pending", "بانتظار التوليد"), ("generating", "قيد التوليد"), ("ready", "جاهز"), ("failed", "فشل")]
    question = models.OneToOneField("learning.Question", on_delete=models.CASCADE, related_name="ai_explanation")
    status = models.CharField(max_length=12, choices=STATUS, default="pending", db_index=True)
    content = models.JSONField(default=dict, blank=True)
    source_hash = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


# ------------------------------------------------------------------ وكيل المدرّس (تنفيذ المهام من المحادثة)
class CurriculumDoc(models.Model):
    """ملف منهج كامل (PDF) يرفعه المدرّس في المحادثة؛ يُستخرج نص كل صفحة في الخلفية ثم يبحث فيه الوكيل ويقرأ منه."""
    STATUS = [("processing", "قيد القراءة"), ("ready", "جاهز"), ("failed", "فشل")]
    teacher = models.ForeignKey("accounts.TeacherProfile", on_delete=models.CASCADE, related_name="curriculum_docs")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="curriculum/", storage=private_storage)
    file_size = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    pages_done = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=STATUS, default="processing")
    error = models.CharField(max_length=300, blank=True)
    used_ocr = models.BooleanField(default=False)
    outline = models.JSONField(default=list, blank=True)  # [{"title":..., "page":..., "level":...}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.title


class CurriculumPage(models.Model):
    doc = models.ForeignKey(CurriculumDoc, on_delete=models.CASCADE, related_name="pages")
    number = models.PositiveIntegerField()
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["number"]
        unique_together = ("doc", "number")


class AgentRun(models.Model):
    """تشغيل واحد للوكيل (يعمل في الخلفية بينما تتابع الواجهة حالته)."""
    STATUS = [("running", "قيد التنفيذ"), ("done", "تم"), ("failed", "فشل")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_runs")
    thread = models.ForeignKey(AssistantThread, on_delete=models.CASCADE, related_name="agent_runs")
    status = models.CharField(max_length=10, choices=STATUS, default="running", db_index=True)
    steps = models.JSONField(default=list, blank=True)  # سطور تقدّم مقروءة للمدرّس
    message = models.ForeignKey(AssistantMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    error = models.CharField(max_length=300, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]


class PendingAction(models.Model):
    """إجراء حسّاس (نشر/حذف/إرسال إشعارات) يجهّزه الوكيل ولا يُنفَّذ إلا بتأكيد المدرّس بالضغط على زر في المحادثة."""
    STATUS = [("pending", "بانتظار التأكيد"), ("done", "تم التنفيذ"), ("cancelled", "أُلغي"), ("failed", "فشل")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_actions")
    thread = models.ForeignKey(AssistantThread, on_delete=models.CASCADE, related_name="pending_actions")
    kind = models.CharField(max_length=30)
    payload = models.JSONField(default=dict, blank=True)
    summary = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=STATUS, default="pending", db_index=True)
    result = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
