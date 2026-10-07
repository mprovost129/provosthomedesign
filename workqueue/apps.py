from django.apps import AppConfig


class WorkqueueConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workqueue"
    verbose_name = "Work queue"

    def ready(self):
        from . import checks  # noqa: F401
