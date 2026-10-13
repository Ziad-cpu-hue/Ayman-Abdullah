"""توليد شروح الأسئلة الناقصة (بالعامية المصرية) بالذكاء الاصطناعي.

الاستخدام:
    python manage.py generate_explanations              # الناقص فقط
    python manage.py generate_explanations --retry-failed
    python manage.py generate_explanations --force      # إعادة توليد الكل
    python manage.py generate_explanations --limit 50
"""
import time

from django.core.management.base import BaseCommand

from assistant import explain, llm
from assistant.models import QuestionExplanation


class Command(BaseCommand):
    help = "يولّد شروح الأسئلة الناقصة بالذكاء الاصطناعي (مرة واحدة لكل سؤال)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        if not llm.is_configured():
            self.stderr.write("لا توجد مفاتيح ذكاء اصطناعي. أضفها من لوحة المالك أولًا.")
            return
        ids = explain.missing_question_ids(limit=opts["limit"] or None, include_failed=opts["retry_failed"], force=opts["force"])
        if opts["retry_failed"] or opts["force"]:
            QuestionExplanation.objects.filter(question_id__in=ids).update(status="pending", attempts=0)
        self.stdout.write(f"عدد الأسئلة المطلوب توليد شرحها: {len(ids)}")
        ok = fail = 0
        hits = 0
        queue = list(ids)
        while queue:
            qid = queue.pop(0)
            try:
                good = explain.generate_one(qid)
                ok += 1 if good else 0
                fail += 0 if good else 1
                hits = 0
                self.stdout.write(f"  [{ok + fail}/{len(ids)}] سؤال {qid}: {'تم' if good else 'تخطّي/فشل'}")
                time.sleep(1)
            except explain.Transient as e:
                if e.kind in ("config", "auth", "blocked"):
                    self.stderr.write(f"توقّف: {e}")
                    break
                hits += 1
                if hits > 6:
                    self.stderr.write(f"توقّف بعد تكرار الفشل: {e}")
                    break
                wait = min(20 * hits, 120)
                self.stdout.write(f"  ضغط/اتصال ({e.kind}) — انتظار {wait} ثانية ثم إعادة المحاولة...")
                queue.insert(0, qid)
                time.sleep(wait)
        self.stdout.write(self.style.SUCCESS(f"انتهى: {ok} شرح جاهز، {fail} تخطّي/فشل."))
