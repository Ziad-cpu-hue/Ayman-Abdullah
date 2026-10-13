from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assistant"
    verbose_name = "المساعد الذكي"

    def ready(self):
        from . import signals  # noqa: F401  (يربط إشارات توليد شروح الأسئلة)
