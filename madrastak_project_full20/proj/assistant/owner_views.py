"""إدارة مفاتيح الذكاء الاصطناعي للمساعد (لوحة المالك فقط)."""
from urllib.parse import urlparse

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import owner_required
from accounts.forms import StyledModelForm
from accounts.utils import audit

from . import explain, llm
from .models import PROVIDER_DEFAULTS, AIProviderKey, AssistantSetting, QuestionExplanation


class KeyForm(StyledModelForm):
    api_key = forms.CharField(
        label="مفتاح API", required=False, widget=forms.Textarea(attrs={"rows": 3, "dir": "ltr", "autocomplete": "off",
                                                                     "placeholder": "الصق المفتاح هنا"}),
        help_text="عند الإضافة يمكنك لصق عدة مفاتيح لنفس المزوّد (كل مفتاح في سطر) فتُنشأ كمفاتيح منفصلة. عند التعديل اتركه فارغًا لإبقاء المفتاح الحالي.")

    class Meta:
        model = AIProviderKey
        fields = ["name", "provider", "base_url", "api_key", "model", "daily_limit", "is_active"]
        widgets = {"base_url": forms.TextInput(attrs={"dir": "ltr", "placeholder": "https://api.example.com/v1"}),
                   "model": forms.TextInput(attrs={"dir": "ltr", "placeholder": "اتركه فارغًا للافتراضي"})}

    def clean_base_url(self):
        v = (self.cleaned_data.get("base_url") or "").strip()
        if v:
            u = urlparse(v)
            if u.scheme not in ("http", "https") or not u.netloc:
                raise forms.ValidationError("أدخل رابطًا صحيحًا يبدأ بـ https://")
        return v

    def clean(self):
        data = super().clean()
        provider = data.get("provider")
        keys = [k.strip() for k in (data.get("api_key") or "").replace(",", "\n").splitlines() if k.strip()]
        if not self.instance.pk and not keys:
            self.add_error("api_key", "أدخل مفتاح API واحدًا على الأقل.")
        if self.instance.pk and len(keys) > 1:
            self.add_error("api_key", "عند التعديل أدخل مفتاحًا واحدًا فقط.")
        if provider == "custom" and not (data.get("base_url") or "").strip():
            self.add_error("base_url", "للمزوّد الآخر يجب إدخال الرابط الكامل.")
        if provider in ("custom", "openrouter") and not (data.get("model") or "").strip():
            self.add_error("model", "اسم النموذج مطلوب لهذا المزوّد.")
        data["_keys"] = keys
        return data


def _context(**extra):
    keys = list(AIProviderKey.objects.all())
    active = sum(1 for k in keys if k.state == "active")
    ready, total, failed, busy = explain.coverage()
    ctx = {"keys": keys, "n_active": active, "cfg": AssistantSetting.get(), "defaults": PROVIDER_DEFAULTS,
           "ex_ready": ready, "ex_total": total, "ex_failed": failed, "ex_busy": busy,
           "ex_running": explain.stats.get("running") or explain.queue_size() > 0,
           "ex_last_error": explain.stats.get("last_error", ""),
           "ex_pct": round(ready * 100 / total) if total else 0}
    ctx.update(extra)
    return ctx


@owner_required("settings")
def keys(request):
    return render(request, "assistant/owner_keys.html", _context())


@owner_required("settings")
def key_new(request):
    form = KeyForm(request.POST or None, initial={"provider": "groq", "is_active": True})
    if request.method == "POST" and form.is_valid():
        raw = form.cleaned_data["_keys"]
        base = form.cleaned_data["name"].strip()
        created = 0
        for i, k in enumerate(raw, 1):
            if AIProviderKey.objects.filter(api_key=k, provider=form.cleaned_data["provider"]).exists():
                continue
            AIProviderKey.objects.create(
                name=base if len(raw) == 1 else f"{base} {i}", provider=form.cleaned_data["provider"],
                base_url=form.cleaned_data["base_url"], api_key=k, model=form.cleaned_data["model"],
                daily_limit=form.cleaned_data["daily_limit"] or 0, is_active=form.cleaned_data["is_active"])
            created += 1
        audit(request, "إضافة مفاتيح ذكاء اصطناعي", base, f"{created} مفتاح")
        if created:
            messages.success(request, f"تمت إضافة {created} مفتاح. جرّبها بزر «اختبار».")
        else:
            messages.info(request, "هذه المفاتيح مضافة مسبقًا.")
        return redirect("assistant:owner_keys")
    return render(request, "assistant/owner_key_form.html", {"form": form, "obj": None, "defaults": PROVIDER_DEFAULTS})


