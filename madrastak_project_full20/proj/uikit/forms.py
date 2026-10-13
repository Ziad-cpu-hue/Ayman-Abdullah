"""نماذج معالجات الإنشاء (وحدة/دروس، اختبار) في لوحة المدرّس الجديدة. تستخدم نفس النماذج والجداول القائمة."""
import json

from django import forms

from learning.models import Course, Lesson, Quiz, Unit


class UnitWizardForm(forms.Form):
    course = forms.ModelChoiceField(queryset=Course.objects.none(), label="الدورة", empty_label="اختر الدورة")
    unit_mode = forms.ChoiceField(choices=[("new", "وحدة جديدة"), ("existing", "وحدة موجودة")], initial="new", label="الوحدة")
    unit_existing = forms.ModelChoiceField(queryset=Unit.objects.none(), required=False, label="الوحدة الحالية", empty_label="اختر الوحدة")
    unit_title = forms.CharField(required=False, max_length=160, label="اسم الوحدة")
    unit_description = forms.CharField(required=False, label="الوصف", widget=forms.Textarea(attrs={"rows": 3}))
    publish_unit = forms.BooleanField(required=False, label="نشر الوحدة للطلاب فور الإنشاء")

    def __init__(self, *a, teacher=None, **kw):
        super().__init__(*a, **kw)
        self.fields["course"].queryset = Course.objects.filter(teacher=teacher)
        self.fields["unit_existing"].queryset = Unit.objects.filter(course__teacher=teacher).select_related("course")

    def clean(self):
        c = super().clean()
        mode, course = c.get("unit_mode"), c.get("course")
        if mode == "new":
            if not (c.get("unit_title") or "").strip():
                self.add_error("unit_title", "اكتب اسم الوحدة.")
        elif mode == "existing":
            unit = c.get("unit_existing")
            if not unit:
                self.add_error("unit_existing", "اختر الوحدة.")
            elif course and unit.course_id != course.id:
                self.add_error("unit_existing", "هذه الوحدة لا تتبع الدورة المختارة.")
        return c


def _target_choices(teacher):
    groups = []
    for c in Course.objects.filter(teacher=teacher).order_by("order", "id"):
        opts = [(f"course:{c.pk}", f"اختبار عام على مستوى الدورة: {c.title}")]
        for u in c.units.all():
            opts.append((f"unit:{u.pk}", f"وحدة: {u.title}"))
            for l in u.lessons.all():
                opts.append((f"lesson:{l.pk}", f"درس: {u.title} ← {l.title}"))
        groups.append((c.title, opts))
    return groups


class QuizWizardForm(forms.Form):
    title = forms.CharField(max_length=160, label="عنوان الاختبار")
    description = forms.CharField(required=False, label="وصف / تعليمات الاختبار", widget=forms.Textarea(attrs={"rows": 3}))
    kind = forms.ChoiceField(choices=Quiz.KINDS, initial="quiz", label="النوع")
    target = forms.ChoiceField(choices=[], label="مكان الاختبار")
    duration_minutes = forms.IntegerField(required=False, min_value=0, initial=0, label="الزمن بالدقائق (0 = بلا حد)")
    max_attempts = forms.IntegerField(required=False, min_value=0, initial=0, label="عدد المحاولات (0 = بلا حد)")
    deadline = forms.DateTimeField(required=False, label="آخر موعد للتسليم (للواجب)",
                                   input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"])
    publish = forms.BooleanField(required=False, label="نشر الاختبار للطلاب فور الحفظ")
    question_source = forms.ChoiceField(choices=[("manual", "إضافة أسئلة يدويًا"), ("pdf", "رفع ملف PDF"), ("image", "رفع صورة")],
                                        initial="manual", label="طريقة إضافة الأسئلة")
    questions_json = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *a, teacher=None, **kw):
        super().__init__(*a, **kw)
        self.teacher = teacher
        self.fields["target"].choices = _target_choices(teacher)

    def clean_questions_json(self):
        return self.cleaned_data.get("questions_json") or "[]"

    def clean(self):
        c = super().clean()
        c["questions"] = []
        if c.get("question_source") == "manual":
            try:
                raw = json.loads(c.get("questions_json") or "[]")
                assert isinstance(raw, list)
            except Exception:
                self.add_error(None, "تعذّر قراءة الأسئلة المُدخلة. أعد المحاولة.")
                return c
            out = []
            for i, q in enumerate(raw, start=1):
                text = str((q or {}).get("text", "")).strip()
                if str((q or {}).get("type", "")) == "essay":
                    # سؤال مقالي: نص السؤال فقط، بلا اختيارات ولا إجابة صحيحة
                    if text:
                        out.append({"text": text, "options": [], "correct": None, "type": "essay",
                                    "hint": str((q or {}).get("hint", "")).strip()[:300],
                                    "explanation": str((q or {}).get("explanation", "")).strip()})
                    continue
                opts = [str(o or "").strip() for o in ((q or {}).get("options") or [])][:4]
                try:
                    correct = int((q or {}).get("correct", 0))
                except (TypeError, ValueError):
                    correct = 0
                if not text and not any(opts):
                    continue  # سؤال فارغ تمامًا يُهمل
                if not text:
                    self.add_error(None, f"السؤال {i}: اكتب نص السؤال.")
                    continue
                filled = [(k, o) for k, o in enumerate(opts) if o]
                if len(filled) < 2:
                    self.add_error(None, f"السؤال {i}: أضف اختيارين على الأقل.")
                    continue
                if correct not in [k for k, _ in filled]:
                    self.add_error(None, f"السؤال {i}: حدّد الإجابة الصحيحة من الاختيارات المكتوبة.")
                    continue
                out.append({"text": text, "options": filled, "correct": correct,
                            "hint": str((q or {}).get("hint", "")).strip()[:300],
                            "explanation": str((q or {}).get("explanation", "")).strip()})
            if not out and not self.errors:
                self.add_error(None, "أضف سؤالًا واحدًا على الأقل، أو اختر رفع ملف PDF/صورة.")
            c["questions"] = out
        return c
