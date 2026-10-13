"""إعدادات مشروع منصة «مدرستك»."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me-in-production-please")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# ملاحظة: لا نستخدم django.contrib.admin نهائيًا؛ كل اللوحات مبنية من الصفر.
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "learning",
    "billing",
    "comms",
    "student",
    "teacher",
    "owner",
    "examimport",
    "uikit",
    "assistant",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.site",
                "accounts.context_processors.dashboard",
                "uikit.context_processors.ui",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# قاعدة البيانات: SQLite الافتراضية
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "/login/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "ar"
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# ملفات عامة (صور الأغلفة والصور الشخصية)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
# ملفات خاصة (إيصالات الدفع، ملفات ومقاطع الدروس): لا تُخدم إلا عبر Views تتحقق من الصلاحية
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
X_FRAME_OPTIONS = "SAMEORIGIN"

# إعدادات المنصة
SUBSCRIPTION_DAYS = 30            # مدة الاشتراك ثابتة: شهر واحد
MAX_FAILED_LOGINS = 5             # عدد المحاولات الفاشلة قبل القفل المؤقت
LOGIN_LOCK_MINUTES = 15
MAX_RECEIPT_MB = 5

# ===== استيراد الاختبارات (PDF/صور) والربط الذكي بالشرح =====
MAX_IMPORT_FILE_MB = int(os.environ.get("MAX_IMPORT_FILE_MB", 15))
MAX_IMPORT_FILES = 30
# لغات التعرف الضوئي على النص (Tesseract). القيمة الافتراضية "eng" تعمل دون أي إعداد إضافي.
# لدعم الملفات العربية ثبّت حزمة اللغة على الخادم: sudo apt-get install tesseract-ocr-ara
# ثم اضبط: OCR_LANGS=eng+ara
OCR_LANGS = os.environ.get("OCR_LANGS", "eng")
# مفتاح Groq API (مجاني) لتفعيل الربط الذكي الفعلي (Semantic Mapping) بين الأسئلة وأقسام الشرح.
# له الأولوية إن وُجد. إن تُرك فارغًا يُستخدم ANTHROPIC_API_KEY إن وُجد، وإلا يعمل النظام
# تلقائيًا بخوارزمية مطابقة تقريبية محلية (بدون اتصال بالإنترنت) حتى يستمر بالعمل.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# مفتاح Anthropic API (اختياري، كبديل احتياطي إن لم يُستخدم Groq).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# اسم النموذج المستخدم في الربط الذكي. الافتراضي مناسب لـ Groq (openai/gpt-oss-120b)؛
# لو استُخدم ANTHROPIC_API_KEY بدل Groq اضبط AI_MAPPING_MODEL إلى موديل Claude مناسب.
AI_MAPPING_MODEL = os.environ.get("AI_MAPPING_MODEL", "openai/gpt-oss-120b")

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
