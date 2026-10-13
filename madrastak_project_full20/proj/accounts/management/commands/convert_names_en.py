from django.core.management.base import BaseCommand

from accounts.models import User
from accounts.translit import has_arabic, to_english_name


class Command(BaseCommand):
    help = "يحوّل أسماء الطلاب المسجّلين سابقًا بالعربية إلى الإنجليزية (يحفظ الأصل في name_ar). لا يُشغَّل تلقائيًا."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="نفّذ التحويل فعليًا (بدونها: معاينة فقط)")

    def handle(self, *args, **opts):
        n = 0
        for u in User.objects.filter(role="student").order_by("id"):
            if not has_arabic(u.full_name):
                continue
            en = to_english_name(u.full_name)[:150].strip()
            if not en:
                continue
            n += 1
            self.stdout.write(f"{u.pk}: {u.full_name}  ->  {en}")
            if opts["apply"]:
                u.name_ar = u.name_ar or u.full_name[:150]
                u.full_name = en
                u.save(update_fields=["full_name", "name_ar"])
        self.stdout.write(self.style.SUCCESS(f"{'تم تحويل' if opts['apply'] else 'سيتم تحويل (معاينة)'} {n} اسم."))
