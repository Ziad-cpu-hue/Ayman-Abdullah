import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_student_number'),
        ('learning', '0005_essay_questions'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CenterAttendance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('present', models.BooleanField(default=True)),
                ('source', models.CharField(choices=[('pdf', 'ملف PDF'), ('manual', 'يدوي')], default='pdf', max_length=10)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='center_attendance', to=settings.AUTH_USER_MODEL)),
                ('teacher', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='center_attendance', to='accounts.teacherprofile')),
            ],
            options={
                'ordering': ['-date'],
                'unique_together': {('teacher', 'student', 'date')},
            },
        ),
    ]
