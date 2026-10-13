"""أدوات الذكاء الاصطناعي للمدرّس (قدرات جديدة مضافة دون تعديل أي منطق قائم):

1) استيراد مفردات الدرس من ملف PDF/صورة أو نص: استخراج الكلمات، ترتيبها، وتوليد معنى وجملة مثال لكل كلمة.
2) هيكلة شرح الدرس: تحويل نص كبير غير مرتّب (أو ملف/صورة) إلى أقسام واضحة (مقدمة، قاعدة، شرح، أمثلة، ملاحظات...).

نفس أسلوب الربط الذكي القائم: OCR (نفس دوال examimport) + Groq أولًا، ثم Claude، ثم تراجع تلقائي إلى
محلّل قواعد نصية محلي حتى تعمل الميزة دون اتصال (مع تنبيه المدرّس بأن الأمثلة/الترتيب لم يُولَّدا بالذكاء الاصطناعي).
"""
import json
import os
import re
import tempfile
import time

from django.conf import settings

MAX_INPUT_CHARS = 14000
ALLOWED_KINDS = ("text", "definition", "rule", "method", "example", "note", "mistakes", "review")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


# ===================== الاتصال بمزوّد الذكاء الاصطناعي =====================
def provider_label():
    from accounts.utils import get_ai_keys

    groq_key, anthropic_key = get_ai_keys()
    if groq_key:
        return "groq"
    if anthropic_key:
        return "anthropic"
    return "heuristic"


def _extract_json(text):
    """يستخرج أول كائن/مصفوفة JSON من ردّ النموذج (حتى لو أحاطه بنص أو ```)."""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    for open_c, close_c in (("[", "]"), ("{", "}")):
        s, e = text.find(open_c), text.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except Exception:
                continue
    return None


def _groq_call(prompt, system, max_tokens):
    try:
        from groq import Groq
    except Exception:
        return None
    from accounts.utils import get_ai_keys

    groq_key, _ = get_ai_keys()
    client = Groq(api_key=groq_key)
    model = getattr(settings, "AI_MAPPING_MODEL", "") or "openai/gpt-oss-120b"
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(model=model, messages=msgs, temperature=0.2, max_tokens=max_tokens)
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 429 and attempt == 0:
                wait = 3.0
                try:
                    wait = float(e.response.headers.get("retry-after", wait))
                except Exception:
                    pass
                time.sleep(min(wait, 10))
                continue
            return None
    return None


def _anthropic_call(prompt, system, max_tokens):
    try:
        import anthropic
    except Exception:
        return None
    model = os.environ.get("AI_TEXT_MODEL") or getattr(settings, "AI_MAPPING_MODEL", "")
    if not model or not model.startswith("claude"):
        model = "claude-sonnet-4-6"
    try:
        from accounts.utils import get_ai_keys

        _, anthropic_key = get_ai_keys()
        client = anthropic.Anthropic(api_key=anthropic_key)
        resp = client.messages.create(model=model, max_tokens=max_tokens, system=system or "",
                                      messages=[{"role": "user", "content": prompt}])
        return "".join(getattr(b, "text", "") for b in resp.content)
    except Exception:
        return None


def call_llm_json(prompt, system="", max_tokens=3500):
    """يعيد كائن JSON من النموذج أو None عند أي فشل (ليتراجع النظام للمحلّل المحلي)."""
    label = provider_label()
    raw = None
    if label == "groq":
        raw = _groq_call(prompt, system, max_tokens)
    elif label == "anthropic":
        raw = _anthropic_call(prompt, system, max_tokens)
    return _extract_json(raw)


