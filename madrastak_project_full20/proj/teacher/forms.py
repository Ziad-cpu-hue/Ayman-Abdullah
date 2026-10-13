from django import forms

from accounts.forms import StyledForm, StyledModelForm
from learning.models import (Choice, Course, Lesson, LessonFile, LessonSection, Quiz, Unit, VocabularyWord)


class CourseForm(StyledModelForm):
    class Meta:
        model = Course
        fields = ["title", "description", "grade", "course_type", "term_label", "cover", "status", "publish_at"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "publish_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class UnitForm(StyledModelForm):
    class Meta:
        model = Unit
        fields = ["title", "description", "order", "status", "publish_at"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2}), "publish_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class LessonForm(StyledModelForm):
    class Meta:
        model = Lesson
        fields = ["title", "summary", "video_url", "video_file"]
        widgets = {"summary": forms.Textarea(attrs={"rows": 2}),
                   "video_url": forms.URLInput(attrs={"dir": "ltr", "placeholder": "https://youtube.com/watch?v=..."})}


class SectionForm(StyledModelForm):
    class Meta:
        model = LessonSection
        fields = ["title", "kind", "content", "order"]
        widgets = {"content": forms.Textarea(attrs={"rows": 6})}


class FileForm(StyledModelForm):
    class Meta:
        model = LessonFile
        fields = ["title", "file"]


class VocabForm(StyledModelForm):
    class Meta:
        model = VocabularyWord
        fields = ["word", "meaning", "example", "example_translation", "order"]
        widgets = {"word": forms.TextInput(attrs={"dir": "ltr"})}


