"""حضور السنتر — قراءة أرقام الطلاب من ملف PDF (أو صورة) وتسجيل الحضور والغياب.

الفكرة: كل طالب له رقم تسلسلي (StudentProfile.student_number: 1، 2، 3 ...). في السنتر
يكتب الطلاب أرقامهم على ورقة، تُحوَّل إلى PDF، فنقرأ الأرقام منه ونطابقها بالطلاب.

طرق القراءة بالترتيب:
  1) طبقة النص داخل الـ PDF (لو الملف مطبوع/مكتوب رقميًا) — فورية ودقيقة.
  2) الذكاء الاصطناعي البصري (Groq أو Anthropic) لو مفتاحه مضبوط في إعدادات المنصة —
     هو الأنسب للخط اليدوي.
  3) التعرف الضوئي Tesseract (نفس الموجود في استيراد الاختبارات) كخيار أخير —
     ضعيف مع الخط اليدوي، ولذلك تُعرض نتيجة القراءة للمدرّس لمراجعتها قبل الحفظ.
"""
import base64
import io
import json
import os
import re

from django.utils import timezone

from accounts.models import StudentProfile
from billing.models import Enrollment
from learning.models import CenterAttendance

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MAX_NUMBER_DIGITS = 6

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DATE_RE = re.compile(r"\b\d{1,4}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,4}\b")
_PHONE_RE = re.compile(r"\b0?1[0125]\d{8}\b")
_NUM_RE = re.compile(r"\d+")


# ------------------------------------------------------------------ تحليل النص إلى أرقام
def parse_numbers(text):
    """يستخرج أرقام الطلاب من نص حر (فواصل/مسافات/أسطر/أرقام عربية). يتجاهل التواريخ
    وأرقام الهواتف، ويحذف التكرار مع الحفاظ على الترتيب."""
    text = (text or "").translate(_AR_DIGITS)
    text = _DATE_RE.sub(" ", text)
    text = _PHONE_RE.sub(" ", text)
    seen, out = set(), []
    for tok in _NUM_RE.findall(text):
        if len(tok) > MAX_NUMBER_DIGITS:
            continue
        n = int(tok)
        if n < 1 or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


# ------------------------------------------------------------------ القراءة من الملفات
def _vision_prompt():
    return (
        "هذه ورقة كتب عليها طلاب أرقامهم (رقم تعريف كل طالب). اقرأ كل الأرقام المكتوبة "
        "بدقة، وتجاهل أي تاريخ أو عنوان أو رقم هاتف. أعد النتيجة بصيغة JSON فقط هكذا: "
        '{"numbers": [1, 5, 12]}'
    )


def _anthropic_read(images_b64, api_key):
    try:
        import anthropic
    except Exception:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
                   for b in images_b64]
        content.append({"type": "text", "text": _vision_prompt()})
        resp = client.messages.create(
            model=os.environ.get("AI_VISION_MODEL_ANTHROPIC", "claude-sonnet-4-6"),
            max_tokens=1500, messages=[{"role": "user", "content": content}])
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return _numbers_from_ai_text(text)
    except Exception:
        return None


