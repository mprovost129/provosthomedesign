from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from .models import PartnerAPIKey


class PartnerAPIKeyAuthentication(BaseAuthentication):
    """Authenticate via X-API-Key header or ?api_key= query param."""

    def authenticate(self, request):
        key = request.META.get("HTTP_X_API_KEY") or request.query_params.get("api_key")
        if not key:
            return None

        try:
            partner_key = PartnerAPIKey.objects.get(key=key, is_active=True)
        except PartnerAPIKey.DoesNotExist:
            raise AuthenticationFailed("Invalid or inactive API key.")

        origin = request.META.get("HTTP_ORIGIN", "")
        if origin and not partner_key.is_origin_allowed(origin):
            raise AuthenticationFailed("Origin not allowed for this API key.")

        # Throttle last_used_at updates to at most once per minute to avoid write storms
        now = timezone.now()
        if not partner_key.last_used_at or (now - partner_key.last_used_at).total_seconds() > 60:
            PartnerAPIKey.objects.filter(pk=partner_key.pk).update(last_used_at=now)

        return (None, partner_key)

    def authenticate_header(self, request):
        return "X-API-Key"


class HasPartnerAPIKey(BasePermission):
    """Allow requests authenticated with a valid PartnerAPIKey."""

    def has_permission(self, request, view):
        return isinstance(request.auth, PartnerAPIKey)
