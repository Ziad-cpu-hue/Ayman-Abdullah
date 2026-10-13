from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password

from .models import RESERVED_SLUGS, ContactMessage, StudentProfile, TeacherProfile, User, phone_validator
from .translit import apply_name_policy
from .utils import normalize_phone


class Styled:
    """يضيف أصناف CSS لكل الحقول."""
    def style(self):
        for f in self.fields.values():
            w = f.widget
            cls = w.attrs.get("class", "")
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs["class"] = (cls + " check").strip()
            elif isinstance(w, forms.RadioSelect):
                pass
            else:
                w.attrs["class"] = (cls + " input").strip()


class StyledForm(Styled, forms.Form):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.style()


class StyledModelForm(Styled, forms.ModelForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.style()


class RegisterForm(StyledForm):
    full_name = forms.CharField(label="الاسم الكامل", max_length=150, widget=forms.TextInput(attrs={"placeholder": "مثال: أحمد محمد علي", "autocomplete": "name"}))
    phone = forms.CharField(label="رقم الهاتف", max_length=15, widget=forms.TextInput(attrs={"placeholder": "01012345678", "inputmode": "tel", "dir": "ltr", "autocomplete": "tel"}))
    parent_phone = forms.CharField(label="رقم هاتف ولي الأمر", max_length=15, widget=forms.TextInput(attrs={"placeholder": "01012345678", "inputmode": "tel", "dir": "ltr"}))
    grade = forms.ChoiceField(label="المرحلة الدراسية", choices=[("", "اختر المرحلة الدراسية")] + StudentProfile.GRADES)
    password1 = forms.CharField(label="كلمة المرور", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="تأكيد كلمة المرور", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name.split()) < 2:
            raise forms.ValidationError("اكتب اسمك الكامل (الاسم الأول واسم العائلة على الأقل).")
        return name

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        phone_validator(phone)
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError("هذا الرقم مسجّل بالفعل. سجّل الدخول بدلًا من إنشاء حساب جديد.")
        return phone

    def clean_parent_phone(self):
        phone = normalize_phone(self.cleaned_data["parent_phone"])
        phone_validator(phone)
        return phone

    def clean(self):
        c = super().clean()
        if c.get("phone") and c.get("phone") == c.get("parent_phone"):
            self.add_error("parent_phone", "يجب أن يختلف رقم ولي الأمر عن رقمك.")
        p1, p2 = c.get("password1"), c.get("password2")
        if p1:
            try:
                validate_password(p1)
            except forms.ValidationError as e:
                self.add_error("password1", e)
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "كلمتا المرور غير متطابقتين.")
        return c

    def save(self):
        c = self.cleaned_data
        # الافتراضي: الاسم العربي يُحوَّل للإنجليزية ويُحفظ الأصل في name_ar (يتحكم فيه المالك من إعدادات المنصة)
        full_name, name_ar = apply_name_policy(c["full_name"])
        user = User.objects.create_user(c["phone"], c["password1"], full_name=full_name, name_ar=name_ar, role="student")
        StudentProfile.objects.create(user=user, parent_phone=c["parent_phone"], grade=int(c["grade"]))
        return user


class LoginForm(StyledForm):
    phone = forms.CharField(label="رقم الهاتف", widget=forms.TextInput(attrs={"placeholder": "01012345678", "inputmode": "tel", "dir": "ltr", "autocomplete": "tel"}))
    password = forms.CharField(label="كلمة المرور", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember = forms.BooleanField(label="تذكّرني", required=False)


class ForgotForm(StyledForm):
    phone = forms.CharField(label="رقم الهاتف المسجّل", widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "tel"}))
    note = forms.CharField(label="ملاحظة (اختياري)", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])


class ContactForm(StyledForm):
    name = forms.CharField(label="الاسم", max_length=100)
    phone = forms.CharField(label="رقم الهاتف", max_length=20, widget=forms.TextInput(attrs={"dir": "ltr"}))
    message = forms.CharField(label="رسالتك", widget=forms.Textarea(attrs={"rows": 4}))


class ProfileForm(StyledForm):
    full_name = forms.CharField(label="الاسم الكامل", max_length=150)
    parent_phone = forms.CharField(label="رقم هاتف ولي الأمر", max_length=15, widget=forms.TextInput(attrs={"dir": "ltr"}))
    grade = forms.ChoiceField(label="المرحلة الدراسية", choices=StudentProfile.GRADES)
    avatar = forms.ImageField(label="الصورة الشخصية", required=False)

    def clean_parent_phone(self):
        phone = normalize_phone(self.cleaned_data["parent_phone"])
        phone_validator(phone)
        return phone


class PasswordChangeForm2(StyledForm):
    old = forms.CharField(label="كلمة المرور الحالية", widget=forms.PasswordInput)
    new1 = forms.CharField(label="كلمة المرور الجديدة", widget=forms.PasswordInput)
    new2 = forms.CharField(label="تأكيد كلمة المرور الجديدة", widget=forms.PasswordInput)

    def __init__(self, user, *a, **kw):
        super().__init__(*a, **kw)
        self.user = user

    def clean_old(self):
        if not self.user.check_password(self.cleaned_data["old"]):
            raise forms.ValidationError("كلمة المرور الحالية غير صحيحة.")
        return self.cleaned_data["old"]

    def clean(self):
        c = super().clean()
        if c.get("new1"):
            try:
                validate_password(c["new1"], self.user)
            except forms.ValidationError as e:
                self.add_error("new1", e)
        if c.get("new1") and c.get("new2") and c["new1"] != c["new2"]:
            self.add_error("new2", "كلمتا المرور غير متطابقتين.")
        return c
