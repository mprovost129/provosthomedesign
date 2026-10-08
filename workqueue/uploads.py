"""Private, staged uploads; neither uploads nor external links are executed."""
import re
import uuid
import warnings
import zipfile
import shutil
from tempfile import SpooledTemporaryFile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.storage import storages
from django.db import transaction
from django.utils import timezone
from PIL import Image

from .models import PendingUpload


MAX_FILES = 10
MAX_BYTES = 100 * 1024 * 1024
TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".webp": "image/webp",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def private_storage():
    if "intake_private" not in settings.STORAGES:
        raise ImproperlyConfigured("Private intake storage is not configured.")
    return storages["intake_private"]


def checked_name(name, size):
    if (not name or len(name) > 255 or any(char in name for char in ['/', '\\', ':', '\x00'])
            or any(ord(char) < 32 for char in name)):
        raise ValidationError("Use a simple filename without folder paths or special control characters.")
    extension = Path(name).suffix.lower()
    if extension not in TYPES:
        raise ValidationError("Upload PDF, JPG, PNG, WebP, DOCX or XLSX files. You can provide a link for another format.")
    if size <= 0 or size > MAX_BYTES:
        raise ValidationError("Each file must be nonempty and no larger than 100 MB.")
    return extension, TYPES[extension]


def validate_content(file, name):
    """Structural/type checks, not an antivirus claim. Always restore the stream."""
    extension = Path(name).suffix.lower()
    try:
        file.seek(0)
        if extension == ".pdf":
            if not file.read(8).startswith(b"%PDF-"):
                raise ValidationError("This file does not appear to be a PDF.")
            file.seek(0, 2)
            end = file.tell()
            file.seek(max(0, end - 2048))
            if b"%%EOF" not in file.read(2048):
                raise ValidationError("This PDF appears incomplete. Save it again and retry.")
        elif extension in {".jpg", ".jpeg", ".png", ".webp"}:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(file)
                expected = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[extension]
                if image.format != expected:
                    raise ValidationError("The picture format does not match its filename.")
                image.verify()
        else:
            with zipfile.ZipFile(file) as package:
                entries = package.infolist()
                names = {entry.filename for entry in entries}
                marker = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
                if "[Content_Types].xml" not in names or marker not in names:
                    raise ValidationError("The document format does not match its filename.")
                if (len(entries) > 5000 or sum(entry.file_size for entry in entries) > 500 * 1024 * 1024
                        or any("vbaproject" in entry.filename.casefold() or entry.flag_bits & 1 for entry in entries)):
                    raise ValidationError("This document contains unsupported protected or active content.")
    except ValidationError:
        raise
    except (OSError, ValueError, SyntaxError, zipfile.BadZipFile, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError("This file could not be read. Check it and try uploading again.") from None
    finally:
        file.seek(0)


@transaction.atomic
def reserve_upload(*, nonce, binding, name, size, upload_id=None):
    from .services import lock_queue
    lock_queue()
    extension, content_type = checked_name(name, size)
    if upload_id:
        existing = PendingUpload.objects.filter(pk=upload_id).first()
        if existing:
            if (existing.intake_nonce != nonce or existing.session_digest != binding
                    or existing.original_name != name or existing.size_bytes != size
                    or existing.state not in {"reserved", "ready"} or existing.expires_at <= timezone.now()):
                raise ValidationError("Start this file upload again.")
            return existing
    # Serialize draft reservations in PostgreSQL so parallel uploads cannot exceed the cap.
    count = PendingUpload.objects.filter(intake_nonce=nonce, session_digest=binding,
                                        state__in=[PendingUpload.State.RESERVED, PendingUpload.State.READY]).count()
    if count >= MAX_FILES:
        raise ValidationError("You can upload no more than 10 files per submission.")
    upload_id = upload_id or uuid.uuid4()
    return PendingUpload.objects.create(id=upload_id, intake_nonce=nonce, session_digest=binding,
        original_name=name, size_bytes=size, content_type=content_type,
        storage_key=f"staged/{upload_id.hex}{extension}", upload_key=f"staged/{upload_id.hex}{extension}",
        sealed_key=f"ready/{uuid.uuid4().hex}{extension}",
        expires_at=timezone.now() + timedelta(hours=24))


def direct_upload_policy(upload):
    storage = private_storage()
    # Fail closed if the configured production bucket can expose uploaded documents.
    if storage.bucket_name == getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""):
        raise ImproperlyConfigured("Private intake documents need a separate private bucket.")
    client = storage.connection.meta.client
    flags = client.get_public_access_block(Bucket=storage.bucket_name)["PublicAccessBlockConfiguration"]
    required = {"BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"}
    if not all(flags.get(flag, False) for flag in required):
        raise ImproperlyConfigured("All S3 Block Public Access protections must be enabled.")
    key = storage._normalize_name(upload.upload_key)
    return client.generate_presigned_post(
        Bucket=storage.bucket_name, Key=key,
        Fields={"Content-Type": upload.content_type},
        Conditions=[{"Content-Type": upload.content_type}, ["content-length-range", upload.size_bytes, upload.size_bytes]],
        ExpiresIn=900,
    )


def finish_upload(upload, *, local_file=None):
    storage = private_storage()
    # A validated file is sealed under a fresh key that the upload credential cannot overwrite.
    ready_key = upload.sealed_key
    if not ready_key:
        raise ValidationError("Please restart this upload.")
    if local_file is not None:
        if local_file.size != upload.size_bytes:
            raise ValidationError("The uploaded file size changed. Please retry.")
        validate_content(local_file, upload.original_name)
        saved_key = storage.save(ready_key, local_file)
        if saved_key != ready_key:
            storage.delete(saved_key)
            raise ValidationError("Please start this upload again.")
    else:
        client = storage.connection.meta.client
        source_key = storage._normalize_name(upload.upload_key)
        head = client.head_object(Bucket=storage.bucket_name, Key=source_key)
        if head["ContentLength"] != upload.size_bytes:
            raise ValidationError("The upload is incomplete. Please retry.")
        response = client.get_object(Bucket=storage.bucket_name, Key=source_key, IfMatch=head["ETag"])
        with response["Body"] as body, SpooledTemporaryFile(max_size=2 * 1024 * 1024) as file:
            shutil.copyfileobj(body, file, length=1024 * 1024)
            if file.tell() != upload.size_bytes:
                raise ValidationError("The upload is incomplete. Please retry.")
            validate_content(file, upload.original_name)
        client.copy_object(Bucket=storage.bucket_name, Key=storage._normalize_name(ready_key),
            CopySource={"Bucket": storage.bucket_name, "Key": source_key}, CopySourceIfMatch=head["ETag"])
    upload.storage_key = ready_key
    upload.state = PendingUpload.State.READY
    upload.save(update_fields=["state", "storage_key"])
    return upload
