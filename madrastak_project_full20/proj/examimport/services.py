"""نظام استيراد الاختبارات — محرك الاستخراج والتحليل.

يحوّل ملف PDF أو صورة/مجموعة صور رفعها المدرّس إلى نص، ثم يحلّل هذا النص
لاستخراج الأسئلة واختياراتها وإجاباتها الصحيحة، حسب المراحل الموصوفة في
مواصفة النظام: رفع الملف ← استخراج النص ← تحليل البنية ← تحديد الأسئلة
← تحديد الاختيارات ← تحديد الإجابة الصحيحة ← تحويلها إلى بيانات منظّمة
← مراجعة المدرّس.

ملاحظة مهمة: هذا محلّل قائم على قواعد نصية (regex) يغطي الصيغ الشائعة
الموصوفة في المواصفة (ترقيم الأسئلة بالأرقام، اختيارات A/B/C/D أو (أ)(ب)(ج)(د)،
علامة صح ✓ أو Answer Key في نهاية الملف). لأي حالة غامضة يضع النظام السؤال
في حالة "يحتاج مراجعة" أو "تعذّر تحليله" بدل تخمين إجابة قد تكون خاطئة،
تمامًا كما تنص المواصفة على ضرورة مراجعة المدرّس قبل اعتماد أي استيراد.
"""
import os
import re

from django.conf import settings
from django.utils import timezone

from .models import ImportedChoice, ImportedQuestion

# ===================== استخراج النص من الملفات =====================

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _ocr_image(image):
    """يشغّل التعرف الضوئي على النص (OCR) على صورة واحدة، مع تراجع تلقائي إلى
    اللغة الإنجليزية فقط إن كانت حزمة اللغة المطلوبة غير مثبّتة على الخادم."""
    import pytesseract

    langs = getattr(settings, "OCR_LANGS", "eng")
    try:
        return pytesseract.image_to_string(image, lang=langs)
    except Exception:
        try:
            return pytesseract.image_to_string(image, lang="eng")
        except Exception as e:
            raise RuntimeError(
                "تعذّر تشغيل خاصية التعرف الضوئي على النص (OCR). "
                "تأكد من تثبيت Tesseract على الخادم."
            ) from e


def _pdf_page_has_text(page, min_chars=25):
    try:
        return len((page.extract_text() or "").strip()) >= min_chars
    except Exception:
        return False


def extract_text_from_pdf(path):
    """يستخرج النص من ملف PDF: مباشرة إن كان نصيًا، وبالتعرف الضوئي (OCR) للصفحات
    الممسوحة ضوئيًا. يعيد (النص الكامل لكل صفحة كقائمة, هل استُخدم OCR لأي صفحة)."""
    try:
        import pdfplumber
    except Exception as e:
        raise RuntimeError(
            "مكتبة pdfplumber غير مثبّتة على الخادم، وهي مطلوبة لقراءة ملفات PDF."
        ) from e

    page_texts = []
    used_ocr = False
    pages_needing_ocr = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if _pdf_page_has_text(page):
                page_texts.append(page.extract_text() or "")
            else:
                page_texts.append(None)
                pages_needing_ocr.append(len(page_texts) - 1)

    if pages_needing_ocr:
        used_ocr = True
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(path)
        except Exception as e:
            raise RuntimeError(
                "تعذّر تحويل صفحات PDF الممسوحة ضوئيًا إلى صور لتشغيل التعرف الضوئي. "
                "تأكد من تثبيت Poppler على الخادم (poppler-utils)."
            ) from e
        for i in pages_needing_ocr:
            if i < len(images):
                page_texts[i] = _ocr_image(images[i])

    return [t or "" for t in page_texts], used_ocr


def extract_text_from_image(path):
    """يستخرج النص من صورة واحدة عبر التعرف الضوئي على النص."""
    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError("مكتبة Pillow غير مثبّتة على الخادم.") from e
    with Image.open(path) as im:
        return _ocr_image(im)


# ===================== تحليل النص إلى أسئلة =====================

_LETTER_INDEX = {
    "A": 1, "B": 2, "C": 3, "D": 4,
    "أ": 1, "ب": 2, "ج": 3, "د": 4,
}

