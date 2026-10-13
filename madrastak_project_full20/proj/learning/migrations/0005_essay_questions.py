from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0004_vocabularyword_example_translation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='question',
            name='qtype',
            field=models.CharField(choices=[('mcq', 'اختيار من متعدد'), ('tf', 'صح / خطأ'), ('essay', 'سؤال مقالي')], default='mcq', max_length=5, verbose_name='نوع السؤال'),
        ),
        migrations.AddField(
            model_name='studentanswer',
            name='text_answer',
            field=models.TextField(blank=True, default='', verbose_name='إجابة الطالب النصية (للأسئلة المقالية)'),
        ),
    ]
