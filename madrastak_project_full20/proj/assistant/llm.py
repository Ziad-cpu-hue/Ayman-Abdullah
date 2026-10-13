"""عميل نماذج اللغة — يوزّع الطلبات على عدة مفاتيح ويتبدّل تلقائيًا عند الفشل.

* يعمل بالمكتبة القياسية فقط (urllib) دون أي تثبيت إضافي.
* يدعم أسلوبين: واجهة OpenAI المتوافقة (Groq/OpenAI/Gemini/OpenRouter/DeepSeek/أي مزوّد آخر)
  وواجهة Anthropic (Claude).
* توزيع الحمل: يُختار دائمًا المفتاح الأقدم استخدامًا (تناوب عادل)، مع تخطّي المفاتيح
  المتوقفة مؤقتًا أو التي بلغت حدّها اليومي.
* عند الفشل: 429 → إيقاف مؤقت للمفتاح (حسب Retry-After)، 401/403 → إيقاف طويل وتسجيل السبب،
  أخطاء الخادم/المهلة → إيقاف قصير، ثم تجربة المفتاح التالي فورًا.
* لو لم يُضف المالك أي مفتاح هنا، يُستخدم مفتاحا Groq/Anthropic القديمان من إعدادات المنصة.
"""
import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from .models import PROVIDER_DEFAULTS, AIProviderKey

DEFAULT_TIMEOUT = 55
# بعض مزوّدي الخدمة (مثل Groq عبر Cloudflare) يرفضون الطلبات التي تعرّف نفسها بـ Python-urllib بخطأ 403،
# لذلك نرسل هوية متصفح/تطبيق معتادة مع كل طلب.
USER_AGENT = "Mozilla/5.0 (compatible; MadrastakAssistant/1.0; +https://example.invalid)"
log = logging.getLogger("assistant.llm")


class AINotConfigured(Exception):
    """لا يوجد أي مفتاح متاح إطلاقًا."""


class AIUnavailable(Exception):
    """كل المفاتيح فشلت أو متوقفة مؤقتًا. kind: rate|network|timeout|auth|config|blocked|too_large|server|..."""

    def __init__(self, message, kind="unknown", retry_after=0, daily=False):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after  # ثوانٍ يقترحها المزوّد قبل إعادة المحاولة (0 = غير معروف)
        self.daily = daily              # True = الحدّ اليومي استُنفد (الانتظار لدقائق لن يفيد)


class _KeyError(Exception):
    def __init__(self, message, cooldown=0, kind="error", retry_after=0, daily=False):
        super().__init__(message)
        self.cooldown = cooldown
        self.kind = kind
        self.retry_after = retry_after
        self.daily = daily


class _LegacyKey:
    """مفتاح قديم من إعدادات المنصة (غير مخزّن في جدول المفاتيح)."""
    id = None
    name = "legacy"

    def __init__(self, provider, api_key):
        self.provider = provider
        self.api_key = api_key
        self.base_url = ""
        self.model = ""
        self.defaults = PROVIDER_DEFAULTS[provider]
        self.effective_model = self.defaults["model"]


# ------------------------------------------------------------------ اختيار المفاتيح
def _pool():
    keys = list(AIProviderKey.objects.filter(is_active=True)
                .order_by(F("last_used_at").asc(nulls_first=True), "id"))
    return [k for k in keys if not k.in_cooldown and not k.over_daily_limit], keys


def _legacy_keys():
    try:
        from accounts.utils import get_ai_keys
        groq, anthropic = get_ai_keys()
    except Exception:
        return []
    out = []
    if groq:
        out.append(_LegacyKey("groq", groq))
    if anthropic:
        out.append(_LegacyKey("anthropic", anthropic))
    return out


def is_configured():
    """هل يوجد أي مفتاح (مفعّل) يمكن استخدامه من حيث المبدأ؟"""
    return AIProviderKey.objects.filter(is_active=True).exists() or bool(_legacy_keys())


def _mark_attempt(key):
    if key.id is None:
        return
    today = timezone.localdate()
    now = timezone.now()
    if key.used_date != today:
        AIProviderKey.objects.filter(pk=key.id).update(used_today=1, used_date=today,
                                                       total_requests=F("total_requests") + 1, last_used_at=now)
    else:
        AIProviderKey.objects.filter(pk=key.id).update(used_today=F("used_today") + 1,
                                                       total_requests=F("total_requests") + 1, last_used_at=now)


def _mark_success(key):
    if key.id is None:
        return
    AIProviderKey.objects.filter(pk=key.id).update(last_success_at=timezone.now(), last_error="", cooldown_until=None)


def _mark_failure(key, err):
    if key.id is None:
        return
    until = timezone.now() + timedelta(seconds=err.cooldown) if err.cooldown else None
    AIProviderKey.objects.filter(pk=key.id).update(total_failures=F("total_failures") + 1,
                                                   last_error=str(err)[:300], cooldown_until=until)


# ------------------------------------------------------------------ HTTP
def _style(key):
    url = (key.base_url or "").lower()
    if key.provider == "custom":
        return "anthropic" if (url.rstrip("/").endswith("/messages") or "anthropic.com" in url) else "openai"
    return key.defaults["style"]


