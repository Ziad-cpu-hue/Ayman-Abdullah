from django.db import migrations, models


def number_existing_students(apps, schema_editor):
    """يمنح الطلاب المسجّلين حاليًا أرقامًا تسلسلية بترتيب تاريخ التسجيل (1، 2، 3 ...)."""
    StudentProfile = apps.get_model("accounts", "StudentProfile")
    n = 0
    for sp in StudentProfile.objects.select_related("user").order_by("user__date_joined", "pk"):
        n += 1
        StudentProfile.objects.filter(pk=sp.pk).update(student_number=n)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_sitesetting_ai_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="student_number",
            field=models.PositiveIntegerField(null=True, blank=True, editable=False, verbose_name="رقم الطالب (ID)"),
        ),
        migrations.RunPython(number_existing_students, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="studentprofile",
            name="student_number",
            field=models.PositiveIntegerField(null=True, blank=True, editable=False, unique=True, verbose_name="رقم الطالب (ID)"),
        ),
        migrations.AlterField(
            model_name="studentprofile",
            name="grade",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "الصف الأول الثانوي"), (2, "الصف الثاني الثانوي"), (3, "الصف الثالث الثانوي"),
                         (4, "الصف الأول الإعدادي"), (5, "الصف الثاني الإعدادي"), (6, "الصف الثالث الإعدادي")],
                verbose_name="المرحلة الدراسية"),
        ),
    ]
