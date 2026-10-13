"""ملفات المنهج الكاملة (PDF): استخراج نص كل صفحة في الخلفية، فهرسة العناوين، البحث والقراءة بالصفحات.

يستخدم الوكيل هذه الدوال ليجد درسًا بعينه داخل الكتاب ويقرأ نصه الحقيقي قبل شرحه أو وضع اختبار عليه.
لا مكتبات إضافية: pdfplumber (موجودة أصلًا في المشروع) + OCR اختياري إن كان Tesseract/Poppler مثبّتين.
"""
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter

from . import knowledge as kn

log = logging.getLogger(__name__)

MAX_PAGES = 600
MAX_PAGE_CHARS = 12000
_ARABIC = re.compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")


# ------------------------------------------------------------------ تنظيف النص
def _arabic_ratio(text):
    letters = [c for c in text if c.isalpha()]
    return (sum(1 for c in letters if _ARABIC.match(c)) / len(letters)) if letters else 0.0


def fix_rtl_line(line):
    """بعض ملفات PDF العربية تُستخرج بترتيب بصري معكوس (الكلمات والحروف مقلوبة). نكتشف ذلك بعلامات قوية
    (كلمات تبدأ بـ«ال» مقابل كلمات تنتهي بـ«لا») ولا نقلب إلا عند وجود دليل واضح."""
    words = line.split()
    if len(words) < 2 or _arabic_ratio(line) < 0.5:
        return line
    normal = sum(1 for w in words if w.startswith("ال") and len(w) > 3)
    reversed_ = sum(1 for w in words if w.endswith("لا") and len(w) > 3)
    if reversed_ >= 2 and reversed_ >= 2 * max(normal, 1):
        return " ".join(w[::-1] for w in reversed(words))
    return line


def clean_page_text(text):
    text = unicodedata.normalize("NFKC", text or "")  # يفكّ أشكال العرض العربية (ﻣ ﺤ ﻤ ﺪ → محمد)
    text = text.replace("\x00", " ")
    lines = [fix_rtl_line(l.rstrip()) for l in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_PAGE_CHARS]


# ------------------------------------------------------------------ الفهرسة (العناوين)
_ORD_AR = (r"(?:الأول[ىة]?|الاول[ىة]?|الثاني[ةه]?|الثانى|الثالث[ةه]?|الرابع[ةه]?|الخامس[ةه]?|السادس[ةه]?|السابع[ةه]?|"
           r"الثامن[ةه]?|التاسع[ةه]?|العاشر[ةه]?|الحادي عشر|الحادية عشرة|الثاني عشر|الثانية عشرة|\d+|[٠-٩]+)")
_HEAD_RES = [
    (re.compile(r"^\s*(?:الوحدة|الباب|الفصل|الجزء|المحور)\s+" + _ORD_AR + r"\b.{0,80}$"), 1),
    (re.compile(r"^\s*(?:الدرس|المحاضرة|الحصة|درس)\s+" + _ORD_AR + r"\b.{0,80}$"), 2),
    (re.compile(r"^\s*(?:unit|chapter|module|part|section)\s*[\-:.]?\s*(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b.{0,80}$", re.I), 1),
    (re.compile(r"^\s*(?:lesson|lec(?:ture)?|topic)\s*[\-:.]?\s*(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b.{0,80}$", re.I), 2),
]
_TOC_LINE = re.compile(r"(?:\.{3,}|…|\s{2,}|\s)\s*[\d٠-٩]{1,4}\s*$")