def _groq_read(images_b64, api_key):
    try:
        from groq import Groq
    except Exception:
        return None
    try:
        client = Groq(api_key=api_key)
        out = []
        for b in images_b64:  # صورة لكل طلب (حدّ Groq)
            resp = client.chat.completions.create(
                model=os.environ.get("AI_VISION_MODEL_GROQ", "meta-llama/llama-4-scout-17b-16e-instruct"),
                max_tokens=1000, temperature=0,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": _vision_prompt()},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}]}])
            nums = _numbers_from_ai_text(resp.choices[0].message.content or "")
            if nums is None:
                return None
            out.extend(nums)
        return out
    except Exception:
        return None


def _numbers_from_ai_text(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            return [int(x) for x in data.get("numbers", []) if str(x).strip().isdigit()]
        except Exception:
            pass
    return parse_numbers(text)


def _image_to_b64(img, max_side=1800):
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _ocr_digits(img):
    import pytesseract
    cfg = "--psm 6 -c tessedit_char_whitelist=0123456789"
    return pytesseract.image_to_string(img, config=cfg)


def _read_images(images):
    """يقرأ قائمة صور: الذكاء الاصطناعي أولًا إن توفّر، ثم Tesseract. يعيد (أرقام, طريقة)."""
    from accounts.utils import get_ai_keys
    groq_key, anthropic_key = get_ai_keys()
    b64 = [_image_to_b64(i) for i in images]
    if anthropic_key:
        nums = _anthropic_read(b64, anthropic_key)
        if nums is not None:
            return parse_numbers(" ".join(map(str, nums))), "ai"
    if groq_key:
        nums = _groq_read(b64, groq_key)
        if nums is not None:
            return parse_numbers(" ".join(map(str, nums))), "ai"
    text = "\n".join(_ocr_digits(i) for i in images)
    return parse_numbers(text), "ocr"


def extract_numbers(path, filename=""):
    """يعيد (قائمة الأرقام, طريقة القراءة: text|ai|ocr, رسالة تنبيه أو "")."""
    ext = os.path.splitext(filename or path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        from PIL import Image
        with Image.open(path) as im:
            nums, method = _read_images([im.copy()])
        return nums, method, _method_warning(method)

    try:
        import pdfplumber
    except Exception as e:
        raise RuntimeError("مكتبة pdfplumber غير مثبّتة على الخادم، وهي مطلوبة لقراءة ملفات PDF.") from e

    texts, scanned_pages = [], []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            t = (page.extract_text() or "").strip()
            if re.search(r"\d", t):
                texts.append(t)
            else:
                scanned_pages.append(i)
    nums = parse_numbers("\n".join(texts)) if texts else []
    method = "text"
    if scanned_pages:
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(path, dpi=200)
        except Exception as e:
            if not nums:
                raise RuntimeError("تعذّر تحويل صفحات الـ PDF إلى صور. تأكد من تثبيت Poppler على الخادم.") from e
            images = []
        images = [images[i] for i in scanned_pages if i < len(images)]
        if images:
            more, method2 = _read_images(images)
            for n in more:
                if n not in nums:
                    nums.append(n)
            method = method2 if not texts else "text+" + method2
    return nums, method, _method_warning(method)


def _method_warning(method):
    if method.endswith("ocr"):
        return ("قُرئ الملف بالتعرف الضوئي العادي، وهو غير دقيق مع الخط اليدوي. راجع الأرقام جيدًا، "
                "أو فعّل مفتاح Groq/Anthropic من إعدادات المنصة لقراءة أدق.")
    return ""


# ------------------------------------------------------------------ المطابقة والتسجيل
def roster_queryset(teacher, grade=0):
    """طلاب المدرّس الذين يُحسب عليهم الغياب: المشتركون حاليًا + من سبق تسجيل حضورهم في سنتره."""
    now = timezone.now()
    ids = set(Enrollment.objects.filter(teacher=teacher, status="active", expires_at__gt=now)
              .values_list("student_id", flat=True))
    ids |= set(CenterAttendance.objects.filter(teacher=teacher).values_list("student_id", flat=True))
    qs = StudentProfile.objects.filter(user_id__in=ids).select_related("user")
    if grade:
        qs = qs.filter(grade=grade)
    return qs


def build_preview(teacher, numbers, grade=0):
    """يطابق الأرقام بالطلاب ويعيد: الحاضرون، الأرقام غير المعروفة، الغائبون (من الكشف)."""
    profiles = {p.student_number: p for p in
                StudentProfile.objects.filter(student_number__in=numbers).select_related("user")}
    present = [profiles[n] for n in numbers if n in profiles]
    unknown = [n for n in numbers if n not in profiles]
    other_grade = [p for p in present if grade and p.grade != grade]
    present_ids = {p.user_id for p in present}
    absent = [p for p in roster_queryset(teacher, grade).order_by("student_number") if p.user_id not in present_ids]
    return {"present": present, "unknown": unknown, "absent": absent, "other_grade": other_grade}


def apply_attendance(teacher, date, numbers, grade=0, mark_absent=True):
    """يحفظ الحضور (من الأرقام) والغياب (باقي الكشف) ليوم محدد. يعيد (حاضر, غائب)."""
    prev = build_preview(teacher, numbers, grade)
    for p in prev["present"]:
        CenterAttendance.objects.update_or_create(
            teacher=teacher, student=p.user, date=date, defaults={"present": True, "source": "pdf"})
    if mark_absent:
        for p in prev["absent"]:
            CenterAttendance.objects.update_or_create(
                teacher=teacher, student=p.user, date=date, defaults={"present": False, "source": "pdf"})
    return len(prev["present"]), (len(prev["absent"]) if mark_absent else 0)
