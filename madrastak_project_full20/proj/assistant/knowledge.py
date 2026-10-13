"""طبقة المنهج: تحويل محتوى الدروس إلى مقاطع قابلة للبحث، ثم استرجاع الأنسب لسؤال المستخدم.

هذه هي الطبقة الأهم في تقييد المساعد بالمنهج: النموذج لا يرى إلا المقاطع التي نستخرجها هنا
من محتوى الدروس التي يحق للمستخدم فتحها فعليًا (مع احترام الاشتراك والدروس المجانية).

لا نستخدم أي مكتبة خارجية: تطبيع عربي/إنجليزي + BM25 مكتوبان هنا.
"""
import math
import re
import time
from collections import Counter

from django.db.models import Q
from django.urls import reverse

# ------------------------------------------------------------------ تطبيع النص
_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0640\u0670]")
_HTML = re.compile(r"<[^>]+>")
_TOKEN = re.compile(r"[a-z0-9]+|[\u0621-\u064A]+")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})

STOPWORDS = set("""
ما ماذا من هو هي هل لماذا كيف اين متي ايه ازاي ليه فين مين
اشرح شرح اشرحلي وضح وضحلي فسر فسرلي اذكر عرف عرفني اعطني اعطيني اريد عايز عاوز ممكن لو سمحت من فضلك
لي لنا لك انا انت نحن عن في علي الي من مع او ام ثم قد كان كانت يكون تكون هذا هذه ذلك تلك هناك هنا
الذي التي الذين كل بعض اي ايضا فقط جدا لكن بس يعني معني كلمه كلمات جمله درس الدرس الدروس
عندي عندك بخصوص حول بشان فيه فيها منه منها به بها له لها
what is are was were the a an of in on to for and or how why when where which who explain tell me please
about does do did can could would should give show mean meaning word words lesson this that these those
with from by as it its be been i you we my your us
""".split())


def norm(text):
    text = (text or "").translate(_DIGITS).lower()
    text = _DIACRITICS.sub("", text)
    return text.translate(_AR_MAP)


_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و")
_AR_SUFFIXES = ("ها", "هم", "ات", "ون", "ين", "ان", "ه", "ي")


def _stem(tok):
    if re.match(r"[a-z0-9]", tok):
        if tok.isdigit():
            return tok
        for suf, minlen in (("ies", 5), ("ing", 6), ("ed", 5), ("es", 5), ("s", 4)):
            if tok.endswith(suf) and len(tok) >= minlen and not tok.endswith("ss"):
                return tok[: -len(suf)] + ("y" if suf == "ies" else "")
        return tok
    for p in _AR_PREFIXES:
        if tok.startswith(p) and len(tok) - len(p) >= 3:
            tok = tok[len(p):]
            break
    for s in _AR_SUFFIXES:
        if tok.endswith(s) and len(tok) - len(s) >= 3:
            tok = tok[: -len(s)]
            break
    return tok


def tokens(text, keep_stop=False):
    out = []
    for t in _TOKEN.findall(norm(text)):
        if not keep_stop and (t in STOPWORDS or (len(t) < 2 and not t.isdigit())):
            continue
        out.append(_stem(t))
    return out


