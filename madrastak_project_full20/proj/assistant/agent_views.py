"""نقاط النهاية (JSON) لوكيل المدرّس: إرسال طلب، متابعة التقدّم، رفع ملف المنهج، تأكيد/إلغاء الإجراءات."""
import os
from functools import wraps

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from . import agent, curriculum, llm, service
from . import agent_tools as tools
from .models import AgentRun, AssistantMessage, AssistantSetting, AssistantThread, CurriculumDoc, PendingAction

MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_DOCS_PER_TEACHER = 20
MAX_AGENT_MESSAGE = 2500


def teacher_json(view):
    @wraps(view)
    def wrapper(request, *a, **kw):
        u = request.user
        if not u.is_authenticated:
            return JsonResponse({"error": "انتهت الجلسة. أعد تسجيل الدخول."}, status=401)
        if u.role != "teacher" or not hasattr(u, "teacher_profile"):
            return JsonResponse({"error": "غير مصرّح."}, status=403)
        return view(request, *a, **kw)
    return wrapper


# ------------------------------------------------------------------ تسلسل البيانات للواجهة
def actions_state(ids):
    rows = {a.pk: a for a in PendingAction.objects.filter(pk__in=ids)}
    return [{"id": rows[i].pk, "summary": rows[i].summary, "status": rows[i].status, "result": rows[i].result}
            for i in ids if i in rows]


def doc_json(d):
    return {"id": d.pk, "title": d.title, "status": d.status, "pages_done": d.pages_done, "page_count": d.page_count,
            "error": d.error, "ocr": d.used_ocr}


def serialize_message(m):
    out = {"id": m.id, "role": m.role, "content": m.content, "mode": m.mode, "sources": m.sources, "lessons": m.lessons,
           "created": m.created_at.isoformat()}
    meta = m.meta or {}
    if meta.get("links"):
        out["links"] = meta["links"]
    if meta.get("actions"):
        out["actions"] = actions_state(meta["actions"])
    return out


# ------------------------------------------------------------------ إرسال طلب للوكيل
@teacher_json
@require_POST
def send(request):
    user = request.user
    cfg = AssistantSetting.get()
    if not cfg.is_enabled:
        return JsonResponse({"error": "المساعد الذكي متوقف حاليًا من إدارة المنصة."}, status=403)
    text = (request.POST.get("message") or "").strip()
    if not text:
        return JsonResponse({"error": "اكتب رسالتك أولًا."}, status=400)
    if len(text) > MAX_AGENT_MESSAGE:
        return JsonResponse({"error": f"الرسالة طويلة جدًا (الحد {MAX_AGENT_MESSAGE} حرفًا). اختصرها أو قسّمها."}, status=400)
    if service.rate_limited(user):
        return JsonResponse({"error": "وصلت للحد الأقصى من الرسائل في الساعة. حاول بعد قليل."}, status=429)
    if not llm.is_configured():
        return JsonResponse({"error": "المساعد الذكي غير مفعّل بعد (لم تُضف مفاتيح الذكاء الاصطناعي). تواصل مع إدارة المنصة."}, status=503)
    agent.sweep_stale()
    if AgentRun.objects.filter(user=user, status="running").exists():
        return JsonResponse({"error": "ما زلت أنفّذ طلبك السابق. انتظر حتى أنتهي ثم أرسل الجديد."}, status=409)

    thread = None
    tid = request.POST.get("thread_id")
    if tid and tid.isdigit():
        thread = AssistantThread.objects.filter(pk=int(tid), user=user).first()
    if thread is None:
        thread = AssistantThread.objects.create(user=user, title=text[:60] or "محادثة جديدة")
    um = AssistantMessage.objects.create(thread=thread, role="user", content=text)
    run = agent.start_run(user, thread)
    return JsonResponse({"run_id": run.pk, "thread_id": thread.pk, "thread_url": f"/teacher/assistant/t/{thread.pk}/",
                         "user_message_id": um.pk, "title": thread.title})


@teacher_json
@require_GET
def run_status(request, pk):
    run = AgentRun.objects.filter(pk=pk, user=request.user).select_related("message").first()
    if run is None:
        return JsonResponse({"error": "غير موجود."}, status=404)
    if run.status == "running":
        agent.sweep_stale()
        run.refresh_from_db()
    data = {"status": run.status, "steps": run.steps or []}
    if run.status != "running" and run.message_id:
        data["message"] = serialize_message(run.message)
    return JsonResponse(data)


# ------------------------------------------------------------------ ملف المنهج
@teacher_json
@require_POST
def upload_doc(request):
    teacher = request.user.teacher_profile
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "اختر ملف PDF أولًا."}, status=400)
    if os.path.splitext(f.name)[1].lower() != ".pdf":
        return JsonResponse({"error": "الصيغة غير مدعومة. ارفع ملف PDF."}, status=400)
    if f.size > MAX_PDF_BYTES:
        return JsonResponse({"error": "حجم الملف كبير (الحد الأقصى 80 ميجابايت)."}, status=400)
    if CurriculumDoc.objects.filter(teacher=teacher).count() >= MAX_DOCS_PER_TEACHER:
        return JsonResponse({"error": "وصلت للحد الأقصى من ملفات المنهج. احذف ملفًا قديمًا أولًا."}, status=400)
    title = os.path.splitext(os.path.basename(f.name))[0][:190] or "ملف المنهج"
    doc = CurriculumDoc.objects.create(teacher=teacher, title=title, file=f, file_size=f.size)
    curriculum.start_processing(doc.pk)
    return JsonResponse({"doc": doc_json(doc)})


@teacher_json
@require_GET
def doc_status(request, pk):
    curriculum.mark_stale_failed()
    d = CurriculumDoc.objects.filter(pk=pk, teacher=request.user.teacher_profile).first()
    if d is None:
        return JsonResponse({"error": "غير موجود."}, status=404)
    return JsonResponse({"doc": doc_json(d)})


@teacher_json
@require_POST
def doc_delete(request, pk):
    d = CurriculumDoc.objects.filter(pk=pk, teacher=request.user.teacher_profile).first()
    if d is None:
        return JsonResponse({"error": "غير موجود."}, status=404)
    try:
        d.file.delete(save=False)
    except Exception:
        pass
    d.delete()
    return JsonResponse({"ok": True})


# ------------------------------------------------------------------ تأكيد / إلغاء الإجراءات
@teacher_json
@require_POST
def action_confirm(request, pk):
    with transaction.atomic():
        a = PendingAction.objects.select_for_update().filter(pk=pk, user=request.user).first()
        if a is None:
            return JsonResponse({"error": "غير موجود."}, status=404)
        if a.status != "pending":
            return JsonResponse({"status": a.status, "result": a.result})
        try:
            result, status = tools.execute_action(a), "done"
        except tools.ToolError as e:
            result, status = str(e), "failed"
        except Exception:
            result, status = "تعذّر تنفيذ الإجراء. حاول من صفحة العنصر مباشرة.", "failed"
        a.status, a.result, a.resolved_at = status, result[:290], timezone.now()
        a.save(update_fields=["status", "result", "resolved_at"])
    return JsonResponse({"status": a.status, "result": a.result})


@teacher_json
@require_POST
def action_cancel(request, pk):
    a = PendingAction.objects.filter(pk=pk, user=request.user, status="pending").first()
    if a is None:
        return JsonResponse({"error": "غير موجود أو تمت معالجته."}, status=404)
    a.status, a.resolved_at = "cancelled", timezone.now()
    a.save(update_fields=["status", "resolved_at"])
    return JsonResponse({"status": "cancelled", "result": ""})
