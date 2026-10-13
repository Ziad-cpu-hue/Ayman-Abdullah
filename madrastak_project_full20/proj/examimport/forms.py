from django import forms
from django.conf import settings

from accounts.forms import StyledForm

from .models import ImportedChoice

ACCEPTED_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".webp")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """حقل رفع عدة ملفات في نفس الوقت (لمجموعة صور صفحات متعددة مثلًا)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return single_clean(data, initial)


class BatchUploadForm(StyledForm):
    files = MultipleFileField(
        label="ملف الأسئلة",
        help_text="ملف PDF واحد، أو صورة واحدة، أو مجموعة صور لصفحات متعددة (JPG/PNG/WEBP).",
        widget=MultipleFileInput(attrs={"accept": ".pdf,image/*"}),
    )

    def clean_files(self):
        files = self.cleaned_data.get("files") or []
        if not files:
            raise forms.ValidationError("اختر ملفًا واحدًا على الأقل.")
        max_files = getattr(settings, "MAX_IMPORT_FILES", 30)
        if len(files) > max_files:
            raise forms.ValidationError(f"الحد الأقصى {max_files} ملفًا في الدفعة الواحدة.")
        max_mb = getattr(settings, "MAX_IMPORT_FILE_MB", 15)
        is_pdf = []
        for f in files:
            name = f.name.lower()
            ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
            if ext not in ACCEPTED_EXT:
                raise forms.ValidationError(f"صيغة الملف غير مدعومة: {f.name}")
            if f.size > max_mb * 1024 * 1024:
                raise forms.ValidationError(f"حجم الملف «{f.name}» أكبر من {max_mb} ميجابايت.")
            is_pdf.append(ext == ".pdf")
        if any(is_pdf) and len(files) > 1:
            raise forms.ValidationError(
                "لا يمكن الجمع بين ملف PDF وملفات أخرى في نفس الدفعة. "
                "ارفع ملف PDF واحدًا، أو مجموعة صور فقط."
            )
        return files


class ImportedQuestionEditForm(StyledForm):
    """نموذج تعديل سؤال مستورَد قبل اعتماده، بنفس منطق نموذج السؤال العادي
    في لوحة المدرّس (نص + نوع + إجابة صحيحة + أربعة اختيارات كحد أقصى)."""
    text = forms.CharField(label="نص السؤال", widget=forms.Textarea(attrs={"rows": 3}))
    qtype = forms.ChoiceField(label="نوع السؤال", choices=[("mcq", "اختيار من متعدد"), ("tf", "صح / خطأ")])
    correct = forms.ChoiceField(
        label="رقم الاختيار الصحيح",
        choices=[("1", "الاختيار الأول"), ("2", "الاختيار الثاني"), ("3", "الاختيار الثالث"), ("4", "الاختيار الرابع")],
    )
    choice_text_1 = forms.CharField(label="الاختيار الأول", required=False)
    choice_text_2 = forms.CharField(label="الاختيار الثاني", required=False)
    choice_text_3 = forms.CharField(label="الاختيار الثالث", required=False)
    choice_text_4 = forms.CharField(label="الاختيار الرابع", required=False)

    def __init__(self, *args, iq=None, **kwargs):
        super().__init__(*args, **kwargs)
        if iq is not None and not self.is_bound:
            choices = list(iq.choices.all())
            self.fields["text"].initial = iq.text
            self.fields["qtype"].initial = iq.qtype
            correct_i = iq.correct_index or next((i + 1 for i, c in enumerate(choices) if c.is_correct), 1)
            self.fields["correct"].initial = str(min(correct_i, 4))
            for i in range(4):
                key = f"choice_text_{i + 1}"
                self.fields[key].initial = choices[i].text if i < len(choices) else ""

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("qtype") == "mcq":
            if not cleaned.get("choice_text_3") or not cleaned.get("choice_text_4"):
                self.add_error(None, "سؤال الاختيار من متعدد يحتاج 4 اختيارات على الأقل.")
        elif cleaned.get("qtype") == "tf":
            cleaned.setdefault("choice_text_1", "")
            if not cleaned.get("choice_text_1"):
                cleaned["choice_text_1"] = "صح"
            if not cleaned.get("choice_text_2"):
                cleaned["choice_text_2"] = "خطأ"
        return cleaned

    def save(self, iq):
        iq.text = self.cleaned_data["text"]
        iq.qtype = self.cleaned_data["qtype"]
        n = 2 if iq.qtype == "tf" else 4
        correct = int(self.cleaned_data["correct"])
        iq.correct_index = correct if correct <= n else None
        iq.status = "ready" if iq.correct_index else "needs_review"
        iq.notes = "" if iq.correct_index else "لم يُحدَّد اختيار صحيح"
        iq.confidence = 100 if iq.correct_index else iq.confidence
        iq.save()
        iq.choices.all().delete()
        for i in range(1, n + 1):
            txt = self.cleaned_data.get(f"choice_text_{i}") or ""
            ImportedChoice.objects.create(question=iq, text=txt, is_correct=(i == correct), order=i)
        return iq
