"""
Minimal Django settings for the 'config' project.
Django 5.2.x
"""
from pathlib import Path
import os
from decouple import config
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Helpers ---
def csv_list(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]

# --- Core ---
SECRET_KEY = config("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = config("DEBUG", cast=bool, default=False)  # set False in prod
RENDER_EXTERNAL_HOSTNAME = config("RENDER_EXTERNAL_HOSTNAME", default="").strip()

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=csv_list)
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="http://127.0.0.1,http://localhost",
    cast=csv_list,
)
if RENDER_EXTERNAL_HOSTNAME:
    render_origin = f"https://{RENDER_EXTERNAL_HOSTNAME}"
    if render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(render_origin)

# CORS configuration for desktop app and optional production frontends
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:1420,http://127.0.0.1:1420" if DEBUG else "",
    cast=csv_list,
)
CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", cast=bool, default=DEBUG)
CORS_ALLOW_CREDENTIALS = True

# --- Apps ---
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.humanize", "django.contrib.sitemaps",
    "django_recaptcha",
    "corsheaders",
    "rest_framework", "rest_framework.authtoken",
    "core", "pages", "plans", "help", "api", "storages"
]

# Simple cache + defaults for sorl
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phd-local-cache",
    }
}

# --- Middleware ---
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
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
            "plans.context_processors.plans_context",
        ],
    },
}]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---
# Prefer Render's DATABASE_URL, but keep the older DB_* env vars as a fallback.
DATABASE_URL = config("DATABASE_URL", default="")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=config("DB_CONN_MAX_AGE", cast=int, default=600),
            ssl_require=config("DB_SSL_REQUIRE", cast=bool, default=True),
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": config("DB_ENGINE", default="django.db.backends.sqlite3"),
            "NAME": config("DB_NAME", default=str(BASE_DIR / "db.sqlite3")),
            "USER": config("DB_USER", default=""),
            "PASSWORD": config("DB_PASSWORD", default=""),
            "HOST": config("DB_HOST", default=""),
            "PORT": config("DB_PORT", default=""),
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

# Local media defaults for development. These are intentionally overridden
# below when S3 media is enabled.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST Framework ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

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
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", cast=bool, default=False)  # disable HTTPS redirect in local dev so CORS works over http
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

# --- Google reCAPTCHA ---
# Support both v2 and v3 key naming conventions
RECAPTCHA_PUBLIC_KEY = config("RECAPTCHA_PUBLIC_KEY", default=config("RECAPTCHA_SITE_KEY", default=""))
RECAPTCHA_PRIVATE_KEY = config("RECAPTCHA_PRIVATE_KEY", default=config("RECAPTCHA_SECRET_KEY", default=""))
# Use reCAPTCHA v2 Checkbox
RECAPTCHA_REQUIRED_SCORE = config("RECAPTCHA_MIN_SCORE", cast=float, default=0.85)
SILENCED_SYSTEM_CHECKS = ['django_recaptcha.recaptcha_test_key_error']  # Only for dev

# --- Spam / form protection ---
RECAPTCHA_SITE_KEY = config("RECAPTCHA_SITE_KEY", "")
RECAPTCHA_SECRET_KEY = config("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_MIN_SCORE = float(config("RECAPTCHA_MIN_SCORE", "0.5"))

PLAN_CHANGE_RATE_LIMIT = config("PLAN_CHANGE_RATE_LIMIT", "3/h")
PLAN_CHANGE_MIN_MESSAGE_LEN = int(config("PLAN_CHANGE_MIN_MESSAGE_LEN", "20"))

# --- Logging ---
# Create logs directory if it doesn't exist
import os
LOGS_DIR = BASE_DIR / "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": LOGS_DIR / "django.log",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"] if DEBUG else ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"] if DEBUG else ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "pages": {
            "handlers": ["console"] if DEBUG else ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "plans": {
            "handlers": ["console"] if DEBUG else ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# ======================================================================
# Authentication & Client Portal
# ======================================================================

# Login/logout URLs
LOGIN_URL = '/portal/login/'
LOGIN_REDIRECT_URL = '/portal/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Session settings
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_COOKIE_SECURE = not DEBUG  # True in production
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# ======================================================================
# Stripe Payment Processing
# ======================================================================

STRIPE_PUBLISHABLE_KEY = config("STRIPE_PUBLISHABLE_KEY", default="")
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = config("STRIPE_WEBHOOK_SECRET", default="")

# Stripe test mode check (keys starting with pk_test/sk_test are test mode)
STRIPE_TEST_MODE = STRIPE_PUBLISHABLE_KEY.startswith('pk_test') if STRIPE_PUBLISHABLE_KEY else True

# ======================================================================
# Company Information (for invoices, emails, etc.)
# ======================================================================

COMPANY_NAME = config("COMPANY_NAME", default="Provost Home Design")
CONTACT_EMAIL = config("CONTACT_EMAIL", default=DEFAULT_FROM_EMAIL)
CONTACT_PHONE = config("CONTACT_PHONE", default="(555) 123-4567")
CONTACT_ADDRESS = config("CONTACT_ADDRESS", default="123 Main St, Your City, ST 12345")

# ======================================================================
# S3 Media Storage
# ======================================================================
# Render has an ephemeral filesystem, so user-uploaded images/files should
# Uploaded media on S3 -----------------------------------------------------
# Static assets stay on WhiteNoise. User/admin-uploaded media such as plan
# images, gallery images, about photos, and brand logos are served from S3.
#
# Required Render env vars:
#   AWS_ACCESS_KEY_ID=<IAM access key>
#   AWS_SECRET_ACCESS_KEY=<IAM secret key>
#   AWS_STORAGE_BUCKET_NAME=phd-media-prod
#   AWS_S3_REGION_NAME=us-east-1
#
# Optional:
#   USE_S3_MEDIA=True
#   AWS_S3_CUSTOM_DOMAIN=phd-media-prod.s3.us-east-1.amazonaws.com

RUNNING_ON_RENDER = bool(os.environ.get("RENDER")) or bool(RENDER_EXTERNAL_HOSTNAME)
AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY", default="")
AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME", default="phd-media-prod")
AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="us-east-1")
AWS_S3_CUSTOM_DOMAIN = config(
    "AWS_S3_CUSTOM_DOMAIN",
    default=f"{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com",
)
AWS_S3_ADDRESSING_STYLE = "virtual"
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = False
AWS_S3_FILE_OVERWRITE = False
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "max-age=86400",
}

# Turn S3 on automatically in production when the bucket/credentials exist.
# This prevents Render from silently falling back to local /media/ URLs if
# USE_S3_MEDIA was forgotten. Set USE_S3_MEDIA=False only for local dev.
S3_ENV_PRESENT = bool(AWS_STORAGE_BUCKET_NAME and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)
USE_S3_MEDIA = config("USE_S3_MEDIA", cast=bool, default=(S3_ENV_PRESENT or RUNNING_ON_RENDER or not DEBUG))

if USE_S3_MEDIA:
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/media/"
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "region_name": AWS_S3_REGION_NAME,
            "location": "media",
            "default_acl": None,
            "querystring_auth": False,
            "file_overwrite": False,
            "addressing_style": AWS_S3_ADDRESSING_STYLE,
        },
    }
