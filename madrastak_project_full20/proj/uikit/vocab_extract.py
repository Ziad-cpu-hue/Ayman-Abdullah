"""استخراج مفردات الدرس من ملف PDF/صورة/نص — محليًا أولًا، والذكاء الاصطناعي عند الحاجة فقط.

المسار:
1) قراءة الملف وتحليله محليًا بالكامل (بدون أي استهلاك لمفتاح الذكاء الاصطناعي):
   جداول PDF أو أسطر النص ← فصل الكلمة الإنجليزية عن ترجمتها العربية ← تصحيح الحروف العربية المعكوسة/المبعثرة
   (شائع جدًا عند قراءة PDF) ← تنظيف الرموز (ترقيم، نوع الكلمة، النطق...).
2) الذكاء الاصطناعي لا يُستدعى إلا في حالتين:
   أ) توليد جملة مثال + ترجمتها لكل كلمة (دفعات كبيرة: طلب واحد لكل ~20 كلمة)،
   ب) إصلاح ترجمة عربية لم ينجح المحلّل في استخراجها بثقة (فقط للصفوف المشكوك فيها، وضمن نفس الطلب).
   وإن تعذّر التحليل المحلي كليًا (لا صفوف) يُستخدم الذكاء الاصطناعي على النص الخام كحلّ أخير.
"""
import os
import re
import tempfile
import unicodedata

from . import ai

_AR = "\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF"
_AR_RE = re.compile(f"[{_AR}]")
_LAT_RE = re.compile(r"[A-Za-z]")
_INVIS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u00ad]")
_TASH_RE = re.compile(r"[\u064b-\u065f\u0670\u0640]")
_SEP_CHARS = set("-–—:=|•▪●*·/\\~_→←؛;،,")
_POS_PAREN_RE = re.compile(r"[\(\[]\s*(?:n|v|adj|adv|prep|pron|conj|interj|det|art|noun|verb|adjective|adverb|preposition|phr\.?\s*v)\.?\s*[\)\]]", re.I)
_POS_BARE_RE = re.compile(r"\b(?:n|v|adj|adv|prep|pron|conj|interj)\.(?=\s|$)", re.I)
_BRACKET_RE = re.compile(r"\[[^\]]{0,30}\]")
_SLASH_IPA_RE = re.compile(r"/[^/\n]{1,40}/")
_STOP = {"vocabulary list", "word list", "vocabulary words", "new vocabulary", "key words", "vocabulary", "vocab", "words", "word", "word list", "new words", "unit", "lesson", "meaning", "translation", "arabic",
         "english", "example", "examples", "sentence", "no", "number", "page", "المفردات", "الكلمات", "الترجمة", "المعنى"}
MAX_ROWS = 300


# ===================== تطبيع النص وتقسيمه إلى مقاطع (إنجليزي / عربي) =====================
def _norm(line):
    s = unicodedata.normalize("NFKC", line or "")  # يحوّل أشكال العرض العربية (ﻛﺘﺎﺏ) إلى الحروف الأصلية
    s = _INVIS_RE.sub("", s)
    s = re.sub(r"^\s*[\(\[]?\d{1,3}\s*[\.\)\]\-:]\s*", "", s)  # ترقيم أول السطر
    s = _POS_PAREN_RE.sub(" ", s)
    s = _POS_BARE_RE.sub(" ", s)
    s = _BRACKET_RE.sub(" ", s)
    s = _SLASH_IPA_RE.sub(lambda m: " " if re.search(r"[^\x00-\x7f]", m.group(0)) else m.group(0), s)
    s = re.sub(f"([A-Za-z\\.\\)\\?!])(?=[{_AR}])", r"\1 ", s)
    s = re.sub(f"([{_AR}])(?=[A-Za-z])", r"\1 ", s)
    return s


def _runs(line):
    """يقسّم السطر إلى مقاطع متتالية [('en', 'abandon'), ('ar', 'نع ىلختي')] حسب الكتابة."""
    out = []
    for tok in _norm(line).split():
        if all(ch in _SEP_CHARS for ch in tok):
            out.append(None)
            continue
        kind = "ar" if _AR_RE.search(tok) else ("en" if _LAT_RE.search(tok) else None)
        if kind is None:
            continue
        if out and out[-1] and out[-1][0] == kind:
            out[-1][1].append(tok)
        else:
            out.append([kind, [tok]])
    return [(k, " ".join(t)) for k, t in (r for r in out if r)]


