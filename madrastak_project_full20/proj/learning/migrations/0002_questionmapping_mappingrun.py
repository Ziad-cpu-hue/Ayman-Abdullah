import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MappingRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('running', 'قيد التنفيذ'), ('done', 'اكتملت'), ('failed', 'فشلت')], default='running', max_length=10)),
                ('provider', models.CharField(blank=True, max_length=20)),
                ('matched_count', models.PositiveIntegerField(default=0)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('quiz', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mapping_runs', to='learning.quiz')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='QuestionMapping',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('confidence', models.PositiveSmallIntegerField(default=0)),
                ('status', models.CharField(choices=[('suggested', 'مقترح من الذكاء الاصطناعي'), ('confirmed', 'مؤكَّد من المدرّس'), ('rejected', 'مرفوض')], default='suggested', max_length=10)),
                ('reasoning', models.CharField(blank=True, max_length=400)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('question', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='mapping', to='learning.question')),
                ('section', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='primary_mappings', to='learning.lessonsection')),
                ('secondary_sections', models.ManyToManyField(blank=True, related_name='secondary_mappings', to='learning.lessonsection')),
            ],
        ),
    ]