def clean_text(text):
    text = _HTML.sub(" ", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ------------------------------------------------------------------ المقاطع
class Chunk:
    __slots__ = ("lesson_id", "title", "url", "text", "kind", "pos", "tf", "length", "label")

    def __init__(self, lesson_id, title, url, text, kind, pos, label=""):
        self.lesson_id, self.title, self.url, self.text, self.kind, self.pos = lesson_id, title, url, text, kind, pos
        self.label = label
        toks = tokens(title) * 2 + tokens(text)  # ترجيح العنوان
        self.tf = Counter(toks)
        self.length = max(len(toks), 1)


def _split(text, limit=1000):
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if len(cur) + len(para) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        while len(para) > limit * 1.5:  # فقرة طويلة جدًا
            parts.append(para[:limit])
            para = para[limit:]
        cur = (cur + "\n" + para).strip()
    if cur:
        parts.append(cur)
    return parts or [""]


KIND_LABELS = {"text": "شرح", "definition": "تعريف", "rule": "قاعدة", "method": "طريقة حل", "example": "مثال",
               "note": "ملاحظة", "mistakes": "أخطاء شائعة", "review": "مراجعة"}


class Corpus:
    def __init__(self):
        self.chunks = []
        self.lessons = {}     # lesson_id -> {"title","course","unit","url","status"}
        self.df = Counter()
        self.avgdl = 1.0
        self.has_latin = False
        self.has_arabic = False

    def finalize(self):
        for c in self.chunks:
            for t in c.tf:
                self.df[t] += 1
        self.avgdl = (sum(c.length for c in self.chunks) / len(self.chunks)) if self.chunks else 1.0
        vocab = self.df.keys()
        self.has_latin = any(re.match(r"[a-z]", t) for t in vocab)
        self.has_arabic = any(re.match(r"[\u0621-\u064A]", t) for t in vocab)
        return self

    def needs_expansion(self, qtokens):
        """هل لغة السؤال تختلف عن لغة (جزء من) المنهج؟ عندها فقط يفيد توسيع البحث بالنموذج."""
        q_ar = any(re.match(r"[\u0621-\u064A]", t) for t in qtokens)
        q_la = any(re.match(r"[a-z]", t) for t in qtokens)
        return (q_ar and self.has_latin) or (q_la and self.has_arabic)

    # --- BM25
    def idf(self, term):
        n = len(self.chunks) or 1
        return math.log(1 + (n - self.df.get(term, 0) + 0.5) / (self.df.get(term, 0) + 0.5))

    def search(self, qtokens, k=6):
        qset = list(dict.fromkeys(qtokens))
        if not qset or not self.chunks:
            return []
        k1, b = 1.5, 0.75
        scored = []
        for c in self.chunks:
            s, matched = 0.0, 0
            for t in qset:
                f = c.tf.get(t, 0)
                if not f:
                    continue
                matched += 1
                s += self.idf(t) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * c.length / self.avgdl))
            if s > 0:
                scored.append((s, matched, c))
        scored.sort(key=lambda x: (-x[0], x[2].pos))
        return scored[:k]

    def coverage(self, qtokens):
        """نسبة الكلمات المهمة في السؤال (موزونة بندرتها) التي وُجدت فعلًا في المنهج."""
        qset = list(dict.fromkeys(qtokens))
        if not qset:
            return 0.0
        # الكلمة غير الموجودة في المنهج مطلقًا (صياغة الطالب مثل «أكونه») لا تُعاقَب بوزن مبالغ فيه
        def w(t):
            return self.idf(t) if self.df.get(t, 0) > 0 else min(self.idf(t), 1.2)
        total = sum(w(t) for t in qset)
        found = sum(w(t) for t in qset if self.df.get(t, 0) > 0)
        return found / total if total else 0.0

    def lesson_chunks(self, lesson_id):
        return [c for c in self.chunks if c.lesson_id == lesson_id]

    def map_lines(self, limit=90):
        by = {}
        for lid, m in self.lessons.items():
            by.setdefault((m["course"], m["unit"]), []).append((lid, m))
        lines = []
        for (course, unit), items in by.items():
            lines.append(f"- {course} › {unit}")
            for lid, m in items:
                lines.append(f"    [#L{lid}] {m['title']}")
        return "\n".join(lines[:limit * 2])