QSTART_RE = re.compile(r"(?m)^[ \t]*(?:[Qq](?:uestion)?\.?\s*)?(\d{1,3})\s*[\.\-\)\:]\s+\S")
CHOICE_RE = re.compile(r"^[ \t]*\(?\s*([A-Da-d]|[أابجد])\s*\)?\s*[\.\)\:\-]\s+(.+?)\s*$")
CORRECT_MARK_RE = re.compile(
    r"\s*[\(\[]?(?:correct(?:\s*answer)?|selected\s*answer|answer\s*selected|"
    r"الإجابة الصحيحة|إجابة صحيحة|الإجابة المختارة|صحيحة|صحيح|✓|✔|√)[\)\]]?\s*$",
    re.IGNORECASE,
)
ANSWER_KEY_HEADER_RE = re.compile(
    r"(?im)^\s*(answer\s*key|answers?|model\s*answers?|الإجابات|نموذج الإجابة|الإجابة النموذجية)\s*[:\-]?\s*$"
)
ANSWER_ITEM_RE = re.compile(r"(\d{1,3})\s*[\.\-\)\:]\s*([A-Da-dأبجد])\b")
_TF_WORDS = {"true", "false", "صح", "خطأ", "صحيح", "خاطئ"}


def _letter_index(letter):
    return _LETTER_INDEX.get((letter or "").strip().upper()) or _LETTER_INDEX.get((letter or "").strip())


