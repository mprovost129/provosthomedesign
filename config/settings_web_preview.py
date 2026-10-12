"""Isolated rebrand review; no .env, production database, email, or analytics.

Run checks/tests with this module. Preview: runserver 127.0.0.1:8766.
"""
from .settings_queue_dev import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                         "NAME": BASE_DIR / "output" / "slater-preview.sqlite3"}}
WEB_DESIGN_HOST = "127.0.0.1"
WEB_DESIGN_URL = "https://web.provosthomedesign.com"
MAIN_SITE_URL = "https://www.provosthomedesign.com"
CONTACT_EMAIL = "mike@provosthomedesign.com"
CONTACT_PHONE = "508-243-7912"
GA_MEASUREMENT_ID = ""
WORK_QUEUE_ENABLED = False
WORK_INTAKE_ENABLED = False
BOOKING_ENABLED = False
