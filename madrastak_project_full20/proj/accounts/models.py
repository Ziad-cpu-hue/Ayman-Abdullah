import random

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.files.storage import FileSystemStorage
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

phone_validator = RegexValidator(r"^01[0125]\d{8}$", "أدخل رقم هاتف مصري صحيحًا مثل 01012345678")

RESERVED_SLUGS = {
    "login", "logout", "register", "student", "teacher", "owner", "support", "media", "static",
    "pricing", "about", "contact", "teachers", "privacy", "terms", "go", "forgot-password", "admin",
}

private_storage = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)


class UserManager(BaseUserManager):
    def create_user(self, phone, password=None, **extra):
        if not phone:
            raise ValueError("رقم الهاتف مطلوب")
        user = self.model(phone=phone, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("role", "owner")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    ROLES = [("student", "طالب"), ("teacher", "مدرّس"), ("owner", "مالك المنصة"), ("staff", "فريق الإدارة")]
    STAFF_ROLES = [("support", "دعم فني"), ("finance", "مالية"), ("content", "مسؤول محتوى")]

    phone = models.CharField("رقم الهاتف", max_length=11, unique=True, validators=[phone_validator])
    full_name = models.CharField("الاسم الكامل", max_length=150)
    name_ar = models.CharField("الاسم بالعربية (كما سُجّل)", max_length=150, blank=True)
    role = models.CharField("الدور", max_length=10, choices=ROLES, default="student")
    staff_role = models.CharField("صلاحية الفريق", max_length=10, choices=STAFF_ROLES, blank=True)
    is_active = models.BooleanField("مفعّل", default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "مستخدم"

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    @property
    def first_name(self):
        return (self.full_name or "").split(" ")[0]

    @property
    def is_student(self):
        return self.role == "student"

    @property
    def is_teacher(self):
        return self.role == "teacher"

    @property
    def is_owner_side(self):
        return self.role in ("owner", "staff")


class StudentProfile(models.Model):
    GRADES = [(1, "الصف الأول الثانوي"), (2, "الصف الثاني الثانوي"), (3, "الصف الثالث الثانوي"),
              (4, "الصف الأول الإعدادي"), (5, "الصف الثاني الإعدادي"), (6, "الصف الثالث الإعدادي")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    # رقم الطالب التسلسلي على مستوى المنصة (1، 2، 3 ...) — يُمنح تلقائيًا عند التسجيل
    # ويُستخدم لتسجيل الحضور في السنتر عبر ملف الـ PDF.
    student_number = models.PositiveIntegerField("رقم الطالب (ID)", unique=True, null=True, blank=True, editable=False)
    parent_phone = models.CharField("رقم ولي الأمر", max_length=11, validators=[phone_validator])
    grade = models.PositiveSmallIntegerField("المرحلة الدراسية", choices=GRADES)
    avatar = models.ImageField("الصورة الشخصية", upload_to="avatars/", blank=True)

    def __str__(self):
        return self.user.full_name

    def save(self, *args, **kwargs):
        if self.student_number is None:
            from django.db import IntegrityError, transaction
            from django.db.models import Max
            for _ in range(5):  # إعادة المحاولة لو سجّل طالبان في اللحظة نفسها
                self.student_number = (StudentProfile.objects.aggregate(m=Max("student_number"))["m"] or 0) + 1
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError:
                    if self.pk:
                        raise
                    self.student_number = None
            raise IntegrityError("تعذّر توليد رقم الطالب")
        return super().save(*args, **kwargs)


def make_teacher_code():
    while True:
        code = "T" + "".join(random.choice("0123456789") for _ in range(6))
        if not TeacherProfile.objects.filter(teacher_code=code).exists():
            return code


class TeacherProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="teacher_profile")
    teacher_code = models.CharField("رقم المدرّس (Teacher ID)", max_length=10, unique=True, blank=True)
    slug = models.SlugField("الرابط الخاص", unique=True, blank=True)
    subject = models.CharField("المادة", max_length=80)
    grades = models.CharField("الصفوف التي يدرّسها", max_length=60, blank=True, help_text="مثال: الأول والثاني الثانوي")
    display_name = models.CharField("الاسم الظاهر للطلاب والزوّار", max_length=60, blank=True,
                                    help_text="يتحكم فيه مالك المنصة فقط. اتركه فارغًا لإظهار الاسم الكامل، أو اكتب مثلًا: M")
    bio = models.TextField("نبذة عن المدرّس", blank=True)
    photo = models.ImageField("الصورة", upload_to="teachers/", blank=True)
    cover = models.ImageField("صورة الغلاف", upload_to="teachers/covers/", blank=True)
    monthly_price = models.DecimalField("سعر الاشتراك الشهري (جنيه)", max_digits=8, decimal_places=2, default=10)
    is_listed = models.BooleanField("يظهر في قائمة المدرّسين العامة", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.teacher_code:
            self.teacher_code = make_teacher_code()
        if not self.slug:
            self.slug = f"mr-{self.teacher_code.lower()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.full_name} — {self.subject}"

    @property
    def name(self):
        return self.user.full_name

    @property
    def is_active(self):
        return self.user.is_active

    @property
    def display_label(self):
        """الاسم الذي يراه الطلاب والزوّار (قد يكون حرفًا واحدًا مثل M بقرار مالك المنصة)."""
        return (self.display_name or "").strip() or self.user.full_name


class SiteSetting(models.Model):
    platform_name = models.CharField("اسم المنصة", max_length=80, default="مدرستك")
    tagline = models.CharField("الشعار النصي", max_length=200, default="مساحتك التعليمية الكاملة مع مدرّسيك")
    wallet_number = models.CharField("رقم المحفظة لاستلام الاشتراكات", max_length=20, blank=True)
    wallet_holder = models.CharField("اسم صاحب المحفظة", max_length=100, blank=True)
    payment_instructions = models.TextField("تعليمات الدفع للطالب", blank=True,
                                            default="حوّل قيمة الاشتراك إلى المحفظة ثم ارفع صورة إيصال التحويل وسيتم تفعيل اشتراكك بعد المراجعة.")
    support_phone = models.CharField("هاتف الدعم", max_length=20, blank=True)
    support_email = models.EmailField("بريد الدعم", blank=True)
    default_monthly_price = models.DecimalField("السعر الافتراضي لاشتراك المدرّس الجديد", max_digits=8, decimal_places=2, default=10)
    groq_api_key = models.CharField("مفتاح Groq API (لتشغيل ميزات الذكاء الاصطناعي)", max_length=200, blank=True)
    anthropic_api_key = models.CharField("مفتاح Anthropic API (بديل عن Groq)", max_length=200, blank=True)
    privacy_text = models.TextField("سياسة الخصوصية", blank=True)
    terms_text = models.TextField("الشروط والأحكام", blank=True)
    registration_open = models.BooleanField("فتح تسجيل الطلاب الجدد", default=True)
    names_in_english = models.BooleanField("تحويل أسماء المسجّلين الجدد إلى الإنجليزية تلقائيًا", default=True)
    # صلاحيات فريق الإدارة: {"support": ["students", ...], "finance": [...], "content": [...]} — الفارغ = الافتراضي
    staff_permissions = models.JSONField("صلاحيات فريق الإدارة", default=dict, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Testimonial(models.Model):
    name = models.CharField("الاسم", max_length=100)
    role = models.CharField("الصفة", max_length=100, blank=True)
    text = models.TextField("الرأي")
    is_active = models.BooleanField(default=True)


class ContactMessage(models.Model):
    KINDS = [("contact", "تواصل"), ("password_reset", "طلب استعادة كلمة المرور"), ("teacher_request", "طلب انضمام مدرّس")]
    kind = models.CharField(max_length=20, choices=KINDS, default="contact")
    name = models.CharField("الاسم", max_length=100, blank=True)
    phone = models.CharField("الهاتف", max_length=20, blank=True)
    message = models.TextField("الرسالة", blank=True)
    is_handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    action = models.CharField(max_length=120)
    target = models.CharField(max_length=200, blank=True)
    detail = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class LoginAttempt(models.Model):
    phone = models.CharField(max_length=20)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
