from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.views import View
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from plans.models import Plans
from .authentication import PartnerAPIKeyAuthentication, HasPartnerAPIKey
from .serializers import (
    UserSerializer,
    PlanSerializer,
)


class UpdatedAfterMixin:
    """Filter list responses by ?updated_after=ISO8601 when the model has an updated_at field."""

    def filter_updated_after(self, queryset):
        updated_after = self.request.query_params.get("updated_after")
        if not updated_after:
            return queryset
        model = queryset.model
        if not hasattr(model, "updated_at"):
            return queryset
        try:
            ts = timezone.datetime.fromisoformat(updated_after)
            if ts.tzinfo is None:
                ts = timezone.make_aware(ts, timezone=timezone.utc)
            return queryset.filter(updated_at__gte=ts)
        except Exception:
            return queryset

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)
        return self.filter_updated_after(qs)


class DeviceTokenAuthView(ObtainAuthToken):
    """Simple token login that issues a device token per user. Accepts email or username."""

    def post(self, request, *args, **kwargs):
        username = request.data.get("username")
        password = request.data.get("password")
        
        # Try email-based login if username looks like an email
        user = None
        if username and "@" in username:
            try:
                user_obj = User.objects.get(email=username)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                pass
        
        # Fall back to standard username login
        if not user:
            user = authenticate(request, username=username, password=password)
        
        if not user:
            return Response(
                {"non_field_errors": ["Unable to log in with provided credentials."]},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        token, _ = Token.objects.get_or_create(user=user)
        return Response({
            "token": token.key,
            "user": UserSerializer(user).data,
        })

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def close(self, request, pk=None):
        project = self.get_object()
        summary = project.get_payment_summary() if hasattr(project, "get_payment_summary") else None
        if summary and not summary.get("is_fully_paid", False):
            return Response({"detail": "Project cannot be closed: outstanding balance remains."}, status=status.HTTP_400_BAD_REQUEST)
        project.is_closed = True
        project.closed_date = timezone.now()
        project.closed_by = request.user
        if getattr(project, "status", None) == "in_progress":
            project.status = "completed"
        project.save(update_fields=["is_closed", "closed_date", "closed_by", "status", "updated_at"])
        return Response(ProjectSerializer(project).data)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def reopen(self, request, pk=None):
        project = self.get_object()
        project.is_closed = False
        project.closed_date = None
        project.closed_by = None
        if getattr(project, "status", None) == "completed":
            project.status = "in_progress"
        project.save(update_fields=["is_closed", "closed_date", "closed_by", "status", "updated_at"])
        return Response(ProjectSerializer(project).data)


# ---------------------------------------------------------------------------
# Public Plans API (partner embed)
# ---------------------------------------------------------------------------

class PublicPlanViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only plan catalog for partner embeds. Requires a partner API key.

    Lookup is by plan_number (e.g. GET /api/plans/ABC123/).

    Supported query filters on the list endpoint:
      ?bedrooms=3
      ?min_sqft=1500&max_sqft=3000
      ?style=ranch          (house style slug)
      ?featured=true
      ?plan_number=ABC123   (alternative to detail URL)
    """
    authentication_classes = [PartnerAPIKeyAuthentication]
    permission_classes = [HasPartnerAPIKey]
    serializer_class = PlanSerializer
    lookup_field = "plan_number"

    def get_queryset(self):
        qs = Plans.objects.available().prefetch_related("images", "house_styles")
        p = self.request.query_params

        if bedrooms := p.get("bedrooms"):
            try:
                qs = qs.filter(bedrooms=int(bedrooms))
            except ValueError:
                pass
        if min_sqft := p.get("min_sqft"):
            try:
                qs = qs.filter(square_footage__gte=int(min_sqft))
            except ValueError:
                pass
        if max_sqft := p.get("max_sqft"):
            try:
                qs = qs.filter(square_footage__lte=int(max_sqft))
            except ValueError:
                pass
        if style := p.get("style"):
            qs = qs.filter(house_styles__slug=style)
        if featured := p.get("featured"):
            if featured.lower() in ("true", "1", "yes"):
                qs = qs.filter(is_featured=True)
        if plan_number := p.get("plan_number"):
            qs = qs.filter(plan_number=plan_number)

        return qs.distinct()


class PlanEmbedWidgetView(View):
    """Serve the embeddable JavaScript widget (no auth required - key is used client-side)."""

    def get(self, request, *args, **kwargs):
        api_base = request.build_absolute_uri("/api/")
        js = render_to_string("api/embed_widget.js", {"api_base": api_base})
        return HttpResponse(js, content_type="application/javascript; charset=utf-8")