def _endpoint(key, style):
    base = (key.base_url or "").strip() or key.defaults["base_url"]
    base = base.rstrip("/")
    if not base:
        raise _KeyError("لم يُحدَّد رابط المزوّد.", cooldown=3600, kind="config")
    tail = "/messages" if style == "anthropic" else "/chat/completions"
    return base if base.endswith(tail) else base + tail


def _error_text(raw):
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            data = data[0]
        err = data.get("error", data) if isinstance(data, dict) else {}
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or err)[:300]
        return str(err)[:300]
    except Exception:
        return (raw or "")[:300]


def _post(url, headers, payload, timeout):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **headers}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    last = None
    for attempt in range(2):  # محاولة ثانية فورية لأعطال الشبكة العابرة فقط
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise _http_error(e.code, _error_text(raw), e.headers.get("Retry-After") if e.headers else None)
        except (socket.timeout, TimeoutError):
            last = _KeyError("انتهت مهلة الاتصال بالمزوّد (الإنترنت بطيء أو الخدمة لا تستجيب).", cooldown=15, kind="timeout")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
                raise _KeyError("فشل التحقق من شهادة الأمان (SSL). غالبًا برنامج حماية أو VPN أو وقت الجهاز غير صحيح يعترض الاتصال.",
                                cooldown=60, kind="network")
            if isinstance(reason, (socket.timeout, TimeoutError)):
                last = _KeyError("انتهت مهلة الاتصال بالمزوّد (الإنترنت بطيء أو الخدمة لا تستجيب).", cooldown=15, kind="timeout")
            else:
                last = _KeyError(f"تعذّر الاتصال بالمزوّد: {reason}", cooldown=15, kind="network")
        except json.JSONDecodeError:
            raise _KeyError("ردّ غير مفهوم من المزوّد (ليس JSON). تحقق من الرابط.", cooldown=300, kind="config")
        if attempt == 0:
            time.sleep(1.0)
    raise last