# ===================== استخراج النص من الملفات المرفوعة =====================
def extract_text_from_uploads(files):
    """يستخرج نصًا من ملفات PDF/صور مرفوعة (بدون حفظها في قاعدة البيانات) عبر نفس محرك examimport."""
    from examimport.services import extract_text_from_image, extract_text_from_pdf

    parts = []
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        fd, path = tempfile.mkstemp(suffix=ext)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            if ext == ".pdf":
                pages, _ = extract_text_from_pdf(path)
                parts.append("\n\n".join(pages))
            elif ext in IMAGE_EXT:
                parts.append(extract_text_from_image(path))
            else:
                raise RuntimeError(f"صيغة ملف غير مدعومة: {f.name}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return "\n\n".join(p for p in parts if p).strip()


# ===================== 1) المفردات =====================
_AR = re.compile(r"[\u0600-\u06FF]")
_EN_WORD = re.compile(r"^[A-Za-z][A-Za-z'’\- ]{0,38}$")
_VOCAB_STOP = {"vocabulary", "words", "word list", "new words", "vocab", "unit", "lesson", "meaning", "word", "المفردات", "الكلمات"}
_SEP = re.compile(r"\s*(?:[-–—:=|\t]|\s{2,})\s*")


def heuristic_vocab(raw):
    """محلّل قواعد: «word - معنى» أو «word : معنى» أو كلمة إنجليزية وحدها. لا يولّد أمثلة."""
    out, seen = [], set()
    for line in (raw or "").splitlines():
        line = re.sub(r"^\s*(?:\d+[\.\)\-]|[•▪●*\-])\s*", "", line).strip()
        if not line or len(line) > 160:
            continue
        word, meaning = "", ""
        m = _AR.search(line)
        if m and m.start() > 0:
            left, right = line[:m.start()].strip(" -–—:=|\t"), line[m.start():].strip()
            if _EN_WORD.match(left):
                word, meaning = left, right
        elif not m:
            parts = _SEP.split(line, maxsplit=1)
            cand = parts[0].strip()
            if _EN_WORD.match(cand) and len(cand.split()) <= 3:
                word = cand
                meaning = parts[1].strip() if len(parts) > 1 and _AR.search(parts[1]) else ""
        if not word:
            continue
        key = word.lower()
        if key in seen or key in _VOCAB_STOP:
            continue
        seen.add(key)
        out.append({"word": word, "meaning": meaning[:200], "example": ""})
    return out[:300]


def _vocab_prompt(chunk):
    return (
        "فيما يلي نص مستخرج (OCR أو نسخ) من مستند مفردات لدرس لغة إنجليزية. المطلوب:\n"
        "1) استخرج الكلمات/العبارات الإنجليزية فقط (تجاهل أرقام الصفحات والعناوين والجمل الشارحة الطويلة).\n"
        "2) لكل كلمة اكتب معناها بالعربية (إن كان المعنى موجودًا في النص فاستخدمه كما هو، وإلا فاكتب معنى دقيقًا ومختصرًا).\n"
        "3) لكل كلمة اكتب جملة مثال إنجليزية قصيرة وبسيطة وصحيحة تستخدم الكلمة.\n"
        "4) رتّب الكلمات كما وردت ولا تكرّر كلمة.\n"
        "أجب بمصفوفة JSON فقط بدون أي نص قبلها أو بعدها، بهذا الشكل بالضبط:\n"
        '[{"word": "introduce", "meaning": "يُقدّم", "example": "He introduced himself to the class."}]\n\n'
        f"النص:\n{chunk}"
    )


def _clean_vocab_rows(rows):
    out, seen = [], set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        word = str(r.get("word", "")).strip()[:100]
        if not word:
            continue
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"word": word, "meaning": str(r.get("meaning", "") or "").strip()[:200],
                    "example": str(r.get("example", "") or "").strip()[:300]})
    return out


