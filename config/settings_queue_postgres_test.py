"""PostgreSQL validation restricted to an explicitly selected local test database.

Set QUEUE_TEST_DATABASE_URL to a local PostgreSQL URL with a database name
starting phd_queue_test. DATABASE_URL and .env are never used here.
"""
import os
import dj_database_url
from .settings_queue_dev import *  # noqa: F403

test_url = os.environ.get("QUEUE_TEST_DATABASE_URL", "")
if not test_url:
    raise RuntimeError("QUEUE_TEST_DATABASE_URL must identify a dedicated local test database.")
test_database = dj_database_url.parse(test_url, conn_max_age=0)
if (test_database["ENGINE"] != "django.db.backends.postgresql"
        or test_database.get("HOST") not in {"localhost", "127.0.0.1", "::1"}
        or not test_database.get("NAME", "").startswith("phd_queue_test")):
    raise RuntimeError("Queue PostgreSQL tests require localhost and a phd_queue_test database name.")
DATABASES = {"default": test_database}