def _add_lesson(corpus, lesson, url, status_note=""):
    course, unit = lesson.unit.course, lesson.unit
    corpus.lessons[lesson.id] = {"title": lesson.title, "course": course.title, "unit": unit.title, "url": url,
                                 "status": status_note}
    ctx = f"{course.title} › {unit.title} › {lesson.title}"
    cite = f"{lesson.title}{status_note}"
    pos = len(corpus.chunks)
    head = clean_text("\n".join(x for x in (course.description, unit.description, lesson.summary) if x))
    corpus.chunks.append(Chunk(lesson.id, cite, url, f"{ctx}\n{head}" if head else ctx, "summary", pos, "ملخص الدرس"))
    for sec in lesson.sections.all():
        body = clean_text(sec.content)
        if not body and not sec.title:
            continue
        label = KIND_LABELS.get(sec.kind, "شرح")
        for part in _split(body):
            corpus.chunks.append(Chunk(lesson.id, f"{cite} — {sec.title}", url,
                                       f"[{label}] {sec.title}\n{part}", "section", len(corpus.chunks), f"{sec.title}"))
    vocab = list(lesson.vocabulary.all())
    for i in range(0, len(vocab), 12):
        rows = []
        for w in vocab[i:i + 12]:
            row = f"{w.word}: {w.meaning}"
            if w.example:
                row += f" | مثال: {w.example}" + (f" ({w.example_translation})" if w.example_translation else "")
            rows.append(row)
        corpus.chunks.append(Chunk(lesson.id, f"{cite} — مفردات الدرس", url, "المفردات:\n" + "\n".join(rows),
                                   "vocab", len(corpus.chunks), "مفردات الدرس"))


