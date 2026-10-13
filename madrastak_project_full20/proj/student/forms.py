from django import forms

from accounts.forms import PasswordChangeForm2, Styled, StyledForm, StyledModelForm
from billing.models import Payment
from django.conf import settings


class SubscribeForm(StyledModelForm):
    class Meta:
        model = Payment
        fields = ["method", "sender_phone", "reference", "receipt"]
        widgets = {"sender_phone": forms.TextInput(attrs={"dir": "ltr", "placeholder": "01xxxxxxxxx"}),
                   "reference": forms.TextInput(attrs={"dir": "ltr"}),
                   "receipt": forms.ClearableFileInput(attrs={"accept": "image/*", "data-preview": "#rc"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["receipt"].required = True
        self.fields["method"].choices = [c for c in Payment.METHODS if c[0] != "manual"]

    def clean_receipt(self):
        f = self.cleaned_data["receipt"]
        if f.size > settings.MAX_RECEIPT_MB * 1024 * 1024:
            raise forms.ValidationError(f"حجم الصورة أكبر من {settings.MAX_RECEIPT_MB} ميجابايت.")
        return f


class MessageForm(StyledForm):
    text = forms.CharField(label="رسالتك", required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "اكتب سؤالك هنا…"}))
    image = forms.ImageField(label="صورة (اختياري)", required=False)

    def clean(self):
        c = super().clean()
        if not c.get("text") and not c.get("image"):
            raise forms.ValidationError("اكتب رسالة أو أرفق صورة.")
        return c