# ===================== العربية: اكتشاف الاتجاه المعكوس وتصحيحه =====================
def _evidence(text):
    """أدلة على أن النص بالاتجاه الصحيح (f) أو معكوس (r): ة/ال في النهاية/البداية، وحروف لا تبدأ بها الكلمات."""
    f = r = 0
    for tok in _TASH_RE.sub("", text).split():
        core = re.sub(r"[^\u0621-\u064a]", "", tok)
        if len(core) < 2:
            continue
        if core.startswith("ال") and len(core) > 3:
            f += 2
        if core.endswith("ة"):
            f += 2
        if core.endswith("ى"):
            f += 1
        if core[0] in "ةى":
            r += 3
        if core[0] in "ئؤ":
            r += 2
        if core.endswith("لا") and len(core) > 3:
            r += 2
    return f, r


def _decide_reversed(ar_texts):
    """(هل النص معكوس؟, هل القرار مؤكد؟) بالتصويت على كل المقاطع العربية في الملف."""
    F = R = 0
    for t in ar_texts:
        f, r = _evidence(t)
        F += f
        R += r
    if F + R == 0:
        return False, False
    if R >= 2 * F + 2:
        return True, True
    if F >= 2 * R + 2:
        return False, True
    return R > F, False


def _clean_ar(text):
    t = _TASH_RE.sub("", text)
    t = re.sub(r"[\(\)\[\]\"“”]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:،؛,.;|")
    return t


def _fix_run(text, reversed_doc):
    t = text[::-1] if reversed_doc else text
    f, r = _evidence(t)
    if r > f + 2:  # هذا المقطع وحده يخالف اتجاه الملف بوضوح
        alt = t[::-1]
        f2, r2 = _evidence(alt)
        if (f2 - r2) > (f - r) + 2:
            t = alt
    return _clean_ar(t)


def _valid_ar(text):
    if len(re.findall(r"[\u0621-\u064a]", text)) < 2 or _LAT_RE.search(text) or len(text) > 80:
        return False
    for tok in text.split():
        core = re.sub(r"[^\u0621-\u064a]", "", tok)
        if core and core[0] in "ةىئؤ":
            return False
    return True


# ===================== الإنجليزية: كلمة أم جملة؟ =====================
def _is_sentence(t):
    n = len(t.split())
    return n >= 5 or (t.rstrip().endswith((".", "?", "!")) and n >= 3) or (n >= 4 and "," in t)


def _as_word(text):
    t = text.strip(" ,;:-–—_*•|()[]\"“”")
    t = re.sub(r"^\d+[\.\)]\s*", "", t).strip()
    if not t or len(t) > 40 or len(t) < 2 or t.endswith((".", "?", "!")):
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z'’\- ]*", t) or len(t.split()) > 4 or t.lower() in _STOP:
        return ""
    if len(t.split()) >= 2 and t.lower().split()[-1] in {"list", "words", "vocabulary", "vocab"}:  # عناوين مثل «Unit 2 Vocabulary»
        return ""
    return t


# ===================== تحليل مجموعة أسطر إلى صفوف =====================
def _parse_group(lines, force_forward=False):
    """lines: قائمة أسطر، كل سطر قائمة مقاطع (kind, text). يعيد (rows, direction_certain)."""
    ar_texts = [t for ln in lines for k, t in ln if k == "ar"]
    if force_forward:
        rev, certain = False, True
    else:
        rev, certain = _decide_reversed(ar_texts)
    # ترتيب العمودين: هل تأتي الكلمة قبل الترجمة في الأسطر (انجليزي أولًا) أم العكس؟
    en_first = ar_first = 0
    for ln in lines:
        ens = [i for i, (k, t) in enumerate(ln) if k == "en" and _as_word(t)]
        ars = [i for i, (k, _) in enumerate(ln) if k == "ar"]
        if len(ens) == 1 and len(ars) == 1:
            if ens[0] < ars[0]:
                en_first += 1
            else:
                ar_first += 1
    arf = ar_first > en_first

    rows, cur, pend = [], None, []
    for ln in lines:
        for kind, text in ln:
            if kind == "en":
                w = _as_word(text)
                if w:
                    cur = {"word": w, "mparts": pend if arf else [], "example": "", "tparts": []}
                    pend = []
                    rows.append(cur)
                elif cur is not None and not cur["example"] and _is_sentence(text):
                    cur["example"] = text.strip()[:300]
            else:
                if arf:
                    pend = pend + [text]
                elif cur is not None:
                    if not cur["mparts"]:
                        cur["mparts"] = [text]
                    elif cur["example"] and not cur["tparts"]:
                        cur["tparts"] = [text]

    out, seen = [], set()
    for r in rows:
        key = r["word"].lower()
        if key in seen:
            continue
        seen.add(key)
        meaning = " ".join(x for x in (_fix_run(p, rev) for p in r["mparts"]) if x)
        ok = bool(meaning) and _valid_ar(meaning) and certain
        tr = " ".join(x for x in (_fix_run(p, rev) for p in r["tparts"]) if x)
        if tr and not _valid_ar(tr):
            tr = ""
        out.append({"word": r["word"], "meaning": meaning[:200] if meaning else "", "example": r["example"], "example_translation": tr[:300],
                    "bad": not ok, "raw": " | ".join(r["mparts"])[:120] if not ok else ""})
        if len(out) >= MAX_ROWS:
            break
    return out