@owner_required("settings")
def key_edit(request, pk):
    obj = get_object_or_404(AIProviderKey, pk=pk)
    old_key = obj.api_key  # يجب حفظه قبل أن يعدّل النموذج الكائن نفسه
    form = KeyForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        new_key = (form.cleaned_data["_keys"] or [None])[0]
        inst = form.save(commit=False)
        inst.api_key = new_key or old_key
        if new_key and new_key != old_key:
            inst.cooldown_until, inst.last_error = None, ""
        inst.save()
        audit(request, "تعديل مفتاح ذكاء اصطناعي", inst.name)
        messages.success(request, "تم حفظ التعديلات.")
        return redirect("assistant:owner_keys")
    return render(request, "assistant/owner_key_form.html", {"form": form, "obj": obj, "defaults": PROVIDER_DEFAULTS})


@owner_required("settings")
@require_POST
def key_toggle(request, pk):
    k = get_object_or_404(AIProviderKey, pk=pk)
    k.is_active = not k.is_active
    k.save(update_fields=["is_active"])
    audit(request, "تفعيل/إيقاف مفتاح ذكاء اصطناعي", k.name, "مفعّل" if k.is_active else "موقوف")
    messages.success(request, f"{'تم تفعيل' if k.is_active else 'تم إيقاف'} المفتاح «{k.name}».")
    return redirect("assistant:owner_keys")


@owner_required("settings")
@require_POST
def key_test(request, pk):
    k = get_object_or_404(AIProviderKey, pk=pk)
    ok, msg, ms = llm.test_key(k)
    (messages.success if ok else messages.error)(
        request, f"✔ المفتاح «{k.name}» يعمل ({ms}ms)." if ok else f"✖ فشل اختبار «{k.name}»: {msg}")
    return redirect("assistant:owner_keys")


@owner_required("settings")
@require_POST
def key_reset(request, pk):
    k = get_object_or_404(AIProviderKey, pk=pk)
    k.cooldown_until, k.last_error = None, ""
    k.save(update_fields=["cooldown_until", "last_error"])
    messages.success(request, f"أُعيد تشغيل المفتاح «{k.name}» فورًا.")
    return redirect("assistant:owner_keys")


@owner_required("settings")
@require_POST
def key_delete(request, pk):
    k = get_object_or_404(AIProviderKey, pk=pk)
    name = k.name
    k.delete()
    audit(request, "حذف مفتاح ذكاء اصطناعي", name)
    messages.success(request, f"تم حذف المفتاح «{name}».")
    return redirect("assistant:owner_keys")


@owner_required("settings")
@require_POST
def settings_save(request):
    cfg = AssistantSetting.get()
    cfg.is_enabled = request.POST.get("is_enabled") == "1"
    try:
        cfg.hourly_limit = max(0, min(1000, int(request.POST.get("hourly_limit") or 0)))
    except ValueError:
        cfg.hourly_limit = 30
    cfg.save()
    audit(request, "تعديل إعدادات المساعد الذكي", "", f"تشغيل={cfg.is_enabled}، حد/ساعة={cfg.hourly_limit}")
    messages.success(request, "تم حفظ إعدادات المساعد.")
    return redirect("assistant:owner_keys")


@owner_required("settings")
@require_POST
def explain_run(request):
    """تعبئة شروح الأسئلة: الناقص فقط / إعادة الفاشل / إعادة توليد الكل (كلها في الخلفية)."""
    mode = request.POST.get("mode", "missing")
    if not llm.is_configured():
        messages.error(request, "أضف مفتاح ذكاء اصطناعي أولًا ثم أعد المحاولة.")
        return redirect("assistant:owner_keys")
    ids = explain.missing_question_ids(include_failed=(mode == "retry"), force=(mode == "force"))
    if mode in ("retry", "force"):
        QuestionExplanation.objects.filter(question_id__in=ids).update(status="pending", attempts=0)
    if not ids:
        messages.info(request, "كل شروح الأسئلة جاهزة ✔")
    else:
        explain.enqueue(ids)
        audit(request, "توليد شروح الأسئلة", mode, f"{len(ids)} سؤال")
        messages.success(request, f"بدأ توليد شروح {len(ids)} سؤال في الخلفية. حدّث الصفحة بعد قليل لمتابعة التقدّم.")
    return redirect("assistant:owner_keys")