# ------------------------------------------------------------------ نصوص الملفات المرفقة
def ensure_file_texts(lesson_ids, budget_seconds=6.0):
    """يستخرج (مرة واحدة ثم يخزّن) نص ملفات PDF/النص المرفقة بالدروس. يتوقف عند نفاد الوقت."""
    from learning.models import LessonFile
    from .models import LessonFileText
    started = time.time()
    files = LessonFile.objects.filter(lesson_id__in=lesson_ids).select_related("extracted")
    for lf in files:
        if time.time() - started > budget_seconds:
            break
        name = (lf.file.name or "").lower()
        if not name.endswith((".pdf", ".txt", ".md")):
            continue
        try:
            size = lf.file.size
        except Exception:
            continue
        ex = getattr(lf, "extracted", None)
        if ex is not None and ex.file_size == size:
            continue
        text = ""
        try:
            if name.endswith(".pdf"):
                import pdfplumber
                with pdfplumber.open(lf.file.path) as pdf:
                    pages = []
                    for page in pdf.pages[:60]:
                        pages.append(page.extract_text() or "")
                        if sum(len(p) for p in pages) > 80000:
                            break
                    text = "\n\n".join(pages)
            else:
                with open(lf.file.path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read(80000)
        except Exception:
            text = ""
        LessonFileText.objects.update_or_create(lesson_file=lf, defaults={"text": clean_text(text)[:80000], "file_size": size})


def _add_file_chunks(corpus, lesson_ids, url_of):
    from .models import LessonFileText
    for ft in (LessonFileText.objects.filter(lesson_file__lesson_id__in=lesson_ids)
               .exclude(text="").select_related("lesson_file")):
        lid = ft.lesson_file.lesson_id
        if lid not in corpus.lessons:
            continue
        cite = f"{corpus.lessons[lid]['title']} — ملف: {ft.lesson_file.title}"
        for part in _split(ft.text, 1000):
            corpus.chunks.append(Chunk(lid, cite, url_of(lid), f"[ملف مرفق: {ft.lesson_file.title}]\n{part}", "file",
                                       len(corpus.chunks), ft.lesson_file.title))


# ------------------------------------------------------------------ بناء المنهج
LESSON_CAP = 400


def build_student_corpus(student):
    """منهج الطالب = دروس المدرّسين المسجَّل معهم، المنشورة، والتي يحق له فتحها فعليًا
    (دورة مجانية أو درس معاينة مجاني أو اشتراك نشط). لا يتسرّب إليه أي محتوى مقفل."""
    from billing.models import Enrollment
    from learning.models import Lesson, vis_q
    enrolls = list(Enrollment.objects.filter(student=student).select_related("teacher"))
    teacher_ids = [e.teacher_id for e in enrolls]
    active_ids = [e.teacher_id for e in enrolls if e.is_active]
    codes = {e.teacher_id: e.teacher.teacher_code for e in enrolls}
    corpus = Corpus()
    if not teacher_ids:
        return corpus.finalize()
    qs = (Lesson.objects.filter(vis_q(), vis_q("unit__"), vis_q("unit__course__"), unit__course__teacher_id__in=teacher_ids)
          .filter(Q(unit__course__course_type="free") | Q(is_free_preview=True) | Q(unit__course__teacher_id__in=active_ids))
          .select_related("unit__course").prefetch_related("sections", "vocabulary")
          .order_by("unit__course__order", "unit__course_id", "unit__order", "order", "id")[:LESSON_CAP])
    lessons = list(qs)
    tmap = {}
    for les in lessons:
        tid = les.unit.course.teacher_id
        tmap[les.id] = codes.get(tid, "")
        _add_lesson(corpus, les, reverse("student:lesson", kwargs={"code": codes.get(tid, ""), "pk": les.id}))
    ids = [l.id for l in lessons]
    if ids:
        ensure_file_texts(ids)
        _add_file_chunks(corpus, ids, lambda lid: corpus.lessons[lid]["url"])
    return corpus.finalize()


def build_teacher_corpus(teacher):
    """منهج المدرّس = كل محتوى دوراته هو (حتى المسودات، مع وسمها)."""
    from learning.models import Lesson
    corpus = Corpus()
    lessons = list(Lesson.objects.filter(unit__course__teacher=teacher).select_related("unit__course")
                   .prefetch_related("sections", "vocabulary")
                   .order_by("unit__course__order", "unit__course_id", "unit__order", "order", "id")[:LESSON_CAP])
    for les in lessons:
        note = "" if (les.is_visible and les.unit.is_visible and les.unit.course.is_visible) else " (غير منشور)"
        _add_lesson(corpus, les, reverse("teacher:lesson_detail", kwargs={"pk": les.id}), note)
    ids = [l.id for l in lessons]
    if ids:
        ensure_file_texts(ids)
        _add_file_chunks(corpus, ids, lambda lid: corpus.lessons[lid]["url"])
    return corpus.finalize()


# ------------------------------------------------------------------ اختيار المقاطع للنموذج
MAX_CONTEXT_CHARS = 6500
MIN_COVERAGE = 0.34


def select_context(corpus, qtokens, k=6, strict_matches=1):
    """يعيد (قائمة مقاطع مرتّبة للنموذج, هل السؤال مرتبط فعلًا بالمنهج؟).
    strict_matches: أقل عدد كلمات متطابقة في أفضل مقطع (نرفعه عند البحث بكلمات موسَّعة من النموذج)."""
    hits = corpus.search(qtokens, k=k)
    if not hits:
        return [], False
    # مرتبط إذا تطابقت كلمتان مختلفتان على الأقل في أفضل مقطع، أو غطّى المنهج جزءًا كافيًا من السؤال
    relevant = hits[0][1] >= strict_matches and (hits[0][1] >= 2 or corpus.coverage(qtokens) >= MIN_COVERAGE)
    if not relevant:
        return [], False
    chosen, seen = [], set()
    for _, _, c in hits:
        if id(c) not in seen:
            chosen.append(c)
            seen.add(id(c))
    # توسيع: بقية مقاطع الدرس الأعلى تطابقًا ليتمكن المساعد من الشرح الوافي
    top_lesson = hits[0][2].lesson_id
    for c in corpus.lesson_chunks(top_lesson):
        if id(c) not in seen:
            chosen.append(c)
            seen.add(id(c))
    out, used = [], 0
    for c in chosen:
        t = c.text if len(c.text) <= 1400 else c.text[:1400] + "…"
        if used + len(t) > MAX_CONTEXT_CHARS and out:
            break
        out.append(c)
        used += len(t)
    return out, True
