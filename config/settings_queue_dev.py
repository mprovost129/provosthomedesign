"""Isolated queue development. Never loads .env or production settings/credentials.

python manage.py test workqueue --settings=config.settings_queue_dev
python manage.py migrate --settings=config.settings_queue_dev
python manage.py runserver --settings=config.settings_queue_dev

The local queue-preview.sqlite3 is disposable development data. These settings
cannot point at Render through DATABASE_URL/DB_* and never send external email.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "queue-local-development-only-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver", "www.provosthomedesign.com", "web.provosthomedesign.com"]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.humanize", "django.contrib.sitemaps", "django_recaptcha", "corsheaders",
    "rest_framework", "rest_framework.authtoken", "core", "pages", "plans", "help", "api",
    "storages", "easy_thumbnails", "workqueue",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "core.middleware.SubdomainURLRoutingMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "workqueue.middleware.IntakeUploadLimitMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "core.context_processors.branding",
        "pages.context_processors.site_analytics", "plans.context_processors.plans_context",
        "workqueue.context_processors.client_features"]}}]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "queue-preview.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "America/New_York"
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = BASE_DIR / "queue-preview-media"
MEDIA_URL = "/media/"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            "intake_private": {"BACKEND": "django.core.files.storage.FileSystemStorage",
                               "OPTIONS": {"location": BASE_DIR / "queue-private-uploads", "base_url": None}}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "queue-preview@example.invalid"
GET_STARTED_TO_EMAILS = ["queue-preview@example.invalid"]
CONTACT_TO_EMAILS = ["queue-preview@example.invalid"]
WORK_QUEUE_ENABLED = True
WORK_INTAKE_ENABLED = True
BOOKING_ENABLED = True
BOOKING_CALENDAR_BACKEND = "preview"
BOOKING_HORIZON_DAYS = 60
GOOGLE_CALENDAR_ID = "preview-primary"
INTAKE_DIRECT_UPLOADS = False
INTAKE_LOCAL_DEVELOPMENT = True
INTAKE_PUBLIC_BASE_URL = "http://127.0.0.1:8765"
INTAKE_OWNER_EMAIL = "owner@example.invalid"
LOGIN_URL = "/admin/login/"
RECAPTCHA_PUBLIC_KEY = ""
RECAPTCHA_PRIVATE_KEY = ""
RECAPTCHA_SECRET_KEY = ""
RECAPTCHA_ENTERPRISE_API_KEY = ""
SILENCED_SYSTEM_CHECKS = ["django_recaptcha.recaptcha_test_key_error"]
STRIPE_SECRET_KEY = ""
STRIPE_PUBLISHABLE_KEY = ""
