from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import TeacherProfile, private_storage

User = settings.AUTH_USER_MODEL


class Enrollment(models.Model):
    STATUS = [("pending", "بانتظار الدفع/المراجعة"), ("active", "نشط"), ("expired", "منتهٍ"),
              ("rejected", "مرفوض"), ("suspended", "موقوف")]
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="enrollments")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "teacher")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} ← {self.teacher}"

    @property
    def is_active(self):
        return self.status == "active" and bool(self.expires_at) and self.expires_at > timezone.now()

    @property
    def has_pending_payment(self):
        return self.payments.filter(status="pending").exists()

    @property
    def display_status(self):
        """active / expired / under_review / awaiting_payment / rejected / suspended"""
        if self.status == "active":
            return "active" if self.is_active else "expired"
        if self.status == "pending":
            return "under_review" if self.has_pending_payment else "awaiting_payment"
        return self.status

    @property
    def display_label(self):
        return {"active": "نشط", "expired": "منتهٍ", "under_review": "قيد المراجعة", "awaiting_payment": "بانتظار الدفع",
                "rejected": "مرفوض", "suspended": "موقوف"}[self.display_status]

    @property
    def days_left(self):
        if not self.is_active:
            return 0
        return max(0, (self.expires_at - timezone.now()).days + 1)


class Payment(models.Model):
    METHODS = [("vodafone_cash", "فودافون كاش"), ("wallet", "محفظة إلكترونية أخرى"), ("instapay", "إنستاباي"), ("manual", "تفعيل يدوي")]
    STATUS = [("pending", "قيد المراجعة"), ("approved", "مقبولة"), ("rejected", "مرفوضة")]
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField("المبلغ", max_digits=8, decimal_places=2)
    method = models.CharField("وسيلة الدفع", max_length=15, choices=METHODS, default="vodafone_cash")
    sender_phone = models.CharField("الرقم الذي حوّلت منه", max_length=20, blank=True)
    reference = models.CharField("رقم العملية (اختياري)", max_length=60, blank=True)
    receipt = models.ImageField("صورة إيصال التحويل", upload_to="receipts/", storage=private_storage, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    note = models.CharField("ملاحظة الإدارة", max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.amount} — {self.enrollment}"
