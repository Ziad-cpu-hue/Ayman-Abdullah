import django.core.files.storage
import django.db.models.deletion
import pathlib
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('learning', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_type', models.CharField(choices=[('pdf_text', 'PDF نصي'), ('pdf_scanned', 'PDF ممسوح ضوئيًا (صور)'), ('image', 'صورة واحدة'), ('images', 'مجموعة صور')], default='pdf_text', max_length=12, verbose_name='نوع الملف')),
                ('status', models.CharField(choices=[('uploaded', 'تم الرفع'), ('processing', 'قيد المعالجة'), ('ready', 'جاهز للمراجعة'), ('failed', 'فشلت المعالجة'), ('applied', 'تم إنشاء الاختبار')], default='uploaded', max_length=12, verbose_name='الحالة')),
                ('error_message', models.TextField(blank=True, verbose_name='رسالة الخطأ')),
                ('raw_text', models.TextField(blank=True, verbose_name='النص المستخرج (للمراجعة والتشخيص)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('applied_at', models.DateTimeField(blank=True, null=True)),
                ('quiz', models.ForeignKey(help_text='الاختبار الذي ستُضاف إليه الأسئلة المستوردة بعد المراجعة', on_delete=django.db.models.deletion.CASCADE, related_name='import_batches', to='learning.quiz')),
                ('teacher', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='import_batches', to='accounts.teacherprofile')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ImportFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(storage=django.core.files.storage.FileSystemStorage(base_url=None, location=pathlib.PurePosixPath('/home/claude/madrastak/private_media')), upload_to='imports/', verbose_name='الملف')),
                ('order', models.PositiveIntegerField(default=0)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='files', to='examimport.importbatch')),
            ],
            options={
                'ordering': ['order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='ImportedQuestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveIntegerField(default=0)),
                ('raw_block', models.TextField(blank=True, verbose_name='النص الأصلي كما ورد في الملف')),
                ('text', models.TextField(blank=True, verbose_name='نص السؤال المستخرج')),
                ('qtype', models.CharField(choices=[('mcq', 'اختيار من متعدد'), ('tf', 'صح / خطأ')], default='mcq', max_length=5, verbose_name='نوع السؤال')),
                ('correct_index', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='رقم الاختيار الصحيح (1 = الأول)')),
                ('confidence', models.PositiveSmallIntegerField(default=0, verbose_name='درجة الثقة بدقة التحليل (%)')),
                ('status', models.CharField(choices=[('ready', 'جاهز'), ('needs_review', 'يحتاج مراجعة'), ('unparsed', 'تعذّر تحليله'), ('rejected', 'مستبعد')], default='needs_review', max_length=14, verbose_name='الحالة')),
                ('notes', models.CharField(blank=True, max_length=300, verbose_name='ملاحظات المراجعة')),
                ('page_number', models.PositiveIntegerField(blank=True, null=True, verbose_name='رقم الصفحة التقريبي')),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='questions', to='examimport.importbatch')),
            ],
            options={
                'ordering': ['order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='ImportedChoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(blank=True, max_length=400)),
                ('is_correct', models.BooleanField(default=False)),
                ('order', models.PositiveIntegerField(default=0)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='choices', to='examimport.importedquestion')),
            ],
            options={
                'ordering': ['order', 'id'],
            },
        ),
    ]
