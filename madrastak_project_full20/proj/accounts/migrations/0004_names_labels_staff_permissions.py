from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_student_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="name_ar",
            field=models.CharField(blank=True, max_length=150, verbose_name="الاسم بالعربية (كما سُجّل)"),
        ),
        migrations.AddField(
            model_name="teacherprofile",
            name="display_name",
            field=models.CharField(
                blank=True, max_length=60, verbose_name="الاسم الظاهر للطلاب والزوّار",
                help_text="يتحكم فيه مالك المنصة فقط. اتركه فارغًا لإظهار الاسم الكامل، أو اكتب مثلًا: M"),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="names_in_english",
            field=models.BooleanField(default=True, verbose_name="تحويل أسماء المسجّلين الجدد إلى الإنجليزية تلقائيًا"),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="staff_permissions",
            field=models.JSONField(blank=True, default=dict, verbose_name="صلاحيات فريق الإدارة"),
        ),
    ]