_RETRY_IN_RE = re.compile(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", re.I)


def _suggested_wait(retry_after, msg):
    """الثواني التي يقترح المزوّد انتظارها: من ترويسة Retry-After أو من نص الرسالة (Please try again in 7.5s / 1m3.2s)."""
    try:
        if retry_after:
            return max(int(float(retry_after)), 1)
    except (TypeError, ValueError):
        pass
    m = _RETRY_IN_RE.search(msg or "")
    if m and any(m.groups()):
        h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
        return max(int(h * 3600 + mi * 60 + sec + 0.999), 1)
    return 0


def _http_error(code, msg, retry_after):
    low = (msg or "").lower()
    if code == 429:
        suggested = _suggested_wait(retry_after, msg)
        wait = min(max(suggested, 5), 600) if suggested else 30
        daily = any(w in low for w in ("per day", "daily", "tokens per day", "quota", "billing"))
        if daily:
            wait = max(wait, 3 * 3600)
        return _KeyError(f"تجاوز حدّ الاستخدام (429): {msg}", cooldown=wait, kind="rate", retry_after=suggested, daily=daily)
    if code == 413 or any(w in low for w in ("request too large", "reduce your message size", "context length", "too many tokens")):
        return _KeyError(f"حجم الطلب أكبر من المسموح للنموذج ({code}): {msg}", cooldown=0, kind="too_large")
    if code in (401, 403):
        if any(w in low for w in ("1010", "1020", "cloudflare", "access denied", "browser_signature", "<html")):
            return _KeyError(f"رُفض الطلب من حماية المزوّد/الشبكة ({code}) وليس بسبب المفتاح: {msg}", cooldown=60, kind="blocked")
        return _KeyError(f"المفتاح غير صالح أو غير مصرّح له ({code}): {msg}", cooldown=1800, kind="auth")
    if code == 402:
        return _KeyError(f"الرصيد غير كافٍ ({code}): {msg}", cooldown=1800, kind="auth")
    if code == 404 or any(w in low for w in ("does not exist", "model_not_found", "decommissioned", "not supported", "deprecated")):
        return _KeyError(f"النموذج أو الرابط غير صحيح ({code}): {msg}", cooldown=600, kind="config")
    if code in (400, 422):
        return _KeyError(f"طلب مرفوض ({code}): {msg}", cooldown=0, kind="bad_request")
    return _KeyError(f"خطأ من المزوّد ({code}): {msg}", cooldown=20, kind="server")


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def _clean(text):
    return _THINK_RE.sub("", text or "").strip()


def _alternate(messages):
    """يضمن تبادل الأدوار user/assistant وبدء المحادثة بـ user (شرط Anthropic)."""
    out = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append({"role": m["role"], "content": m["content"]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _call_openai(key, system, messages, max_tokens, temperature, timeout):
    url = _endpoint(key, "openai")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key.api_key}"}
    msgs = ([{"role": "system", "content": system}] if system else []) + _alternate(messages)
    payload = {"model": key.effective_model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature}
    if not payload["model"]:
        raise _KeyError("لم يُحدَّد اسم النموذج لهذا المفتاح.", cooldown=3600, kind="config")
    if "gpt-oss" in (payload["model"] or "").lower():
        payload["reasoning_effort"] = "low"  # أسرع وأقل استهلاكًا للرموز؛ يُحذف تلقائيًا لو رفضه المزوّد
    for _ in range(4):  # تعديل المعاملات لو رفضها النموذج (نماذج الاستدلال الجديدة)
        try:
            data = _post(url, headers, payload, timeout)
            break
        except _KeyError as e:
            low = str(e).lower()
            if e.kind == "bad_request" and "max_completion_tokens" in low and "max_tokens" in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
                continue
            if e.kind == "bad_request" and "temperature" in low and "temperature" in payload:
                payload.pop("temperature")
                continue
            if e.kind == "bad_request" and "reasoning" in low and "reasoning_effort" in payload:
                payload.pop("reasoning_effort")
                continue
            raise
    else:
        raise _KeyError("تعذّر ضبط معاملات الطلب لهذا النموذج.", cooldown=0, kind="bad_request")
    try:
        content = data["choices"][0]["message"].get("content")
    except Exception:
        raise _KeyError("صيغة ردّ غير متوقعة من المزوّد.", cooldown=900, kind="config")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = _clean(content)
    if not text:
        raise _KeyError("ردّ فارغ من النموذج.", cooldown=0, kind="empty")
    return text


def _call_anthropic(key, system, messages, max_tokens, temperature, timeout):
    url = _endpoint(key, "anthropic")
    headers = {"Content-Type": "application/json", "x-api-key": key.api_key, "anthropic-version": "2023-06-01"}
    if not key.effective_model:
        raise _KeyError("لم يُحدَّد اسم النموذج لهذا المفتاح.", cooldown=3600, kind="config")
    payload = {"model": key.effective_model, "max_tokens": max_tokens, "temperature": temperature,
               "messages": _alternate(messages)}
    if system:
        payload["system"] = system
    data = _post(url, headers, payload, timeout)
    try:
        text = _clean("".join(b.get("text", "") for b in data["content"] if b.get("type") == "text"))
    except Exception:
        raise _KeyError("صيغة ردّ غير متوقعة من المزوّد.", cooldown=900, kind="config")
    if not text:
        raise _KeyError("ردّ فارغ من النموذج.", cooldown=0, kind="empty")
    return text


def _call_one(key, system, messages, max_tokens, temperature, timeout):
    if _style(key) == "anthropic":
        return _call_anthropic(key, system, messages, max_tokens, temperature, timeout)
    return _call_openai(key, system, messages, max_tokens, temperature, timeout)


# ------------------------------------------------------------------ الواجهة العامة
def chat(system, messages, max_tokens=2000, temperature=0.2, timeout=DEFAULT_TIMEOUT, max_attempts=4):
    """يرسل المحادثة ويعيد (النص, اسم المفتاح المستخدم). يجرّب مفاتيح متعددة عند الفشل."""
    available, all_keys = _pool()
    candidates = list(available)
    if not all_keys:  # لم تُضَف مفاتيح في اللوحة → المفاتيح القديمة كخيار احتياطي
        candidates = _legacy_keys()
    elif not candidates:
        # كل المفاتيح في فترة انتظار: لا نترك الخدمة ميتة؛ نجرّب المفتاح الأقرب لانتهاء انتظاره (إن لم يبلغ حدّه اليومي)
        cooling = sorted((k for k in all_keys if k.in_cooldown and not k.over_daily_limit),
                         key=lambda k: k.cooldown_until)
        candidates = cooling[:1]
    if not candidates:
        if all_keys:
            raise AIUnavailable("كل مفاتيح الذكاء الاصطناعي بلغت حدّها اليومي.", kind="rate", daily=True)
        raise AINotConfigured("لم يتم ضبط أي مفتاح للذكاء الاصطناعي.")
    last = None
    for key in candidates[:max_attempts]:
        _mark_attempt(key)
        try:
            text = _call_one(key, system, messages, max_tokens, temperature, timeout)
            _mark_success(key)
            return text, key.name
        except _KeyError as e:
            _mark_failure(key, e)
            log.warning("AI key '%s' (%s / %s) failed [%s]: %s", key.name, key.provider, key.effective_model, e.kind, e)
            last = e
    raise AIUnavailable(str(last) if last else "تعذّر الوصول إلى خدمة الذكاء الاصطناعي.", kind=last.kind if last else "unknown",
                        retry_after=getattr(last, "retry_after", 0), daily=getattr(last, "daily", False))


def test_key(key, timeout=30):
    """اختبار مفتاح بعينه. يعيد (نجح؟, رسالة, زمن بالمللي ثانية)."""
    t0 = time.time()
    _mark_attempt(key)
    try:
        text = _call_one(key, "", [{"role": "user", "content": "اكتب كلمة واحدة فقط: تم"}], 300, 0, timeout)
        _mark_success(key)
        return True, text[:80], int((time.time() - t0) * 1000)
    except _KeyError as e:
        e.cooldown = 0  # الاختبار اليدوي لا يوقف المفتاح
        _mark_failure(key, e)
        return False, str(e), int((time.time() - t0) * 1000)