def build_vocabulary(raw):
    """يعيد (rows, provider_used, note). provider_used = 'ai' أو 'heuristic'."""
    raw = (raw or "").strip()
    if not raw:
        return [], "none", "لم يُعثر على نص داخل الملف."
    raw = raw[:MAX_INPUT_CHARS]
    if provider_label() != "heuristic":
        rows, chunk, size = [], [], 0
        chunks = []
        for ln in raw.splitlines():
            chunk.append(ln)
            size += len(ln) + 1
            if size >= 3200:
                chunks.append("\n".join(chunk))
                chunk, size = [], 0
        if chunk:
            chunks.append("\n".join(chunk))
        failed = False
        for i, ch in enumerate(chunks):
            res = call_llm_json(_vocab_prompt(ch), system="أنت مساعد تعليمي دقيق يعيد JSON صالحًا فقط.", max_tokens=3500)
            if isinstance(res, dict):
                res = res.get("words") or res.get("vocabulary") or []
            if not res:
                failed = True
                rows.extend(heuristic_vocab(ch))
            else:
                rows.extend(res)
            if provider_label() == "groq" and i < len(chunks) - 1:
                time.sleep(2)
        cleaned = _clean_vocab_rows(rows)
        if cleaned:
            note = ("تعذّر توليد بعض الأمثلة بالذكاء الاصطناعي؛ راجع الصفوف الفارغة." if failed else "")
            return cleaned, ("heuristic" if failed and not any(r["example"] for r in cleaned) else "ai"), note
    rows = heuristic_vocab(raw)
    note = ("لا يوجد مفتاح ذكاء اصطناعي مفعّل حاليًا، فتم استخراج الكلمات بالمحلّل المحلي "
            "دون توليد جمل الأمثلة. يمكنك كتابة الأمثلة بنفسك، أو التواصل مع إدارة المنصة لتفعيل المفتاح.")
    return rows, "heuristic", note


# ===================== 2) هيكلة شرح الدرس =====================
_HEAD_RULES = [
    ("rule", re.compile(r"^(?:rule|formula|form|structure|القاعدة|قاعدة|التكوين|قاعدة التكوين|الصيغة)\b", re.I)),
    ("example", re.compile(r"^(?:examples?|أمثلة|امثلة|مثال|نماذج)\b", re.I)),
    ("definition", re.compile(r"^(?:definition|meaning|تعريف|التعريف|المقصود|مفهوم)\b", re.I)),
    ("note", re.compile(r"^(?:note|notes|important|ملاحظة|ملاحظات|تنبيه|مهم)\b", re.I)),
    ("mistakes", re.compile(r"^(?:common mistakes?|mistakes?|أخطاء شائعة|اخطاء شائعة|أخطاء|خطأ شائع)\b", re.I)),
    ("review", re.compile(r"^(?:summary|review|recap|ملخص|مراجعة|الخلاصة)\b", re.I)),
    ("method", re.compile(r"^(?:how to|steps?|method|طريقة|خطوات|كيفية)\b", re.I)),
    ("text", re.compile(r"^(?:explanation|introduction|intro|overview|شرح|الشرح|مقدمة|شرح مبسط)\b", re.I)),
]
_KIND_TITLES = {"text": "شرح", "definition": "تعريف", "rule": "القاعدة", "method": "طريقة الحل",
                "example": "أمثلة", "note": "ملاحظة مهمة", "mistakes": "أخطاء شائعة", "review": "مراجعة سريعة"}


def _kind_for_heading(h):
    h = h.strip().strip("#:*-– ").strip()
    for kind, rx in _HEAD_RULES:
        if rx.search(h):
            return kind
    return "text"


def _is_heading(line):
    s = line.strip()
    if not s or len(s) > 70:
        return False
    if s.startswith("#"):
        return True
    if s.endswith(":") and len(s.split()) <= 8:
        return True
    if re.match(r"^\d+[\.\)]\s+\S", s) and len(s.split()) <= 8 and not s.endswith((".", "؟", "?")):
        return True
    if any(rx.search(s) for _, rx in _HEAD_RULES) and len(s.split()) <= 6 and not s.endswith("."):
        return True
    return False


