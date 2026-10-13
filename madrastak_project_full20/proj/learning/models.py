import re

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import TeacherProfile, private_storage

User = settings.AUTH_USER_MODEL

STATUS = [("draft", "مسودة"), ("ready", "جاهز"), ("published", "منشور"), ("scheduled", "مجدول"), ("archived", "مؤرشف")]


def vis_q(prefix=""):
    """شرط ظهور عنصر للطالب: منشور، أو مجدول وحان موعده."""
    now = timezone.now()
    return Q(**{f"{prefix}status": "published"}) | Q(**{f"{prefix}status": "scheduled", f"{prefix}publish_at__lte": now})


class StatusMixin:
    @property
    def is_visible(self):
        return self.status == "published" or (self.status == "scheduled" and self.publish_at and self.publish_at <= timezone.now())

    @property
    def status_label(self):
        if self.status == "scheduled" and self.is_visible:
            return "منشور"
        return dict(STATUS).get(self.status, self.status)


class Course(StatusMixin, models.Model):
    TYPES = [("monthly", "دورة شهرية"), ("semester", "دورة فصلية"), ("free", "دورة مجانية")]
    GRADES = [(0, "كل الصفوف"), (1, "الأول الثانوي"), (2, "الثاني الثانوي"), (3, "الثالث الثانوي")]
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="courses")
    title = models.CharField("عنوان الدورة", max_length=160)
    description = models.TextField("الوصف", blank=True)
    grade = models.PositiveSmallIntegerField("الصف", choices=GRADES, default=0)
    course_type = models.CharField("نوع الدورة", max_length=10, choices=TYPES, default="semester")
    term_label = models.CharField("الفصل / الشهر", max_length=60, blank=True, help_text="مثال: الترم الأول أو شهر أكتوبر")
    cover = models.ImageField("صورة الغلاف", upload_to="courses/", blank=True)
    status = models.CharField("الحالة", max_length=10, choices=STATUS, default="draft")
    publish_at = models.DateTimeField("موعد النشر", null=True, blank=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title

    @property
    def is_free(self):
        return self.course_type == "free"


class Unit(StatusMixin, models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="units")
    title = models.CharField("عنوان الوحدة", max_length=160)
    description = models.TextField("وصف مختصر", blank=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    status = models.CharField("الحالة", max_length=10, choices=STATUS, default="draft")
    publish_at = models.DateTimeField("موعد النشر", null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title

    @property
    def teacher(self):
        return self.course.teacher


class Lesson(StatusMixin, models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField("عنوان الدرس", max_length=160)
    summary = models.TextField("ملخص الدرس", blank=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    status = models.CharField("الحالة", max_length=10, choices=STATUS, default="draft")
    publish_at = models.DateTimeField("موعد النشر", null=True, blank=True)
    is_free_preview = models.BooleanField("درس تجريبي مجاني (يفتح لغير المشتركين)", default=False)
    video_url = models.URLField("رابط الفيديو (اختياري)", blank=True)
    video_file = models.FileField("رفع فيديو (اختياري)", upload_to="videos/", storage=private_storage, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title

    @property
    def course(self):
        return self.unit.course

    @property
    def teacher(self):
        return self.unit.course.teacher

    @property
    def embed_url(self):
        u = self.video_url
        m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]{6,})", u or "")
        return f"https://www.youtube.com/embed/{m.group(1)}" if m else ""

    @property
    def reading_minutes(self):
        words = sum(len((s.content or "").split()) for s in self.sections.all())
        return max(1, round(words / 180))

    def completeness(self):
        """قائمة مكوّنات الدرس وهل أُضيفت (لمؤشرات الاكتمال عند المدرّس)."""
        return [
            ("الشرح", self.sections.exists()),
            ("الفيديو", bool(self.video_file or self.video_url)),
            ("الملفات", self.files.exists()),
            ("المفردات", self.vocabulary.exists()),
            ("الاختبار", self.quizzes.filter(kind__in=("quiz", "exam")).exists()),
            ("الواجب", self.quizzes.filter(kind="homework").exists()),
        ]


class LessonSection(models.Model):
    KINDS = [("text", "شرح"), ("definition", "تعريف"), ("rule", "قاعدة"), ("method", "طريقة الحل"),
             ("example", "مثال"), ("note", "ملاحظة مهمة"), ("mistakes", "أخطاء شائعة"), ("review", "مراجعة سريعة")]
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField("عنوان القسم", max_length=160)
    kind = models.CharField("نوع القسم", max_length=12, choices=KINDS, default="text")
    content = models.TextField("المحتوى")
    order = models.PositiveIntegerField("الترتيب", default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class LessonFile(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="files")
    title = models.CharField("اسم الملف", max_length=160)
    file = models.FileField("الملف", upload_to="lesson_files/", storage=private_storage)
    created_at = models.DateTimeField(auto_now_add=True)


class VocabularyWord(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="vocabulary")
    word = models.CharField("الكلمة", max_length=100)
    meaning = models.CharField("المعنى", max_length=200)
    example = models.CharField("جملة مثال", max_length=300, blank=True)
    example_translation = models.CharField("ترجمة جملة المثال", max_length=300, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.word


class Quiz(StatusMixin, models.Model):
    KINDS = [("quiz", "كويز الدرس"), ("exam", "اختبار"), ("homework", "واجب")]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="quizzes")
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.CASCADE, related_name="quizzes")
    lesson = models.ForeignKey(Lesson, null=True, blank=True, on_delete=models.CASCADE, related_name="quizzes")
    title = models.CharField("عنوان الاختبار", max_length=160)
    kind = models.CharField("النوع", max_length=10, choices=KINDS, default="quiz")
    description = models.TextField("تعليمات", blank=True)
    duration_minutes = models.PositiveIntegerField("الزمن بالدقائق (0 = بلا حد)", default=0)
    deadline = models.DateTimeField("آخر موعد للتسليم (للواجب)", null=True, blank=True)
    max_attempts = models.PositiveIntegerField("عدد المحاولات (0 = بلا حد)", default=0)
    status = models.CharField("الحالة", max_length=10, choices=STATUS, default="draft")
    publish_at = models.DateTimeField("موعد النشر", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.lesson_id:
            self.unit = self.lesson.unit
            self.course = self.lesson.unit.course
        elif self.unit_id:
            self.course = self.unit.course
        super().save(*args, **kwargs)

    @property
    def teacher(self):
        return self.course.teacher

    @property
    def total_points(self):
        return sum(q.points for q in self.questions.all())

    @property
    def is_closed(self):
        return bool(self.deadline and self.deadline < timezone.now())

    @property
    def location(self):
        parts = [self.course.title]
        if self.unit_id:
            parts.append(self.unit.title)
        if self.lesson_id:
            parts.append(self.lesson.title)
        return " ← ".join(parts)


class Question(models.Model):
    TYPES = [("mcq", "اختيار من متعدد"), ("tf", "صح / خطأ")]
    ALL_TYPES = TYPES + [("essay", "سؤال مقالي")]
    LEVELS = [("easy", "سهل"), ("medium", "متوسط"), ("hard", "صعب")]
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField("نص السؤال")
    image = models.ImageField("صورة (اختياري)", upload_to="questions/", blank=True)
    qtype = models.CharField("نوع السؤال", max_length=5, choices=ALL_TYPES, default="mcq")
    difficulty = models.CharField("الصعوبة", max_length=8, choices=LEVELS, default="medium")
    points = models.PositiveIntegerField("الدرجة", default=1)
    hint = models.CharField("تلميح", max_length=300, blank=True)
    explanation = models.TextField("شرح الإجابة الصحيحة", blank=True)
    section = models.ForeignKey(LessonSection, null=True, blank=True, on_delete=models.SET_NULL, related_name="questions",
                                verbose_name="القسم المرتبط من الدرس")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:60]

    @property
    def correct_choice(self):
        return next((c for c in self.choices.all() if c.is_correct), None)


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=400)
    is_correct = models.BooleanField(default=False)
    why_wrong = models.CharField("سبب الخطأ عند اختيار هذه الإجابة", max_length=400, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]


class LessonProgress(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress")
    completed = models.BooleanField(default=False)
    needs_review = models.BooleanField(default=False)
    first_viewed = models.DateTimeField(auto_now_add=True)
    last_viewed = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("student", "lesson")


class Attempt(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attempts")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]

    @property
    def percent(self):
        return round(self.score * 100 / self.total) if self.total else 0


class StudentAnswer(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="student_answers")
    choice = models.ForeignKey(Choice, null=True, blank=True, on_delete=models.SET_NULL)
    is_correct = models.BooleanField(default=False)
    text_answer = models.TextField("إجابة الطالب النصية (للأسئلة المقالية)", blank=True, default="")


class ErrorRecord(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="errors")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="error_records")
    choice = models.ForeignKey(Choice, null=True, blank=True, on_delete=models.SET_NULL)
    times_wrong = models.PositiveIntegerField(default=1)
    reviewed = models.BooleanField("تمت مراجعته", default=False)
    resolved = models.BooleanField("أُجيب عنه صحيحًا لاحقًا", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "question")
        ordering = ["-updated_at"]


class SavedQuestion(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_questions")
    question = models.ForeignKey(Question, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("student", "question")


class VocabularyProgress(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="vocab_progress")
    word = models.ForeignKey(VocabularyWord, on_delete=models.CASCADE, related_name="progress")
    wrong_count = models.PositiveIntegerField(default=0)
    correct_streak = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("student", "word")

    @property
    def needs_review(self):
        return self.wrong_count > 0 and self.correct_streak < 2


class Attendance(models.Model):
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="attendance")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    present = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("teacher", "student", "date")
        ordering = ["-date"]


class CenterAttendance(models.Model):
    """حضور وغياب الطلاب في السنتر (الحضور الفعلي)، منفصل عن حضور المنصة (Attendance).
    يُسجَّل تلقائيًا من ملف PDF فيه أرقام الطلاب (student_number) أو يدويًا."""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="center_attendance")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="center_attendance")
    date = models.DateField()
    present = models.BooleanField(default=True)
    source = models.CharField(max_length=10, default="pdf", choices=[("pdf", "ملف PDF"), ("manual", "يدوي")])
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("teacher", "student", "date")
        ordering = ["-date"]


# ===================== الربط الذكي بين الأسئلة وأقسام الشرح =====================
# نظام ربط الأسئلة بالشرح (Question–Lecture Mapping): يقترح الذكاء الاصطناعي القسم
# الأنسب من شرح الدرس لكل سؤال، ويعتمد المدرّس الاقتراح أو يعدّله قبل استخدامه في
# تجربة الطالب (يُكتب الاعتماد النهائي في الحقل الموجود أصلًا Question.section).

MAPPING_STATUS = [
    ("suggested", "مقترح من الذكاء الاصطناعي"),
    ("confirmed", "مؤكَّد من المدرّس"),
    ("rejected", "مرفوض"),
]


class QuestionMapping(models.Model):
    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="mapping")
    section = models.ForeignKey(LessonSection, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="primary_mappings")
    secondary_sections = models.ManyToManyField(LessonSection, blank=True, related_name="secondary_mappings")
    confidence = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, choices=MAPPING_STATUS, default="suggested")
    reasoning = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"سؤال {self.question_id} → قسم {self.section_id}"


class MappingRun(models.Model):
    RUN_STATUS = [("running", "قيد التنفيذ"), ("done", "اكتملت"), ("failed", "فشلت")]
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="mapping_runs")
    status = models.CharField(max_length=10, choices=RUN_STATUS, default="running")
    provider = models.CharField(max_length=20, blank=True)
    matched_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
