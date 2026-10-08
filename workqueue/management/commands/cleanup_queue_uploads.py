from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from workqueue.models import IntakeDraft, PendingUpload
from workqueue.uploads import private_storage


class Command(BaseCommand):
    help = "Clean expired unsubmitted files and stale direct-upload staging copies."

    def handle(self, *args, **options):
        IntakeDraft.objects.filter(expires_at__lte=timezone.now()).delete()
        if "intake_private" not in settings.STORAGES:
            raise CommandError("Private intake storage is not configured.")
        storage = private_storage()
        cleaned = 0
        # A retry may issue a new 15-minute upload policy near draft expiry.
        # Wait beyond that last possible permission; early deletion could leave a
        # staging object recreated later by a still-valid upload credential.
        cutoff = timezone.now()-timedelta(minutes=20)
        candidates = PendingUpload.objects.filter(expires_at__lt=cutoff).exclude(
            upload_key="", state="attached").exclude(upload_key="", sealed_key="", state="failed")
        for upload_id in candidates.values_list("pk", flat=True):
            with transaction.atomic():
                upload = PendingUpload.objects.select_for_update().get(pk=upload_id)
                # Presigned permissions have expired by now. Never delete a submitted attachment.
                if upload.state in ("ready", "attached") or upload.expires_at < timezone.now() or upload.state == "failed":
                    if upload.upload_key and upload.upload_key != upload.storage_key:
                        storage.delete(upload.upload_key)
                        upload.upload_key = ""
                        upload.save(update_fields=["upload_key"])
                if upload.state != "attached" and (upload.expires_at < timezone.now() or upload.state == "failed"):
                    storage.delete(upload.storage_key)
                    if upload.sealed_key and upload.sealed_key != upload.storage_key:
                        storage.delete(upload.sealed_key)
                    upload.state = "failed"
                    upload.sealed_key = ""
                    upload.save(update_fields=["state", "sealed_key"])
                    cleaned += 1
        self.stdout.write(f"Cleaned {cleaned} unsubmitted file records; submitted files were retained.")
