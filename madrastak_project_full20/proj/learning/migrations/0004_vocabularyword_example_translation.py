from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0003_alter_lesson_video_file_alter_lessonfile_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='vocabularyword',
            name='example_translation',
            field=models.CharField(blank=True, max_length=300, verbose_name='ترجمة جملة المثال'),
        ),
    ]
