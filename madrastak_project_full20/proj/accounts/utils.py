import re

from django.http import FileResponse, Http404, HttpResponse

from .models import AuditLog

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_phone(value):
    value = (value or "").translate(AR_DIGITS)
    return re.sub(r"[\s\-+]", "", value)


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


def audit(request, action, target="", detail=""):
    actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    AuditLog.objects.create(actor=actor, action=action, target=str(target)[:200], detail=detail, ip=client_ip(request))


def serve_private(request, fieldfile, download_name=None):
    """يخدم ملفًا خاصًا مع دعم Range لتشغيل الفيديو."""
    if not fieldfile:
        raise Http404
    try:
        f = fieldfile.open("rb")
    except FileNotFoundError:
        raise Http404
    size = fieldfile.size
    rng = request.META.get("HTTP_RANGE", "")
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    import mimetypes
    ctype = mimetypes.guess_type(fieldfile.name)[0] or "application/octet-stream"
    if m and (m.group(1) or m.group(2)):
        start = int(m.group(1)) if m.group(1) else max(size - int(m.group(2)), 0)
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        f.seek(start)
        data = f.read(end - start + 1)
        f.close()
        resp = HttpResponse(data, status=206, content_type=ctype)
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        resp = FileResponse(f, content_type=ctype)
        if download_name:
            resp["Content-Disposition"] = f'attachment; filename="{download_name}"'
    resp["Accept-Ranges"] = "bytes"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


def get_ai_keys():
    """يعيد (مفتاح Groq، مفتاح Anthropic) لتشغيل ميزات الذكاء الاصطناعي في المنصة.

    الأولوية للقيمة التي يضبطها صاحب المنصة من لوحة تحكّم المالك (إعدادات المنصة)،
    فإن كانت فارغة يُستخدم متغير البيئة على الخادم (GROQ_API_KEY / ANTHROPIC_API_KEY)
    كتراجع آمن لمن يفضّل ضبطه من الخادم مباشرة. لا حاجة لإعادة تشغيل السيرفر عند
    تغيير المفتاح من لوحة التحكّم؛ يُقرأ من قاعدة البيانات في كل استخدام.
    """
    from django.conf import settings as dj_settings

    from .models import SiteSetting

    s = SiteSetting.get()
    groq = (s.groq_api_key or "").strip() or (getattr(dj_settings, "GROQ_API_KEY", "") or "")
    anthropic = (s.anthropic_api_key or "").strip() or (getattr(dj_settings, "ANTHROPIC_API_KEY", "") or "")
    return groq, anthropic