# ===================== قراءة الملفات =====================
def _table_good(tbl_lines):
    both = sum(1 for ln in tbl_lines if {k for k, _ in ln} >= {"en", "ar"})
    return both >= 2 and both >= 0.5 * len(tbl_lines)


def _pdf_lines(path):
    """يعيد (أسطر مقاطع, نص خام). جداول PDF أولًا (أدق)، ثم أسطر النص، ثم OCR للملفات الممسوحة."""
    try:
        import pdfplumber
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("مكتبة pdfplumber غير مثبّتة على الخادم، وهي مطلوبة لقراءة ملفات PDF.") from e
    lines, raw = [], []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tbl = []
            try:
                for table in page.extract_tables() or []:
                    for row in table:
                        cells = [c.replace("\n", " ") for c in (row or []) if c and c.strip()]
                        runs = [rn for c in cells for rn in _runs(c)]
                        if runs:
                            tbl.append(runs)
                            raw.append(" ".join(cells))
            except Exception:  # noqa: BLE001
                tbl = []
            if _table_good(tbl):
                lines.extend(tbl)
                continue
            for ln in (page.extract_text() or "").splitlines():
                rn = _runs(ln)
                if rn:
                    lines.append(rn)
                    raw.append(ln)
    if not lines:  # لا طبقة نصية: ملف ممسوح ضوئيًا
        from examimport.services import extract_text_from_pdf

        pages, _ = extract_text_from_pdf(path)
        for ln in "\n".join(pages).splitlines():
            rn = _runs(ln)
            if rn:
                lines.append(rn)
                raw.append(ln)
    return lines, "\n".join(raw)


def _text_lines(text):
    lines = []
    for ln in (text or "").splitlines():
        rn = _runs(ln)
        if rn:
            lines.append(rn)
    return lines


def extract_vocabulary(files, pasted=""):
    """يعيد (rows, note). كل صف: word, meaning, example, example_translation, bad, raw."""
    groups, raw_all = [], []  # (lines, force_forward)
    for f in files or []:
        ext = os.path.splitext(f.name)[1].lower()
        fd, path = tempfile.mkstemp(suffix=ext)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            if ext == ".pdf":
                lines, raw = _pdf_lines(path)
            elif ext in ai.IMAGE_EXT:
                from examimport.services import extract_text_from_image

                raw = extract_text_from_image(path)
                lines = _text_lines(raw)
            else:
                raise RuntimeError(f"صيغة ملف غير مدعومة: {f.name}")
            groups.append((lines, False))
            raw_all.append(raw)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    if pasted.strip():
        groups.append((_text_lines(pasted), True))
        raw_all.append(pasted)

    rows, seen = [], set()
    for lines, ff in groups:
        for r in _parse_group(lines, force_forward=ff):
            if r["word"].lower() not in seen:
                seen.add(r["word"].lower())
                rows.append(r)
    raw_text = "\n".join(raw_all).strip()

    if not rows and raw_text:
        # حلّ أخير: فشل التحليل المحلي كليًا ← الذكاء الاصطناعي على النص الخام (إن وُجد مفتاح)، وإلا المحلّل البسيط
        ai_rows, used, note = ai.build_vocabulary(raw_text)
        rows = [{"word": r["word"], "meaning": r["meaning"], "example": r["example"], "example_translation": "",
                 "bad": not r["meaning"], "raw": ""} for r in ai_rows]
        if rows and used == "ai":
            return rows, "تعذّر تحليل تنسيق الملف محليًا، فاستُخدم الذكاء الاصطناعي لقراءة الكلمات."
        return rows, note
    note = ""
    if rows and any(r["bad"] for r in rows):
        n = sum(1 for r in rows if r["bad"])
        note = f"{n} كلمة ترجمتها غير واضحة في الملف (مبعثرة أو ناقصة) وعليها علامة تنبيه؛ "
        note += ("سيحاول الذكاء الاصطناعي إصلاحها." if ai.provider_label() != "heuristic" else "راجعها يدويًا، أو فعّل مفتاح الذكاء الاصطناعي لإصلاحها تلقائيًا.")
    return rows, note


