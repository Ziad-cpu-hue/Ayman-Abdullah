"""واجهات المساعد الذكي للطالب والمدرّس (نفس المنطق، قالب وقائمة جانبية مختلفان حسب الدور)."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required

from . import service
from .models import AssistantMessage, AssistantSetting, AssistantThread

MAX_THREADS = 40

BASES = {"student": "student/base.html", "teacher": "teacher/base.html"}
PREFIX = {"student": "/student/assistant/", "teacher": "/teacher/assistant/"}


def _guard(request, role):
    return request.user.role == role


def _serialize(m):
    from .agent_views import serialize_message
    return serialize_message(m)


def _page(request, role, thread=None):
    user = request.user
    threads = list(AssistantThread.objects.filter(user=user)[:MAX_THREADS])
    msgs = [_serialize(m) for m in thread.messages.all()] if thread else []
    ctx = {
        "base": BASES[role], "role": role, "prefix": PREFIX[role], "threads": threads, "thread": thread,
        "messages_json": msgs, "enabled": AssistantSetting.get().is_enabled,
    }
    if role == "teacher":
        # وكيل المدرّس: متابعة تشغيل جارٍ في الخلفية + ملفات المنهج المرفوعة
        from . import agent, curriculum
        from .agent_views import doc_json
        from .models import AgentRun, CurriculumDoc
        agent.sweep_stale()
        curriculum.mark_stale_failed()
        running = AgentRun.objects.filter(user=user, status="running").first()
        ctx["agent"] = True
        ctx["active_run"] = running.pk if running and (thread is None or running.thread_id == thread.pk) else None
        ctx["docs_json"] = [doc_json(d) for d in CurriculumDoc.objects.filter(teacher=user.teacher_profile)[:10]]
    return render(request, "assistant/chat.html", ctx)


@role_required("student", "teacher")
def home(request, role):
    if not _guard(request, role):
        return redirect("go")
    if request.GET.get("new"):
        return _page(request, role)
    last = AssistantThread.objects.filter(user=request.user).first()
    return _page(request, role, last)


@role_required("student", "teacher")
def thread_view(request, role, pk):
    if not _guard(request, role):
        return redirect("go")
    thread = get_object_or_404(AssistantThread, pk=pk, user=request.user)
    return _page(request, role, thread)


@role_required("student", "teacher")
@require_POST
def send(request, role):
    if not _guard(request, role):
        return JsonResponse({"error": "غير مصرح."}, status=403)
    text = (request.POST.get("message") or "").strip()
    thread = None
    tid = request.POST.get("thread_id")
    if tid and tid.isdigit():
        thread = AssistantThread.objects.filter(pk=int(tid), user=request.user).first()
    reply = service.answer(request.user, thread, text, role)
    if reply.error:
        return JsonResponse({"error": reply.text}, status=reply.status)
    if thread is None:
        thread = AssistantThread.objects.create(user=request.user, title=text[:60] or "محادثة جديدة")
    AssistantMessage.objects.create(thread=thread, role="user", content=text)
    am = AssistantMessage.objects.create(thread=thread, role="assistant", content=reply.text, mode=reply.mode,
                                         sources=reply.sources, lessons=reply.lessons)
    thread.save(update_fields=["updated_at"])
    return JsonResponse({"thread_id": thread.id, "thread_url": f"{PREFIX[role]}t/{thread.id}/", "message": _serialize(am),
                         "title": thread.title})


@role_required("student", "teacher")
@require_POST
def delete_thread(request, role, pk):
    if not _guard(request, role):
        return redirect("go")
    get_object_or_404(AssistantThread, pk=pk, user=request.user).delete()
    messages.success(request, "تم حذف المحادثة.")
    return redirect(PREFIX[role])
