"""نظام الربط الذكي بين الأسئلة وأقسام شرح الدرس (Question–Lecture Mapping).

يُنفَّذ مسبقًا (Precomputed) عند إعداد الاختبار وليس عند إجابة كل طالب، تمامًا كما
تنص المواصفة: يقرأ شرح الدرس كاملًا مع كل أسئلة الاختبار مرة واحدة، ويقترح لكل
سؤال القسم الأنسب من الشرح مع درجة ثقة، ثم يعرض الاقتراح على المدرّس ليعتمده أو
يغيّره — الاعتماد النهائي يُكتب في نفس الحقل الموجود أصلًا Question.section بحيث
تستمر كل الأماكن التي تعتمد عليه (صفحة نتيجة الطالب، اقتراح سؤال مشابه) بالعمل
دون أي تعديل إضافي.

يعمل النظام بثلاث طرق مرتّبة حسب الأولوية:
- إن توفّر مفتاح GROQ_API_KEY في الإعدادات: يُستخدم Groq (نموذج مجاني) لفهم
  المعنى (Semantic Matching) بدل مطابقة الكلمات الحرفية.
- وإلا، إن توفّر مفتاح ANTHROPIC_API_KEY: يُستخدم Claude بنفس الطريقة.
- وإلا: يعمل النظام تلقائيًا بخوارزمية تقريبية محلية (تشابه الكلمات المفتاحية)
  حتى يستمر بالعمل دون اتصال بالإنترنت، مع الإفصاح عن ذلك في سبب الاقتراح
  المعروض للمدرّس.
"""
import json
import re
import time

from django.conf import settings
from django.utils import timezone

_STOPWORDS = set(
    """the a an of to in on for and or is are was were be been being this that these those
    في من إلى على عن مع هو هي هم هن كان كانت يكون تكون التي الذي و أو لا نعم كل بعض غير ما لم لن قد ثم""".split()
)


def _tokenize(text):
    words = re.findall(r"[A-Za-z\u0600-\u06FF]{2,}", (text or "").lower())
    return [w for w in words if w not in _STOPWORDS]


def _overlap_score(a_tokens, b_tokens):
    if not a_tokens or not b_tokens:
        return 0.0
    a_set, b_set = set(a_tokens), set(b_tokens)
    inter = len(a_set & b_set)
    return inter / max(1, min(len(a_set), len(b_set)))


def heuristic_best_section(question_text, choice_texts, sections):
    """مطابقة تقريبية بالاعتماد على تشابه الكلمات المفتاحية، تُستخدم عند عدم توفر
    مزوّد ذكاء اصطناعي فعلي، أو كتراجع آمن إذا فشل الاستدعاء."""
    q_tokens = _tokenize(question_text + " " + " ".join(choice_texts))
    scored = []
    for s in sections:
        s_tokens = _tokenize((s.title or "") + " " + (s.content or ""))
        scored.append((_overlap_score(q_tokens, s_tokens), s))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] <= 0:
        return None, 0, []
    best_score, best_section = scored[0]
    confidence = min(95, round(30 + best_score * 140))
    secondary = [s for score, s in scored[1:3] if score > 0 and score >= best_score * 0.6]
    return best_section, confidence, secondary