# ===================== الذكاء الاصطناعي: جملة مثال + ترجمتها (+ إصلاح الترجمات المشكوك فيها) =====================
MAX_ENRICH_ITEMS = 25


def _enrich_prompt(items):
    lines = []
    for it in items:
        if it.get("fix"):
            lines.append(f"{it['i']}|{it['word']}|FIX hint={it.get('raw', '')[:100]}")
        else:
            extra = f"|ex={it['example']}" if it.get("example") else ""
            lines.append(f"{it['i']}|{it['word']}|{it.get('meaning', '')}{extra}")
    return (
        "You help an Arabic-speaking English teacher build a vocabulary list. For each item below return:\n"
        '- "e": one short, simple, natural English example sentence (6-12 words) using the word exactly as written '
        "(omit when the item already has ex=...).\n"
        '- "t": the natural Modern Standard Arabic translation of that example sentence (of ex=... when given).\n'
        '- "m": ONLY for FIX items: the correct short Arabic meaning of the word (1-3 words). The hint is a garbled/reversed '
        "version of the meaning; repair it if you can, otherwise translate the word yourself.\n"
        'Reply with ONLY a JSON array, no markdown, like: [{"i":0,"e":"...","t":"..."},{"i":1,"e":"...","t":"...","m":"..."}]\n'
        "Items (index|word|Arabic meaning):\n" + "\n".join(lines)
    )


def enrich(items):
    """items: [{i, word, meaning, example, fix, raw}] (≤ MAX_ENRICH_ITEMS). يعيد ([{i, example, example_translation, meaning?}], error)."""
    clean = []
    for it in (items or [])[:MAX_ENRICH_ITEMS]:
        word = str(it.get("word", "")).strip()[:100]
        if not word:
            continue
        clean.append({"i": int(it.get("i", 0)), "word": word, "meaning": str(it.get("meaning", "") or "").strip()[:200],
                      "example": str(it.get("example", "") or "").strip()[:300], "fix": bool(it.get("fix")),
                      "raw": str(it.get("raw", "") or "")[:120]})
    if not clean:
        return [], "لا توجد كلمات."
    if ai.provider_label() == "heuristic":
        return [], "no_key"
    res = ai.call_llm_json(_enrich_prompt(clean), system="You return valid JSON only.", max_tokens=3500)
    if isinstance(res, dict):
        res = res.get("items") or res.get("words") or []
    if not isinstance(res, list):
        return [], "تعذّر الحصول على ردّ من خدمة الذكاء الاصطناعي (قد يكون الحد المسموح قد استُهلك مؤقتًا). حاول مرة أخرى بعد قليل."
    by_i = {c["i"]: c for c in clean}
    out = []
    for r in res:
        if not isinstance(r, dict):
            continue
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        src = by_i.get(i)
        if not src:
            continue
        e = str(r.get("e", "") or "").strip()[:300]
        t = str(r.get("t", "") or "").strip()[:300]
        m = _clean_ar(str(r.get("m", "") or ""))[:200]
        item = {"i": i}
        if src["example"]:
            item["example"] = src["example"]
        elif e and _LAT_RE.search(e):
            item["example"] = e
        if t and _AR_RE.search(t) and item.get("example"):
            item["example_translation"] = t
        if src["fix"] and m and _valid_ar(m):
            item["meaning"] = m
        if len(item) > 1:
            out.append(item)
    return out, ("" if out else "لم يُرجع الذكاء الاصطناعي نتائج صالحة لهذه الدفعة.")
