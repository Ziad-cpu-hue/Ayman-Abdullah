import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_names_labels_staff_permissions"),
        ("assistant", "0002_question_explanation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="assistantmessage",
            name="meta",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="CurriculumDoc",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("file", models.FileField(storage=accounts.models.private_storage, upload_to="curriculum/")),
                ("file_size", models.PositiveIntegerField(default=0)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("pages_done", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("processing", "قيد القراءة"), ("ready", "جاهز"), ("failed", "فشل")], default="processing", max_length=12)),
                ("error", models.CharField(blank=True, max_length=300)),
                ("used_ocr", models.BooleanField(default=False)),
                ("outline", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("teacher", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="curriculum_docs", to="accounts.teacherprofile")),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="CurriculumPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.PositiveIntegerField()),
                ("text", models.TextField(blank=True)),
                ("doc", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pages", to="assistant.curriculumdoc")),
            ],
            options={"ordering": ["number"], "unique_together": {("doc", "number")}},
        ),
        migrations.CreateModel(
            name="AgentRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("running", "قيد التنفيذ"), ("done", "تم"), ("failed", "فشل")], db_index=True, default="running", max_length=10)),
                ("steps", models.JSONField(blank=True, default=list)),
                ("error", models.CharField(blank=True, max_length=300)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="assistant.assistantmessage")),
                ("thread", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_runs", to="assistant.assistantthread")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_runs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="PendingAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(max_length=30)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("summary", models.CharField(max_length=300)),
                ("status", models.CharField(choices=[("pending", "بانتظار التأكيد"), ("done", "تم التنفيذ"), ("cancelled", "أُلغي"), ("failed", "فشل")], db_index=True, default="pending", max_length=10)),
                ("result", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("thread", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pending_actions", to="assistant.assistantthread")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_actions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["id"]},
        ),
    ]