def _is_toc_page(text):
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 6:
        return False
    dotted = sum(1 for l in lines if re.search(r"\.{4,}|…{2,}", l))
    return dotted >= max(4, len(lines) // 3)


def build_outline(pages):
    """pages: [(number, text)] → قائمة عناوين مرتّبة [{"title","page","level"}] (يتجاهل صفحات الفهرس نفسها)."""
    out, seen = [], set()
    for number, text in pages:
        if not text or _is_toc_page(text):
            continue
        for raw in text.split("\n")[:40]:  # العناوين الحقيقية تظهر غالبًا في أعلى الصفحة
            line = raw.strip()
            if not line or len(line) > 110:
                continue
            for rx, level in _HEAD_RES:
                if rx.match(line):
                    key = (re.sub(r"\s+", " ", kn.norm(line)), number)
                    if key not in seen:
                        seen.add(key)
                        out.append({"title": line, "page": number, "level": level})
                    break
    out.sort(key=lambda r: (r["page"], r["level"]))
    return out[:400]


# ------------------------------------------------------------------ البحث (BM25 على الصفحات)
_index_cache = {}  # doc_id -> (signature, index)
_index_lock = threading.Lock()


class _Index:
    def __init__(self, pages):
        self.pages = pages  # [(number, text)]
        self.tfs, self.lens = [], []
        df = Counter()
        for _, text in pages:
            toks = kn.tokens(text)
            tf = Counter(toks)
            self.tfs.append(tf)
            self.lens.append(max(len(toks), 1))
            df.update(tf.keys())
        self.df = df
        self.n = max(len(pages), 1)
        self.avgdl = sum(self.lens) / self.n if pages else 1.0

    def search(self, query, limit=5):
        q = list(dict.fromkeys(kn.tokens(query)))
        if not q:
            return []
        scored = []
        for i, tf in enumerate(self.tfs):
            s, hit = 0.0, 0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                hit += 1
                idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * self.lens[i] / self.avgdl))
            if s > 0:
                scored.append((s * (1 + 0.15 * hit), i))
        scored.sort(reverse=True)
        return [(self.pages[i][0], sc) for sc, i in scored[:limit]]


def _get_index(doc):
    from .models import CurriculumPage
    sig = (doc.pages_done, doc.page_count, doc.status)
    with _index_lock:
        hit = _index_cache.get(doc.id)
        if hit and hit[0] == sig:
            return hit[1]
    pages = list(CurriculumPage.objects.filter(doc=doc).order_by("number").values_list("number", "text"))
    idx = _Index(pages)
    with _index_lock:
        if len(_index_cache) > 8:
            _index_cache.clear()
        _index_cache[doc.id] = (sig, idx)
    return idx