class QuizForm(StyledModelForm):
    class Meta:
        model = Quiz
        fields = ["title", "kind", "description", "duration_minutes", "deadline", "max_attempts", "status", "publish_at"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2}), "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
                   "publish_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class QuestionForm(StyledForm):
    text = forms.CharField(label="نص السؤال", widget=forms.Textarea(attrs={"rows": 2}))
    image = forms.ImageField(label="صورة (اختياري)", required=False)
    qtype = forms.ChoiceField(label="نوع السؤال", choices=[("mcq", "اختيار من متعدد"), ("tf", "صح / خطأ"), ("essay", "سؤال مقالي")])
    difficulty = forms.ChoiceField(label="الصعوبة", choices=[("easy", "سهل"), ("medium", "متوسط"), ("hard", "صعب")], initial="medium")
    points = forms.IntegerField(label="الدرجة", min_value=1, initial=1)
    hint = forms.CharField(label="تلميح (اختياري)", required=False)
    explanation = forms.CharField(label="شرح الإجابة الصحيحة", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    section = forms.ModelChoiceField(label="القسم المرتبط من الدرس", queryset=LessonSection.objects.none(), required=False)
    correct = forms.ChoiceField(label="الإجابة الصحيحة", required=False, choices=[("1", "الاختيار الأول"), ("2", "الاختيار الثاني"), ("3", "الاختيار الثالث"), ("4", "الاختيار الرابع")])
    choice_text_1 = forms.CharField(label="الاختيار الأول", required=False)
    choice_wrong_1 = forms.CharField(label="سبب الخطأ إن اختير (اختياري)", required=False)
    choice_text_2 = forms.CharField(label="الاختيار الثاني", required=False)
    choice_wrong_2 = forms.CharField(label="سبب الخطأ إن اختير (اختياري)", required=False)
    choice_text_3 = forms.CharField(label="الاختيار الثالث", required=False)
    choice_wrong_3 = forms.CharField(label="سبب الخطأ إن اختير (اختياري)", required=False)
    choice_text_4 = forms.CharField(label="الاختيار الرابع", required=False)
    choice_wrong_4 = forms.CharField(label="سبب الخطأ إن اختير (اختياري)", required=False)

    def __init__(self, *a, lesson=None, quiz=None, **kw):
        super().__init__(*a, **kw)
        if lesson:
            self.fields["section"].queryset = lesson.sections.all()

    def clean(self):
        c = super().clean()
        if c.get("qtype") in ("mcq", "tf"):
            # نفس شروط الحقول الإلزامية السابقة (كانت إلزامية على مستوى الحقل)
            for name in ("choice_text_1", "choice_text_2", "correct"):
                if not c.get(name):
                    self.add_error(name, "هذا الحقل مطلوب.")
        if c.get("qtype") == "mcq":
            if not c.get("choice_text_3") or not c.get("choice_text_4"):
                self.add_error(None, "سؤال الاختيار من متعدد يحتاج 4 اختيارات على الأقل.")
        return c

    def save(self, quiz):
        from learning.models import Question
        q = Question.objects.create(quiz=quiz, text=self.cleaned_data["text"], image=self.cleaned_data.get("image") or "",
                                    qtype=self.cleaned_data["qtype"], difficulty=self.cleaned_data["difficulty"],
                                    points=self.cleaned_data["points"], hint=self.cleaned_data["hint"],
                                    explanation=self.cleaned_data["explanation"], section=self.cleaned_data.get("section"))
        if self.cleaned_data["qtype"] == "essay":
            return q  # السؤال المقالي بلا اختيارات ولا إجابة صحيحة
        correct = self.cleaned_data["correct"]
        n = 2 if self.cleaned_data["qtype"] == "tf" else 4
        for i in range(1, n + 1):
            txt = self.cleaned_data.get(f"choice_text_{i}")
            if not txt:
                continue
            Choice.objects.create(question=q, text=txt, is_correct=(str(i) == correct), why_wrong=self.cleaned_data.get(f"choice_wrong_{i}", ""), order=i)
        return q


RESERVED_SLUGS = {
    "", "pricing", "about", "contact", "privacy", "terms", "teachers", "register", "login", "logout",
    "forgot-password", "go", "student", "teacher", "owner", "support", "static", "media", "admin",
    "favicon.ico", "robots.txt",
}


class TeacherProfileForm(StyledModelForm):
    full_name = forms.CharField(label="الاسم الكامل", max_length=150)
    slug = forms.CharField(
        label="اسمك في رابطك الخاص",
        max_length=50,
        help_text="اكتب اسمك بالإنجليزية (أو أرقام وشرطات) — سيتحوّل تلقائيًا إلى رابط صالح تشاركه مع طلابك.",
        widget=forms.TextInput(attrs={"dir": "ltr", "placeholder": "mohamed-mostafa"}),
    )

    class Meta:
        from accounts.models import TeacherProfile
        model = TeacherProfile
        fields = ["subject", "grades", "bio", "photo", "cover", "slug"]
        widgets = {"bio": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance and self.instance.pk:
            self.fields["full_name"].initial = self.instance.user.full_name
        order = ["full_name", "slug", "subject", "grades", "bio", "photo", "cover"]
        self.order_fields(order)

    def clean_slug(self):
        from django.utils.text import slugify

        from accounts.models import TeacherProfile

        raw = (self.cleaned_data.get("slug") or "").strip()
        if not raw:
            raise forms.ValidationError("اكتب الاسم الذي تريد أن يظهر في رابطك.")
        value = slugify(raw)
        if not value:
            raise forms.ValidationError("اكتب اسمًا يحتوي أحرفًا إنجليزية أو أرقامًا (سيتحوّل تلقائيًا إلى رابط صالح).")
        if value in RESERVED_SLUGS:
            raise forms.ValidationError("هذا الاسم محجوز للنظام، جرّب اسمًا آخر.")
        qs = TeacherProfile.objects.filter(slug=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("هذا الرابط مستخدم من معلم آخر بالفعل، جرّب صيغة أخرى (مثلاً بإضافة مادتك أو رقم).")
        return value


class AttendanceForm(StyledForm):
    date = forms.DateField(label="التاريخ", widget=forms.DateInput(attrs={"type": "date"}))
