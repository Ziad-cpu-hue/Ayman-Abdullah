from django import forms

from accounts.forms import StyledForm, StyledModelForm
from accounts.models import SiteSetting, TeacherProfile, User


class TeacherCreateForm(StyledForm):
    full_name = forms.CharField(label="الاسم الكامل", max_length=150)
    phone = forms.CharField(label="رقم الهاتف (دخول المدرّس)", max_length=15, widget=forms.TextInput(attrs={"dir": "ltr"}))
    password1 = forms.CharField(label="كلمة مرور مبدئية", widget=forms.PasswordInput)
    subject = forms.CharField(label="المادة", max_length=80)
    grades = forms.CharField(label="الصفوف (اختياري)", max_length=60, required=False)
    monthly_price = forms.DecimalField(label="سعر الاشتراك الشهري (جنيه)", min_value=0, initial=10)
    display_name = forms.CharField(label="الاسم الظاهر للطلاب (اختياري)", max_length=60, required=False,
                                   help_text="اتركه فارغًا لإظهار الاسم الكامل، أو اكتب مثلًا: M")
    bio = forms.CharField(label="نبذة (اختياري)", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_phone(self):
        from accounts.utils import normalize_phone
        from accounts.models import phone_validator
        phone = normalize_phone(self.cleaned_data["phone"])
        phone_validator(phone)
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError("هذا الرقم مستخدم بالفعل.")
        return phone

    def save(self):
        c = self.cleaned_data
        user = User.objects.create_user(c["phone"], c["password1"], full_name=c["full_name"], role="teacher")
        return TeacherProfile.objects.create(user=user, subject=c["subject"], grades=c["grades"], bio=c["bio"],
                                             monthly_price=c["monthly_price"], display_name=c["display_name"].strip())


class TeacherEditForm(StyledModelForm):
    full_name = forms.CharField(label="الاسم الكامل", max_length=150)

    class Meta:
        model = TeacherProfile
        fields = ["display_name", "subject", "grades", "bio", "monthly_price", "is_listed"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["full_name"].initial = self.instance.user.full_name
        self.order_fields(["full_name", "display_name", "subject", "grades", "bio", "monthly_price", "is_listed"])

    def save(self, commit=True):
        obj = super().save(commit=False)
        if commit:
            obj.user.full_name = self.cleaned_data["full_name"]
            obj.user.save(update_fields=["full_name"])
            obj.save()
        return obj


class SiteSettingForm(StyledModelForm):
    class Meta:
        model = SiteSetting
        fields = ["platform_name", "tagline", "wallet_number", "wallet_holder", "payment_instructions",
                  "support_phone", "support_email", "default_monthly_price", "registration_open", "names_in_english",
                  "groq_api_key", "anthropic_api_key", "privacy_text", "terms_text"]
        widgets = {"payment_instructions": forms.Textarea(attrs={"rows": 3}), "privacy_text": forms.Textarea(attrs={"rows": 6}),
                   "terms_text": forms.Textarea(attrs={"rows": 6}), "wallet_number": forms.TextInput(attrs={"dir": "ltr"}),
                   "groq_api_key": forms.TextInput(attrs={"dir": "ltr", "placeholder": "gsk_..."}),
                   "anthropic_api_key": forms.TextInput(attrs={"dir": "ltr", "placeholder": "sk-ant-..."})}
        help_texts = {
            "names_in_english": "مفعّل افتراضيًا: عند تسجيل طالب باسم عربي يُحفظ ويُعرض اسمه بالإنجليزية (مثل Ahmed Mohamed Ali) ويُحفظ الاسم العربي الأصلي لدى الإدارة.",
            "groq_api_key": "من console.groq.com ← API Keys. يشغّل استيراد المفردات وهيكلة شرح الدروس بالذكاء الاصطناعي.",
            "anthropic_api_key": "بديل اختياري إن لم تستخدم Groq. يُستخدم فقط إذا تُرك مفتاح Groq فارغًا.",
        }


class StaffCreateForm(StyledForm):
    full_name = forms.CharField(label="الاسم الكامل", max_length=150)
    phone = forms.CharField(label="رقم الهاتف", max_length=15, widget=forms.TextInput(attrs={"dir": "ltr"}))
    password1 = forms.CharField(label="كلمة مرور مبدئية", widget=forms.PasswordInput)
    staff_role = forms.ChoiceField(label="الصلاحية", choices=User.STAFF_ROLES)

    def clean_phone(self):
        from accounts.utils import normalize_phone
        from accounts.models import phone_validator
        phone = normalize_phone(self.cleaned_data["phone"])
        phone_validator(phone)
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError("هذا الرقم مستخدم بالفعل.")
        return phone

    def save(self):
        c = self.cleaned_data
        return User.objects.create_user(c["phone"], c["password1"], full_name=c["full_name"], role="staff", staff_role=c["staff_role"], is_staff=True)