def _snippet(text, query, width=260):
    toks = set(kn.tokens(query))
    flat = re.sub(r"\s+", " ", text)
    best = 0
    for m in re.finditer(r"\S+", flat):
        if kn.tokens(m.group(0)) and set(kn.tokens(m.group(0))) & toks:
            best = m.start()
            break
    start = max(0, best - width // 4)
    return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")


def search_pages(doc, query, limit=5):
    """يعيد [{"page", "snippet", "score"}] لأنسب الصفحات للاستعلام."""
    idx = _get_index(doc)
    page_text = dict(idx.pages)
    return [{"page": n, "snippet": _snippet(page_text.get(n, ""), query), "score": round(sc, 2)}
            for n, sc in idx.search(query, limit)]


def read_pages(doc, first, last, max_chars=9000):
    """نص الصفحات [first..last] مع علامات الصفحات. يعيد (النص, آخر صفحة قُرئت كاملة, هل اقتُطع؟)."""
    from .models import CurriculumPage
    first = max(1, int(first))
    last = min(int(last), first + 14, doc.page_count or int(last))  # حدّ أقصى 15 صفحة للطلب الواحد
    out, used, done, cut = [], 0, first - 1, False
    for p in CurriculumPage.objects.filter(doc=doc, number__gte=first, number__lte=last).order_by("number"):
        t = p.text or "(صفحة بلا نص مقروء)"
        block = f"--- صفحة {p.number} ---\n{t}"
        if used + len(block) > max_chars and out:
            cut = True
            break
        out.append(block[:max_chars])
        used += len(block)
        done = p.number
    return "\n\n".join(out), done, cut


# ------------------------------------------------------------------ الاستخراج في الخلفية
def _ocr_page(path, number):
    try:
        from pdf2image import convert_from_path
        from examimport.services import _ocr_image
        imgs = convert_from_path(path, first_page=number, last_page=number, dpi=170)
        return _ocr_image(imgs[0]) if imgs else ""
    except Exception as e:  # Tesseract/Poppler غير مثبّتين أو فشلت الصفحة
        log.info("OCR skipped for page %s: %s", number, e)
        return ""


def process_doc(doc_id):
    """يشتغل في خيط خلفي: يقرأ صفحات الملف صفحة صفحة ويخزّنها ثم يبني فهرس العناوين."""
    from django.db import close_old_connections
    from .models import CurriculumDoc, CurriculumPage
    close_old_connections()
    try:
        doc = CurriculumDoc.objects.get(pk=doc_id)
        try:
            import pdfplumber
        except Exception:
            raise RuntimeError("مكتبة pdfplumber غير مثبّتة على الخادم.")
        path = doc.file.path
        started = time.time()
        with pdfplumber.open(path) as pdf:
            total = min(len(pdf.pages), MAX_PAGES)
            CurriculumDoc.objects.filter(pk=doc.pk).update(page_count=total)
            ocr_ok = True
            rows = []
            for i in range(total):
                try:
                    txt = pdf.pages[i].extract_text() or ""
                except Exception:
                    txt = ""
                if len(txt.strip()) < 25 and ocr_ok:
                    o = _ocr_page(path, i + 1)
                    if o.strip():
                        txt = o
                        CurriculumDoc.objects.filter(pk=doc.pk).update(used_ocr=True)
                    elif i >= 2 and not any(r[1].strip() for r in rows[-3:]):
                        ocr_ok = False  # لا فائدة من تكرار محاولات OCR الفاشلة صفحة بعد صفحة
                try:
                    pdf.pages[i].flush_cache()
                except Exception:
                    pass
                rows.append((i + 1, clean_page_text(txt)))
                if len(rows) >= 10 or i == total - 1:
                    CurriculumPage.objects.bulk_create(
                        [CurriculumPage(doc=doc, number=n, text=t) for n, t in rows], ignore_conflicts=True)
                    CurriculumDoc.objects.filter(pk=doc.pk).update(pages_done=i + 1)
                    rows = []
        pages = list(CurriculumPage.objects.filter(doc=doc).order_by("number").values_list("number", "text"))
        if not any(t.strip() for _, t in pages):
            CurriculumDoc.objects.filter(pk=doc.pk).update(
                status="failed", error="لم أستطع قراءة أي نص من الملف (قد يكون صورًا ممسوحة ضوئيًا ولا يوجد OCR على الخادم).")
        else:
            CurriculumDoc.objects.filter(pk=doc.pk).update(status="ready", outline=build_outline(pages), error="")
        log.info("curriculum doc %s processed in %.1fs", doc_id, time.time() - started)
    except Exception as e:
        log.exception("curriculum doc %s failed", doc_id)
        try:
            CurriculumDoc.objects.filter(pk=doc_id).update(status="failed", error=str(e)[:290])
        except Exception:
            pass
    finally:
        close_old_connections()


def start_processing(doc_id):
    t = threading.Thread(target=process_doc, args=(doc_id,), name=f"curriculum-{doc_id}", daemon=True)
    t.start()
    return t


def mark_stale_failed():
    """ملفات علقت في «قيد القراءة» (انتهت العملية قبل إكمالها) تُعلَّم فاشلة بعد 30 دقيقة."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import CurriculumDoc
    CurriculumDoc.objects.filter(status="processing", created_at__lt=timezone.now() - timedelta(minutes=30)).update(
        status="failed", error="توقفت القراءة قبل الاكتمال. أعد رفع الملف.")
