import hashlib
import secrets
import unicodedata
from datetime import timedelta

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import BookingAccess, EmailAccessToken, ProjectAccess, ProjectClaim, WorkItem, WorkProject


SESSION_SALT = "workqueue.intake.session.v1"


def normalize_email(email):
    return email.strip().lower()


def normalized_context(text):
    # Keep lot/unit digits and punctuation; no fuzzy linking or guessed address parsing.
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def session_digest(request):
    if not request.session.session_key:
        request.session.create()
    if not request.session.get("queue_intake_binding"):
        request.session["queue_intake_binding"] = secrets.token_hex(32)
    return hashlib.sha256(request.session["queue_intake_binding"].encode()).hexdigest()


def new_intake_token(request):
    import uuid
    return signing.dumps({"nonce": str(uuid.uuid4()), "session": session_digest(request)}, salt=SESSION_SALT)


def read_intake_token(request, value):
    import uuid
    try:
        data = signing.loads(value, salt=SESSION_SALT, max_age=86400)
        if not secrets.compare_digest(data["session"], session_digest(request)):
            raise ValueError
        return uuid.UUID(data["nonce"])
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise ValidationError("This form session expired. Reload the form and try again.") from None


def verified_email(request):
    until = request.session.get("queue_email_verified_until", 0)
    if until <= timezone.now().timestamp():
        return ""
    return request.session.get("queue_verified_email", "")


def authorized_projects(email):
    if not email:
        return WorkProject.objects.none()
    return WorkProject.objects.filter(access_grants__email=normalize_email(email), access_grants__revoked_at__isnull=True)


def authorized_work(email):
    if not email:
        return WorkItem.objects.none()
    email = normalize_email(email)
    revoked = WorkProject.objects.filter(access_grants__email=email, access_grants__revoked_at__isnull=False)
    return WorkItem.objects.filter(Q(submissions__owner_email=email) | Q(project__in=authorized_projects(email))).exclude(project__in=revoked).distinct()


def resolve_project(email, *, reference="", context="", project_id=""):
    """Only known, authorized records can be auto-connected; no global name search."""
    if not email:
        return None, None
    projects = authorized_projects(email)
    if project_id:
        return projects.filter(pk=project_id).first(), None
    if reference:
        previous = authorized_work(email).select_related("project").filter(reference=reference.strip().upper()).first()
        if previous and previous.project_id and projects.filter(pk=previous.project_id).exists():
            return previous.project, previous
        found = projects.filter(canonical_reference=reference.strip().upper()).first()
        if found:
            return found, None
    if context:
        normalized = normalized_context(context)
        matches = []
        for project in projects:
            address = " ".join(value for value in [project.street, project.city, project.state, project.zip_code] if value)
            if normalized in {normalized_context(project.name), normalized_context(address)}:
                matches.append(project)
        if len(matches) == 1:
            return matches[0], None
    return None, None


def mint_email_token(email, reference="", destination="tracking"):
    raw = secrets.token_urlsafe(32)
    record = EmailAccessToken.objects.create(email=normalize_email(email), reference=reference, destination=destination,
        digest=hashlib.sha256(raw.encode()).hexdigest(), expires_at=timezone.now() + timedelta(hours=24))
    return record, raw


@transaction.atomic
def consume_email_token(raw):
    if not raw or len(raw) > 200:
        raise ValidationError("That sign-in link is invalid or expired. Request a new link.")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    record = EmailAccessToken.objects.select_for_update().filter(digest=digest, used_at__isnull=True,
                                                               expires_at__gt=timezone.now()).first()
    if not record:
        raise ValidationError("That sign-in link is invalid or expired. Request a new link.")
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])
    # Only the original new-project claim can establish automatic project access.
    for claim in ProjectClaim.objects.select_related("submission__work_item").filter(email=record.email, verified_at__isnull=True):
        if claim.submission.work_item.project_id != claim.project_id:
            continue
        ProjectAccess.objects.get_or_create(project_id=claim.project_id, email=record.email)
        # Explicitly revoked grants remain revoked when an old claim is verified again.
        claim.verified_at = timezone.now()
        claim.save(update_fields=["verified_at"])
        BookingAccess.objects.get_or_create(email=record.email)
    # Existing imported/manual clients can book after proving their recorded
    # email. Do not grant project-wide access or undo an explicit booking ban.
    if authorized_work(record.email).exists():
        BookingAccess.objects.get_or_create(email=record.email)
    return record


def rate_allowed(scope, identity, limit, window):
    # Distributed cache in production; no client-controlled proxy header is trusted.
    bucket = int(timezone.now().timestamp()) // window
    digest = hashlib.sha256(identity.encode()).hexdigest()
    key = f"queue-rate:{scope}:{bucket}:{digest}"
    if cache.add(key, 1, timeout=window + 5):
        return True
    try:
        return cache.incr(key) <= limit
    except ValueError:
        return False


def reconcile_verified_updates(email):
    """A receipt verification can connect earlier queued updates to authorized projects."""
    from .services import edit_work
    candidates = WorkItem.objects.filter(kind="update", project__isnull=True,
                                         submissions__owner_email=normalize_email(email)).distinct()
    for item in candidates:
        answers = item.submissions.order_by("received_at").first().answers
        reference = answers.get("Project ID, if you have it", "")
        context = answers.get("Project name or full address", item.project_context)
        project, _ = resolve_project(email, reference=reference, context=context)
        if project:
            edit_work(item_id=item.pk, version=item.version, data={"project": project}, actor=None)
