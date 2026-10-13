from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

# أقسام لوحة المالك المتاحة لكل صلاحية داخل الفريق. المالك يرى كل شيء.
STAFF_SECTIONS = {
    "support": {"overview", "students", "tickets", "notifications", "messages_in"},
    "finance": {"overview", "payments", "enrollments", "revenue", "students"},
    "content": {"overview", "content", "teachers", "analytics"},
}


# الصلاحيات القابلة للضبط من لوحة المالك (صفحة «صلاحيات الفريق»). المالك يملك كل شيء دائمًا،
# وأقسام الإعدادات وسجل التدقيق والأمان وإدارة الفريق للمالك فقط ولا تُمنح لأحد من الفريق.
PERMISSION_GROUPS = [
    ("أقسام اللوحة", [
        ("overview", "نظرة عامة"),
        ("teachers", "المدرّسون (عرض / إضافة / تعديل)"),
        ("students", "الطلاب (عرض بياناتهم وإيقاف/تفعيل الحساب)"),
        ("enrollments", "التعيينات والاشتراكات"),
        ("payments", "المدفوعات وتفعيل الاشتراكات"),
        ("revenue", "الإيرادات"),
        ("analytics", "التحليلات"),
        ("content", "المحتوى"),
        ("notifications", "الإشعارات والإعلانات"),
        ("tickets", "الدعم والتذاكر"),
    ]),
    ("إجراءات حسّاسة", [
        ("student_passwords", "تغيير/توليد كلمة مرور طالب"),
        ("teacher_passwords", "تغيير كلمة مرور مدرّس"),
        ("teacher_impersonate", "الدخول لحساب مدرّس وإدارة كل ما يديره بنفسه (يتطلب أيضًا قسم «المدرّسون»)"),
    ]),
]
CONFIGURABLE_PERMS = {k for _, items in PERMISSION_GROUPS for k, _ in items}

# الإجراءات الحسّاسة كانت مرتبطة سابقًا بالأقسام نفسها؛ هذه القيم الافتراضية تحفظ السلوك القديم كما هو.
DEFAULT_STAFF_ACTIONS = {
    "support": {"student_passwords"},
    "finance": {"student_passwords"},
    "content": {"teacher_passwords"},
}


def default_staff_perms(staff_role):
    return (set(STAFF_SECTIONS.get(staff_role, set())) | DEFAULT_STAFF_ACTIONS.get(staff_role, set())) & CONFIGURABLE_PERMS


def staff_perms(user):
    """مجموعة الصلاحيات المسموحة لعضو الفريق (تُقرأ من إعدادات المنصة، وإلا الافتراضي). المالك: None = كل شيء."""
    if user.role != "staff":
        return None
    cached = getattr(user, "_staff_perms_cache", None)
    if cached is not None:
        return cached
    from .models import SiteSetting

    stored = SiteSetting.get().staff_permissions or {}
    if user.staff_role in stored:
        perms = set(stored[user.staff_role]) & CONFIGURABLE_PERMS
    else:
        perms = default_staff_perms(user.staff_role)
    user._staff_perms_cache = perms
    return perms


def has_perm(user, key):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.role == "owner":
        return True
    return user.role == "staff" and key in (staff_perms(user) or set())


def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **kw):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.user.role not in roles:
                messages.error(request, "ليست لديك صلاحية لفتح هذه الصفحة.")
                return redirect("go")
            return view(request, *a, **kw)
        return wrapper
    return deco


def owner_required(section="overview"):
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **kw):
            u = request.user
            if not u.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if u.role == "owner":
                return view(request, *a, **kw)
            if u.role == "staff" and section in (staff_perms(u) or set()):
                return view(request, *a, **kw)
            messages.error(request, "ليست لديك صلاحية لهذا القسم.")
            return redirect("owner:overview" if u.role == "staff" else "go")
        return wrapper
    return deco


student_required = role_required("student")
teacher_required = role_required("teacher")