def heuristic_structure(raw):
    lines = [ln.rstrip() for ln in (raw or "").splitlines()]
    sections, cur = [], {"kind": "text", "title": "مقدمة", "buf": []}

    def flush():
        body = "\n".join(cur["buf"]).strip()
        if body:
            sections.append({"kind": cur["kind"], "title": cur["title"], "content": body})

    for ln in lines:
        if _is_heading(ln):
            flush()
            title = ln.strip().lstrip("#").strip().rstrip(":").strip()
            cur = {"kind": _kind_for_heading(title), "title": title[:160] or "قسم", "buf": []}
        else:
            cur["buf"].append(ln)
    flush()
    if len(sections) <= 1 and sections:
        # لا عناوين: قسّم الفقرات، والأسطر التي تبدأ بمثال/Example تُجمع في قسم أمثلة
        paras = [p.strip() for p in re.split(r"\n\s*\n", raw or "") if p.strip()]
        sections, ex_lines = [], []
        for i, p in enumerate(paras):
            if re.match(r"^(?:examples?|مثال|أمثلة)\b", p, re.I):
                ex_lines.append(p)
            else:
                sections.append({"kind": "text", "title": "مقدمة" if i == 0 else f"شرح ({i})", "content": p})
        if ex_lines:
            sections.append({"kind": "example", "title": "أمثلة", "content": "\n".join(ex_lines)})
    return sections[:40]


def _structure_prompt(raw):
    return (
        "فيما يلي شرح درس كتبه مدرّس على شكل نص كبير غير مرتّب (قد يكون مستخرجًا بالـ OCR وفيه أخطاء تنسيق). "
        "المطلوب: نظّم الشرح إلى أقسام واضحة دون أن تحذف أو تخترع معلومات، مع الحفاظ على لغة المدرّس "
        "(لا تترجم النص الإنجليزي ولا تغيّر الأمثلة) وتصحيح أخطاء التنسيق الواضحة فقط.\n\n"
        "أنواع الأقسام المسموحة (kind): text (شرح/مقدمة)، definition (تعريف)، rule (قاعدة أو معادلة)، "
        "method (طريقة الحل/خطوات)، example (أمثلة)، note (ملاحظة مهمة)، mistakes (أخطاء شائعة)، review (مراجعة سريعة).\n"
        "قواعد الصياغة: اجعل content نصًا عاديًا؛ للنقاط استخدم سطرًا لكل نقطة يبدأ بـ «• »؛ "
        "لكل مثال سطر واحد بالشكل «English sentence — الترجمة أو الشرح»؛ اجعل عنوان كل قسم قصيرًا وواضحًا.\n"
        "أجب بكائن JSON فقط بدون أي نص قبله أو بعده بهذا الشكل بالضبط:\n"
        '{"title": "عنوان الدرس", "sections": [{"kind": "text", "title": "مقدمة", "content": "..."}, '
        '{"kind": "rule", "title": "قاعدة التكوين", "content": "..."}, {"kind": "example", "title": "أمثلة", "content": "..."}]}\n\n'
        f"نص الشرح:\n{raw}"
    )


def _clean_sections(items):
    out = []
    for s in items or []:
        if not isinstance(s, dict):
            continue
        content = str(s.get("content", "") or "").strip()
        if not content:
            continue
        kind = str(s.get("kind", "text")).strip().lower()
        if kind not in ALLOWED_KINDS:
            kind = "text"
        title = str(s.get("title", "") or "").strip()[:160] or _KIND_TITLES.get(kind, "قسم")
        out.append({"kind": kind, "title": title, "content": content})
    return out[:40]


def build_lesson_structure(raw):
    """يعيد (title, sections, provider_used, note)."""
    raw = (raw or "").strip()
    if not raw:
        return "", [], "none", "لا يوجد نص لتحليله."
    raw = raw[:MAX_INPUT_CHARS]
    if provider_label() != "heuristic":
        res = call_llm_json(_structure_prompt(raw), system="أنت مساعد تعليمي دقيق يعيد JSON صالحًا فقط.", max_tokens=4000)
        if isinstance(res, list):
            res = {"title": "", "sections": res}
        if isinstance(res, dict):
            secs = _clean_sections(res.get("sections"))
            if secs:
                return str(res.get("title", "") or "").strip()[:160], secs, "ai", ""
    secs = _clean_sections(heuristic_structure(raw))
    note = ("لا يوجد مفتاح ذكاء اصطناعي مفعّل حاليًا (أو تعذّر الاتصال)، فتمت الهيكلة بمحلّل قواعد نصية بسيط. "
            "راجع الأقسام وعدّلها قبل الحفظ.")
    return "", secs, "heuristic", note
