from django.urls import reverse

from .decorators import CONFIGURABLE_PERMS, has_perm, staff_perms
from .models import SiteSetting


def site(request):
    return {"site": SiteSetting.get()}


def _item(label, icon, url, section=None, exact=False):
    return {"label": label, "icon": icon, "url": url, "section": section, "exact": exact}


def dashboard(request):
    u = getattr(request, "user", None)
    ctx = {}
    if not u or not u.is_authenticated:
        return ctx
    path = request.path
    from comms.models import Notification
    ctx["unread_count"] = Notification.objects.filter(user=u, is_read=False).count()
    ctx["impersonating"] = bool(request.session.get("impersonator"))
    ctx["base_template"] = {"student": "student/base.html", "teacher": "teacher/base.html"}.get(u.role, "owner/base.html")
    nav = []
    if u.role == "student":
        from billing.models import Enrollment
        from accounts.models import TeacherProfile
        nav = [
            _item("الرئيسية", "🏠", "/student/", exact=True),
            _item("المدرّسون", "👨‍🏫", "/student/teachers/"),
            _item("اشتراكاتي", "💳", "/student/subscriptions/"),
            _item("الإشعارات", "🔔", "/student/notifications/"),
            _item("الملف الشخصي", "👤", "/student/profile/"),
            _item("الدعم الفني", "🆘", "/support/"),
        ]
        ctx["my_enrollments"] = list(Enrollment.objects.filter(student=u).select_related("teacher__user"))
        code = (request.resolver_match.kwargs.get("code") if request.resolver_match else None) or None
        if code:
            t = TeacherProfile.objects.filter(teacher_code=code).select_related("user").first()
            if t:
                base = f"/student/t/{code}/"
                enr = next((e for e in ctx["my_enrollments"] if e.teacher_id == t.id), None)
                ctx["current_teacher"] = t
                ctx["current_enrollment"] = enr
                ctx["workspace_nav"] = [
                    _item("محتوى المنهج", "📚", base, exact=False),
                    _item("الواجبات", "📝", base + "assignments/"),
                    _item("بنك الأسئلة", "🧠", base + "bank/"),
                    _item("المفردات", "🔤", base + "vocabulary/"),
                    _item("أخطائي", "❌", base + "mistakes/"),
                    _item("تقدّمي الدراسي", "📈", base + "progress/"),
                    _item("الرسائل", "💬", base + "messages/"),
                    _item("إعلانات المدرّس", "📣", base + "announcements/"),
                ]
    elif u.role == "teacher":
        nav = [
            _item("الرئيسية", "🏠", "/teacher/", exact=True),
            _item("محتوى المنهج", "📚", "/teacher/courses/"),
            _item("الطلاب", "👨‍🎓", "/teacher/students/"),
            _item("الاشتراكات", "💳", "/teacher/subscriptions/"),
            _item("الحضور والغياب", "🗓️", "/teacher/attendance/"),
            _item("التحليلات", "📊", "/teacher/analytics/"),
            _item("الرسائل", "💬", "/teacher/messages/"),
            _item("الإعلانات", "📣", "/teacher/announcements/"),
            _item("الملف الشخصي", "👤", "/teacher/profile/"),
            _item("الإعدادات", "⚙️", "/teacher/settings/"),
            _item("الدعم والمساعدة", "🆘", "/support/"),
        ]
    elif u.role in ("owner", "staff"):
        allowed = staff_perms(u) if u.role == "staff" else None
        ctx["staff_can"] = {k: has_perm(u, k) for k in CONFIGURABLE_PERMS}
        items = [
            _item("نظرة عامة", "👑", "/owner/", "overview", True),
            _item("المدرّسون", "👨‍🏫", "/owner/teachers/", "teachers"),
            _item("الطلاب", "👨‍🎓", "/owner/students/", "students"),
            _item("التعيينات والاشتراكات", "🔗", "/owner/enrollments/", "enrollments"),
            _item("المدفوعات والتفعيل", "💳", "/owner/payments/", "payments"),
            _item("الإيرادات", "💰", "/owner/revenue/", "revenue"),
            _item("التحليلات", "📊", "/owner/analytics/", "analytics"),
            _item("المحتوى", "📁", "/owner/content/", "content"),
            _item("الإشعارات والإعلانات", "🔔", "/owner/notifications/", "notifications"),
            _item("الدعم والتذاكر", "🎫", "/owner/tickets/", "tickets"),
            _item("إعدادات المنصة", "⚙️", "/owner/settings/", "settings"),
            _item("صلاحيات الفريق", "🔑", "/owner/staff-permissions/", "settings"),
            _item("المساعد الذكي", "🤖", "/owner/ai-keys/", "settings"),
            _item("سجل التدقيق", "🔐", "/owner/audit/", "audit"),
            _item("الأمان", "🛡️", "/owner/security/", "security"),
        ]
        nav = [i for i in items if allowed is None or i["section"] in allowed]
    for i in nav + ctx.get("workspace_nav", []):
        i["active"] = (path == i["url"]) if i["exact"] else path.startswith(i["url"])
    # لا يُفعَّل "محتوى المنهج" إلا في صفحات المحتوى نفسها داخل مساحة المدرّس
    if ctx.get("workspace_nav"):
        base = ctx["workspace_nav"][0]["url"]
        others = [i["url"] for i in ctx["workspace_nav"][1:]]
        ctx["workspace_nav"][0]["active"] = path.startswith(base) and not any(path.startswith(o) for o in others) and not path.endswith("/subscribe/")
    ctx["nav"] = nav
    src = ctx.get("workspace_nav") or nav
    ctx["bottom_nav"] = src[:4]
    return ctx
