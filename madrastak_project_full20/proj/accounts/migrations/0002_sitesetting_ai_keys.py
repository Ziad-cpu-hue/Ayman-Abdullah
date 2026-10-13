from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="groq_api_key",
            field=models.CharField(blank=True, max_length=200, verbose_name="مفتاح Groq API (لتشغيل ميزات الذكاء الاصطناعي)"),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="anthropic_api_key",
            field=models.CharField(blank=True, max_length=200, verbose_name="مفتاح Anthropic API (بديل عن Groq)"),
        ),
    ]