def _split_candidate_blocks(text):
    """يقسّم النص الكامل إلى كتل، كل كتلة تبدأ عند رقم سؤال مرشّح."""
    starts = [m.start() for m in QSTART_RE.finditer(text)]
    if not starts:
        return [text] if text.strip() else []
    starts.append(len(text))
    return [text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def _merge_weak_blocks(blocks):
    """كتلة لا تحتوي على اختيارَين على الأقل غالبًا ليست بداية سؤال حقيقية
    (قد تكون رقمًا ضمن الشرح)، فتُلحق بالكتلة السابقة بدل اعتبارها سؤالًا مستقلًا."""
    merged = []
    for block in blocks:
        choice_hits = [ln for ln in block.splitlines() if CHOICE_RE.match(ln.strip())]
        if len(choice_hits) < 2 and merged:
            merged[-1] += block
        else:
            merged.append(block)
    return merged


def parse_answer_key(text):
    """يبحث عن قائمة إجابات في نهاية الملف (Answer Key) ويعيدها كقاموس {رقم السؤال: الحرف}."""
    header = ANSWER_KEY_HEADER_RE.search(text)
    region = text[header.end():] if header else text
    items = ANSWER_ITEM_RE.findall(region)
    if not header and len(items) < 5:
        return {}
    result = {}
    for num, letter in items:
        result[int(num)] = letter
    return result


def _parse_block(block):
    lines = block.splitlines()
    choice_idx = [i for i, ln in enumerate(lines) if CHOICE_RE.match(ln.strip())]
    q_lines = lines[:choice_idx[0]] if choice_idx else lines
    qtext = "\n".join(q_lines).strip()
    qtext = re.sub(
        r"^[ \t]*(?:[Qq](?:uestion)?\.?\s*)?\d{1,3}\s*[\.\-\)\:]\s*", "", qtext, count=1
    ).strip()

    choices = []
    for i in choice_idx:
        m = CHOICE_RE.match(lines[i].strip())
        letter, ctext = m.group(1), m.group(2)
        is_correct = bool(CORRECT_MARK_RE.search(ctext))
        ctext = CORRECT_MARK_RE.sub("", ctext).strip()
        choices.append({"letter": letter, "text": ctext, "is_correct": is_correct})

    correct_index = next((i + 1 for i, c in enumerate(choices) if c["is_correct"]), None)
    qnum_match = re.match(r"^[ \t]*(?:[Qq](?:uestion)?\.?\s*)?(\d{1,3})", block)
    qnum = int(qnum_match.group(1)) if qnum_match else None

    return {
        "number": qnum,
        "text": qtext,
        "choices": choices,
        "correct_index": correct_index,
        "raw": block.strip(),
    }


def parse_questions(full_text):
    """يحلّل نصًا كاملًا ويعيد قائمة أسئلة منظّمة (dict لكل سؤال)."""
    blocks = _merge_weak_blocks(_split_candidate_blocks(full_text))
    parsed = [_parse_block(b) for b in blocks if b.strip()]

    answer_key = parse_answer_key(full_text)
    for p in parsed:
        if not p["correct_index"] and p["number"] and p["number"] in answer_key:
            idx = _letter_index(answer_key[p["number"]])
            if idx and idx <= len(p["choices"]):
                for c in p["choices"]:
                    c["is_correct"] = False
                p["choices"][idx - 1]["is_correct"] = True
                p["correct_index"] = idx

    for p in parsed:
        texts = {c["text"].strip().lower() for c in p["choices"]}
        if len(p["choices"]) == 2 and texts and texts.issubset(_TF_WORDS):
            p["qtype"] = "tf"
        else:
            p["qtype"] = "mcq"

    return parsed


def _score(parsed):
    notes = []
    score = 30
    n_choices = len(parsed["choices"])
    if n_choices >= 4:
        score += 30
    elif n_choices == 2:
        score += 20
    elif n_choices == 3:
        score += 15
        notes.append("تم العثور على 3 اختيارات فقط")
    else:
        notes.append("لم يُعثر على اختيارات كافية لهذا السؤال")

    if parsed["correct_index"]:
        score += 25
    else:
        notes.append("تعذّر تحديد الإجابة الصحيحة تلقائيًا")

    if parsed["text"] and len(parsed["text"]) >= 8:
        score += 15
    else:
        notes.append("نص السؤال قصير جدًا أو غير واضح")

    score = min(score, 100)
    if n_choices == 0 or not parsed["text"]:
        status = "unparsed"
    elif score >= 75 and parsed["correct_index"]:
        status = "ready"
    else:
        status = "needs_review"
    return score, status, "، ".join(notes)


# ===================== تنسيق العملية الكاملة =====================

def run_import_batch(batch):
    """ينفّذ خط الأنابيب الكامل: استخراج النص من كل ملف في الدفعة، تحليله إلى
    أسئلة، وحفظها كـ ImportedQuestion بانتظار مراجعة المدرّس."""
    batch.status = "processing"
    batch.save(update_fields=["status"])
    try:
        files = list(batch.files.order_by("order", "id"))
        segments = []  # (page_number, text)
        any_ocr = False
        page_no = 0
        for f in files:
            path = f.file.path
            ext = os.path.splitext(f.file.name)[1].lower()
            if ext == ".pdf":
                page_texts, used_ocr = extract_text_from_pdf(path)
                any_ocr = any_ocr or used_ocr
                for t in page_texts:
                    page_no += 1
                    segments.append((page_no, t))
            elif ext in IMAGE_EXTENSIONS:
                page_no += 1
                segments.append((page_no, extract_text_from_image(path)))
            else:
                raise RuntimeError(f"صيغة ملف غير مدعومة: {f.file.name}")

        if files and os.path.splitext(files[0].file.name)[1].lower() == ".pdf":
            batch.source_type = "pdf_scanned" if any_ocr else "pdf_text"
        else:
            batch.source_type = "images" if len(files) > 1 else "image"

        full_text = "\n\n".join(t for _, t in segments)
        batch.raw_text = full_text[:20000]

        parsed_list = parse_questions(full_text)

        # حساب حدود الصفحات لتحديد رقم الصفحة التقريبي لكل سؤال
        boundaries = []
        offset = 0
        for pno, seg_text in segments:
            offset += len(seg_text)
            boundaries.append((offset, pno))
            offset += 2  # طول الفاصل "\n\n"

        def page_for(idx):
            if idx == -1 or not boundaries:
                return None
            for limit, pno in boundaries:
                if idx < limit:
                    return pno
            return boundaries[-1][1]

        batch.questions.all().delete()
        cursor = 0
        for order, parsed in enumerate(parsed_list, start=1):
            needle = parsed["raw"][:40]
            idx = full_text.find(needle, cursor) if needle else -1
            if idx == -1 and needle:
                idx = full_text.find(needle)
            if idx != -1:
                cursor = idx + len(needle)

            confidence, status, notes = _score(parsed)
            iq = ImportedQuestion.objects.create(
                batch=batch,
                order=order,
                raw_block=parsed["raw"],
                text=parsed["text"],
                qtype=parsed.get("qtype", "mcq"),
                correct_index=parsed["correct_index"],
                confidence=confidence,
                status=status,
                notes=notes,
                page_number=page_for(idx),
            )
            for i, c in enumerate(parsed["choices"], start=1):
                ImportedChoice.objects.create(
                    question=iq, text=c["text"], is_correct=(i == parsed["correct_index"]), order=i
                )

        if parsed_list:
            batch.status = "ready"
        else:
            batch.status = "failed"
            batch.error_message = (
                "لم يتمكّن النظام من العثور على أي أسئلة داخل الملف. "
                "تأكد من صيغة الملف، أو أدخل الأسئلة يدويًا."
            )
        batch.processed_at = timezone.now()
        batch.save()
    except Exception as e:
        batch.status = "failed"
        batch.error_message = str(e)[:2000]
        batch.processed_at = timezone.now()
        batch.save()
    return batch
