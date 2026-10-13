"""سياق الواجهة الجديدة: الأيقونات وقوائم التنقل للطالب والمدرّس. لا يغيّر أي بيانات."""
from .icons import ICONS


def _item(label, icon, url, active=False, badge=0):
    return {"label": label, "icon": icon, "url": url, "active": active, "badge": badge}


def ui(request):
    ctx = {"ICONS": ICONS}
    u = getattr(request, "user", None)
    if not u or not u.is_authenticated:
        return ctx
    p = request.path
    if u.role == "student":
        unread = 0
        try:
            from comms.models import Notification
            unread = Notification.objects.filter(user=u, is_read=False).count()
        except Exception:
            pass
        in_ws = p.startswith("/student/t/")
        is_sub = in_ws and p.rstrip("/").endswith("/subscribe")
        ctx["ui_nav"] = [
            _item("الرئيسية", "fa-house", "/student/", p == "/student/"),
            _item("محتوى الدروس", "fa-book-open", "/student/content/", (in_ws and not is_sub) or p.startswith("/student/content/")),
            _item("تقارير الأداء", "fa-chart-line", "/student/performance/", p.startswith("/student/performance/")),
            _item("المساعد الذكي", "fa-robot", "/student/assistant/", p.startswith("/student/assistant/")),
            _item("المعلمون", "fa-chalkboard-user", "/student/teachers/", p.startswith("/student/teachers/") or p.startswith("/student/join/") or is_sub),
            _item("الملف الشخصي", "fa-user", "/student/profile/", p.startswith("/student/profile/")),
            _item("اشتراكاتي", "fa-wallet", "/student/subscriptions/", p.startswith("/student/subscriptions/")),
            _item("الإشعارات", "fa-bell", "/student/notifications/", p.startswith("/student/notifications/"), unread),
            _item("الدعم الفني", "fa-life-ring", "/support/", p.startswith("/support/")),
        ]
        # تبويب «محتوى الدروس» وكل الصفحات داخله تعرض الهيدر فقط بدون القائمة الجانبية.
        ctx["ui_hide_sidebar"] = (in_ws and not is_sub) or p.startswith("/student/content/")
        ctx["ui_unread"] = unread
        try:
            ctx["ui_student_profile"] = u.student_profile
        except Exception:
            ctx["ui_student_profile"] = None
    elif u.role == "teacher":
        unread = 0
        try:
            from comms.models import ChatMessage
            unread = (ChatMessage.objects.filter(conversation__teacher__user=u, is_read=False)
                      .exclude(sender=u).count())
        except Exception:
            pass
        cur_prefixes = ("/teacher/curriculum", "/teacher/courses", "/teacher/unit", "/teacher/lesson", "/teacher/section",
                        "/teacher/file", "/teacher/vocab", "/teacher/quiz", "/teacher/question", "/teacher/mapping",
                        "/teacher/create", "/teacher/ai", "/teacher/import")
        # صفحات «محتوى المنهج» وكل ما يتفرّع منها (دورات/وحدات/دروس/اختبارات/أسئلة...) تعرض
        # الهيدر فقط بدون القائمة الجانبية. باقي صفحات المدرّس تحتفظ بالشريط الجانبي كما هو.
        ctx["ui_hide_sidebar"] = p.startswith(cur_prefixes)
        if p == "/teacher/" or p == "/teacher":
            active = "home"
        elif p.startswith(cur_prefixes):
            active = "courses"
        elif p.startswith("/teacher/content"):
            active = "content"
        elif p.startswith("/teacher/students"):
            active = "students"
        elif p.startswith("/teacher/reports"):
            active = "reports"
        elif p.startswith("/teacher/subscriptions"):
            active = "subscriptions"
        elif p.startswith("/teacher/attendance"):
            active = "attendance"
        elif p.startswith("/teacher/analytics"):
            active = "analytics"
        elif p.startswith("/teacher/assistant"):
            active = "assistant"
        elif p.startswith("/teacher/profile") or p.startswith("/teacher/settings"):
            active = "profile"
        else:
            active = ""
        ctx["ui_active"] = active
        ctx["ui_nav"] = [
            _item("الرئيسية", "fa-house", "/teacher/", p == "/teacher/"),
            _item("محتوى المنهج", "fa-layer-group", "/teacher/courses/", p.startswith(cur_prefixes)),
            _item("إدارة المحتوى الحالي", "fa-table-list", "/teacher/content/", p.startswith("/teacher/content/")),
            _item("قائمة الطلاب", "fa-list-check", "/teacher/students/", p.startswith("/teacher/students/")),
            _item("تقرير الطلاب", "fa-chart-pie", "/teacher/reports/", p.startswith("/teacher/reports/")),
            _item("الاشتراكات", "fa-credit-card", "/teacher/subscriptions/", p.startswith("/teacher/subscriptions/")),
            _item("الغياب والحضور", "fa-calendar-check", "/teacher/attendance/", p.startswith("/teacher/attendance/")),
            _item("التحليلات", "fa-chart-column", "/teacher/analytics/", p.startswith("/teacher/analytics/")),
            _item("المساعد الذكي", "fa-robot", "/teacher/assistant/", p.startswith("/teacher/assistant/")),
            _item("الملف الشخصي", "fa-id-badge", "/teacher/profile/", p.startswith("/teacher/profile/") or p.startswith("/teacher/settings/")),
        ]
        ctx["ui_unread"] = unread
        try:
            ctx["ui_teacher_profile"] = u.teacher_profile
        except Exception:
            ctx["ui_teacher_profile"] = None
    return ctx
