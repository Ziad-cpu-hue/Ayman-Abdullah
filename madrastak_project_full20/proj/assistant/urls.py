from django.urls import path

from . import agent_views, owner_views, views

app_name = "assistant"

urlpatterns = []
for _role in ("student", "teacher"):
    _p = f"{_role}/assistant/"
    urlpatterns += [
        path(_p, views.home, {"role": _role}, name=f"{_role}_home"),
        path(_p + "send/", views.send, {"role": _role}, name=f"{_role}_send"),
        path(_p + "t/<int:pk>/", views.thread_view, {"role": _role}, name=f"{_role}_thread"),
        path(_p + "t/<int:pk>/delete/", views.delete_thread, {"role": _role}, name=f"{_role}_delete"),
    ]

# وكيل المدرّس (تنفيذ المهام من المحادثة) — للمدرّس فقط
urlpatterns += [
    path("teacher/assistant/agent/send/", agent_views.send, name="agent_send"),
    path("teacher/assistant/agent/run/<int:pk>/", agent_views.run_status, name="agent_run"),
    path("teacher/assistant/agent/upload/", agent_views.upload_doc, name="agent_upload"),
    path("teacher/assistant/agent/doc/<int:pk>/", agent_views.doc_status, name="agent_doc"),
    path("teacher/assistant/agent/doc/<int:pk>/delete/", agent_views.doc_delete, name="agent_doc_delete"),
    path("teacher/assistant/agent/action/<int:pk>/confirm/", agent_views.action_confirm, name="agent_action_confirm"),
    path("teacher/assistant/agent/action/<int:pk>/cancel/", agent_views.action_cancel, name="agent_action_cancel"),
]

urlpatterns += [
    path("owner/ai-keys/", owner_views.keys, name="owner_keys"),
    path("owner/ai-keys/new/", owner_views.key_new, name="owner_key_new"),
    path("owner/ai-keys/settings/", owner_views.settings_save, name="owner_settings"),
    path("owner/ai-keys/explanations/", owner_views.explain_run, name="owner_explain_run"),
    path("owner/ai-keys/<int:pk>/edit/", owner_views.key_edit, name="owner_key_edit"),
    path("owner/ai-keys/<int:pk>/toggle/", owner_views.key_toggle, name="owner_key_toggle"),
    path("owner/ai-keys/<int:pk>/test/", owner_views.key_test, name="owner_key_test"),
    path("owner/ai-keys/<int:pk>/reset/", owner_views.key_reset, name="owner_key_reset"),
    path("owner/ai-keys/<int:pk>/delete/", owner_views.key_delete, name="owner_key_delete"),
]
