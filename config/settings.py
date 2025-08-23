"""
Minimal Django settings for the 'config' project.
Django 5.2.x
"""
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Helpers ---
def csv_list(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]

# --- Core ---
SECRET_KEY = config("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = config("DEBUG", cast=bool, default=True)  # set False in prod

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=csv_list)
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="http://127.0.0.1,http://localhost",
    cast=csv_list,
)

# --- Apps ---
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.humanize", "django.contrib.sitemaps",
    "core", "pages", "plans",
    "sorl.thumbnail",
]

# Thumbnails (sorl-thumbnail)
THUMBNAIL_ALIASES = {
    "": {
        "plan_card":     {"size": (800, 500), "crop": True, "quality": 85},
        "plan_card@2x":  {"size": (1200, 750), "crop": True, "quality": 82},
    }
}
# Simple cache + defaults for sorl
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phd-local-cache",
    }
}
THUMBNAIL_KVSTORE = "sorl.thumbnail.kvstores.cached_db_kvstore"
THUMBNAIL_ENGINE = "sorl.thumbnail.engines.pil_engine.Engine"
THUMBNAIL_DEBUG = config("THUMBNAIL_DEBUG", cast=bool, default=False)
THUMBNAIL_QUALITY = config("THUMBNAIL_QUALITY", cast=int, default=85)

# --- Middleware ---
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# --- Templates ---
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "core.context_processors.branding",
            "pages.context_processors.site_analytics",
        ],
    },
}]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database (SQLite for dev) ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --- Internationalization ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static / Media ---
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Django 5 storages (recommended)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ======================================================================
# Email
# Strategy (in order):
# 1) If EMAIL_BACKEND is explicitly set -> respect it
# 2) If USE_MAILHOG=true -> SMTP to local MailHog/Papercut
# 3) If DEV_EMAIL_FILE=true -> file-based backend writing .eml to BASE_DIR/var/emails
# 4) If Graph env looks complete -> use msgraphbackend.MSGraphBackend
# 5) Else -> console backend
# ======================================================================

USE_MAILHOG = config("USE_MAILHOG", cast=bool, default=False)
DEV_EMAIL_FILE = config("DEV_EMAIL_FILE", cast=bool, default=False)
EXPLICIT_BACKEND = config("EMAIL_BACKEND", default="").strip() # type: ignore

# --- Microsoft Graph Email (env → settings) ---
MSGRAPH_USER_ID = config("MSGRAPH_USER_ID", default="")

# Prefer MICROSOFT_GRAPH_* but accept MSGRAPH_* for backwards compat
MICROSOFT_GRAPH_CLIENT_ID = config("MICROSOFT_GRAPH_CLIENT_ID", default=config("MSGRAPH_CLIENT_ID", default=""))
MICROSOFT_GRAPH_CLIENT_SECRET = config("MICROSOFT_GRAPH_CLIENT_SECRET", default=config("MSGRAPH_CLIENT_SECRET", default=""))
MICROSOFT_GRAPH_TENANT_ID = config("MICROSOFT_GRAPH_TENANT_ID", default=config("MSGRAPH_TENANT_ID", default=""))

# Aliases some backends look for:
MSGRAPH_CLIENT_ID = MICROSOFT_GRAPH_CLIENT_ID
MSGRAPH_CLIENT_SECRET = MICROSOFT_GRAPH_CLIENT_SECRET
MSGRAPH_TENANT_ID = MICROSOFT_GRAPH_TENANT_ID
MSGRAPH_SCOPE = config("MSGRAPH_SCOPE", default="https://graph.microsoft.com/.default")

def _graph_is_configured() -> bool:
    return all([
        MICROSOFT_GRAPH_CLIENT_ID,
        MICROSOFT_GRAPH_CLIENT_SECRET,
        MICROSOFT_GRAPH_TENANT_ID,
        MSGRAPH_USER_ID,
    ])

if EXPLICIT_BACKEND:
    EMAIL_BACKEND = EXPLICIT_BACKEND
elif USE_MAILHOG:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = config("MAILHOG_HOST", default="127.0.0.1")
    EMAIL_PORT = config("MAILHOG_PORT", cast=int, default=1025)
    EMAIL_HOST_USER = ""
    EMAIL_HOST_PASSWORD = ""
    EMAIL_USE_TLS = False
    EMAIL_USE_SSL = False
    EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", cast=int, default=10)
elif DEV_EMAIL_FILE:
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
    EMAIL_FILE_PATH = BASE_DIR / "var" / "emails"
elif _graph_is_configured():
    EMAIL_BACKEND = "msgraphbackend.MSGraphBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# From addresses
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=(MSGRAPH_USER_ID or "no-reply@localhost"))
SERVER_EMAIL = config("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
EMAIL_SUBJECT_PREFIX = config("EMAIL_SUBJECT_PREFIX", default="[Provost] ")
AUTO_ACK_FROM_EMAIL = config("AUTO_ACK_FROM_EMAIL", default=DEFAULT_FROM_EMAIL)
CONTACT_EMAIL_SUBJECT_PREFIX = config("CONTACT_EMAIL_SUBJECT_PREFIX", default="[Contact] ")

# Recipient lists used in views (fall back to primary contact email)
COMPANY_NAME = config("COMPANY_NAME", default="Provost Home Design")
CONTACT_NAME = config("CONTACT_NAME", default="Michael Provost")
CONTACT_EMAIL = config("CONTACT_EMAIL", default="mike@provosthomedesign.com")
CONTACT_PHONE = config("CONTACT_PHONE", default="508-243-7912")
CONTACT_ADDRESS = config("CONTACT_ADDRESS", default="7 Park St. Unit 1, Rehoboth, MA 02769")
BRAND_LOGO_STATIC = config("BRAND_LOGO_STATIC", default="images/phdlogo.svg")
TAGLINE = config("TAGLINE", default="Custom House & Framing Plans")

CONTACT_TO_EMAILS = config("CONTACT_TO_EMAILS", default=CONTACT_EMAIL, cast=csv_list)
GET_STARTED_TO_EMAILS = config("GET_STARTED_TO_EMAILS", default=CONTACT_EMAIL, cast=csv_list)
TESTIMONIAL_TO_EMAILS = config("TESTIMONIAL_TO_EMAILS", default=CONTACT_EMAIL, cast=csv_list)

# --- Security (tune for prod) ---
BEHIND_PROXY = config("BEHIND_PROXY", cast=bool, default=False)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", cast=bool, default=not DEBUG)
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", cast=bool, default=not DEBUG)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", cast=bool, default=not DEBUG)
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", cast=int, default=0 if DEBUG else 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config("SECURE_HSTS_INCLUDE_SUBDOMAINS", cast=bool, default=not DEBUG)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", cast=bool, default=not DEBUG)
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

USE_X_FORWARDED_HOST = True

GA_MEASUREMENT_ID = config("GA_MEASUREMENT_ID", "")
