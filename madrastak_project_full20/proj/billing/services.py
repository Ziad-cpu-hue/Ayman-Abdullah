from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from comms.services import notify


def activate_enrollment(enrollment, actor, payment=None, days=None):
    """يفعّل الاشتراك شهرًا (أو يمدّده إن كان نشطًا) ويُرسل إشعارًا للطالب."""
    days = days or settings.SUBSCRIPTION_DAYS
    now = timezone.now()
    base = enrollment.expires_at if (enrollment.is_active and enrollment.expires_at) else now
    enrollment.expires_at = base + timedelta(days=days)
    enrollment.status = "active"
    if not enrollment.started_at:
        enrollment.started_at = now
    enrollment.activated_by = actor
    enrollment.save()
    if payment:
        payment.status = "approved"
        payment.reviewed_by = actor
        payment.reviewed_at = now
        payment.save()
    notify(enrollment.student, "تم تفعيل اشتراكك 🎉",
           f"اشتراكك مع {enrollment.teacher.display_label} نشط حتى {enrollment.expires_at:%Y-%m-%d}.",
           f"/student/t/{enrollment.teacher.teacher_code}/", "subscription")
    return enrollment


def reject_payment(payment, actor, note=""):
    payment.status = "rejected"
    payment.note = note
    payment.reviewed_by = actor
    payment.reviewed_at = timezone.now()
    payment.save()
    e = payment.enrollment
    if e.status == "pending" and not e.payments.filter(status="pending").exists():
        pass  # يبقى "بانتظار الدفع" ليعيد الطالب المحاولة
    notify(e.student, "تعذّر قبول إيصال الدفع",
           note or "يرجى رفع صورة إيصال واضحة للتحويل والمحاولة مرة أخرى.",
           f"/student/t/{e.teacher.teacher_code}/subscribe/", "subscription")