def _anthropic_map_question(question, sections, api_key):
    """يستخدم Claude لتحديد القسم الأنسب دلاليًا. يعيد None عند أي خطأ ليتراجع
    النظام تلقائيًا إلى المطابقة التقريبية بدل التوقف بالكامل."""
    try:
        import anthropic
    except Exception:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _mapping_prompt(question, sections)
        resp = client.messages.create(
            model=getattr(settings, "AI_MAPPING_MODEL", "claude-sonnet-4-6"),
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception:
        return None


def _mapping_prompt(question, sections):
    sections_payload = "\n\n".join(f"[القسم {s.id}] {s.title}\n{s.content}" for s in sections)
    choices_text = "\n".join(f"- {c.text}" for c in question.choices.all())
    return (
        "لديك شرح درس مقسّم إلى أقسام، وسؤال اختبار واحد. حدّد رقم القسم الأنسب "
        "لشرح المفهوم الذي يقيسه هذا السؤال، اعتمادًا على المعنى وليس فقط تطابق الكلمات.\n\n"
        f"الأقسام:\n{sections_payload}\n\n"
        f"السؤال: {question.text}\nالاختيارات:\n{choices_text}\n\n"
        "أجب بصيغة JSON فقط بدون أي نص إضافي قبله أو بعده، بهذا الشكل بالضبط:\n"
        '{"section_id": <رقم أو null>, "confidence": <رقم من 0 إلى 100>, '
        '"secondary_ids": [<أرقام إضافية إن وجدت>], "reasoning": "<جملة قصيرة جدًا>"}'
    )


def _groq_map_question(question, sections, api_key, model):
    """يستخدم Groq (نموذج مجاني متوافق مع واجهة OpenAI) لتحديد القسم الأنسب
    دلاليًا. يعيد None عند أي خطأ ليتراجع النظام تلقائيًا إلى المطابقة التقريبية
    بدل التوقف بالكامل. يعيد محاولة واحدة إضافية عند بلوغ حد معدّل الاستخدام
    (429 Too Many Requests) باحترام رأس retry-after، لأن الخطة المجانية في
    Groq محدودة العدد بالدقيقة (RPM/TPM)."""
    try:
        from groq import Groq
    except Exception:
        return None
    prompt = _mapping_prompt(question, sections)
    client = Groq(api_key=api_key)
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=300,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return None
            return json.loads(match.group(0))
        except Exception as e:
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


def _candidate_sections(quiz):
    from .models import LessonSection

    lessons = set()
    if quiz.lesson_id:
        lessons.add(quiz.lesson)
    elif quiz.unit_id:
        lessons.update(quiz.unit.lessons.all())
    elif quiz.course_id:
        for unit in quiz.course.units.all():
            lessons.update(unit.lessons.all())
    if not lessons:
        return []
    return list(LessonSection.objects.filter(lesson__in=lessons).select_related("lesson"))


def map_quiz_questions(quiz):
    """ينفّذ الربط الذكي بين كل أسئلة الاختبار وأقسام شرح دروسه المرتبطة، ويحفظ
    النتائج كاقتراحات (status='suggested') بانتظار موافقة المدرّس. لا يغيّر أبدًا
    اقتراحًا سبق للمدرّس اعتماده أو رفضه يدويًا."""
    from accounts.utils import get_ai_keys

    from .models import QuestionMapping

    sections = _candidate_sections(quiz)
    groq_key, anthropic_key = get_ai_keys()
    if groq_key:
        provider_label = "groq"
    elif anthropic_key:
        provider_label = "anthropic"
    else:
        provider_label = "heuristic"
    matched = 0
    if not sections:
        return 0, provider_label

    already_final = set(
        QuestionMapping.objects.filter(question__quiz=quiz, status__in=["confirmed", "rejected"])
        .values_list("question_id", flat=True)
    )
    pending = list(quiz.questions.exclude(id__in=already_final).prefetch_related("choices"))
    for i, question in enumerate(pending):
        ai_result = None
        if groq_key:
            model = getattr(settings, "AI_MAPPING_MODEL", "openai/gpt-oss-120b")
            ai_result = _groq_map_question(question, sections, groq_key, model)
            if i < len(pending) - 1:
                # الخطة المجانية في Groq محدودة بعدد طلبات قليل بالدقيقة (RPM)،
                # فنترك فاصلًا صغيرًا بين الطلبات لتجنّب رفض الطلبات (429).
                time.sleep(2)
        elif anthropic_key:
            ai_result = _anthropic_map_question(question, sections, anthropic_key)
        if ai_result and ai_result.get("section_id"):
            section = next((s for s in sections if s.id == ai_result["section_id"]), None)
            confidence = max(0, min(100, int(ai_result.get("confidence") or 60)))
            secondary_ids = ai_result.get("secondary_ids") or []
            secondary = [s for s in sections if s.id in secondary_ids]
            reasoning = (ai_result.get("reasoning") or "")[:400]
        else:
            section, confidence, secondary = heuristic_best_section(
                question.text, [c.text for c in question.choices.all()], sections
            )
            reasoning = "مطابقة تقريبية بالاعتماد على تشابه الكلمات المفتاحية (لا يوجد مزوّد ذكاء اصطناعي فعّال حاليًا)."
        if not section:
            continue
        mapping, _ = QuestionMapping.objects.update_or_create(
            question=question,
            defaults={"section": section, "confidence": confidence, "status": "suggested", "reasoning": reasoning},
        )
        mapping.secondary_sections.set(secondary)
        matched += 1
    return matched, provider_label


def run_mapping_for_quiz(quiz):
    """يسجّل تشغيلة ربط (MappingRun) وينفّذ map_quiz_questions ضمنها، لتتبّع
    تاريخ عمليات الربط الذكي وحالتها لكل اختبار."""
    from .models import MappingRun

    run = MappingRun.objects.create(quiz=quiz)
    try:
        matched, provider_label = map_quiz_questions(quiz)
        run.status = "done"
        run.matched_count = matched
        run.provider = provider_label
    except Exception as e:
        run.status = "failed"
        run.error_message = str(e)[:2000]
    run.finished_at = timezone.now()
    run.save()
    return run
