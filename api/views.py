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

from billing.models import Client, Project, Invoice, Payment, Expense, ExpenseCategory, SystemSettings, ClientPlanFile, IncomingWorkLog
from plans.models import Plans
from timetracking.models import TimeEntry
from .authentication import PartnerAPIKeyAuthentication, HasPartnerAPIKey
from .serializers import (
    UserSerializer,
    ClientSerializer,
    ProjectSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    ExpenseSerializer,
    ExpenseCategorySerializer,
    TimeEntrySerializer,
    SystemSettingsSerializer,
    ClientPlanFileSerializer,
    IncomingWorkLogSerializer,
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


class ClientViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all().select_related("user")
    serializer_class = ClientSerializer


class ProjectViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = Project.objects.all().select_related("client", "created_by", "closed_by")
    serializer_class = ProjectSerializer

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


class InvoiceViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = Invoice.objects.all().select_related("client", "project")
    serializer_class = InvoiceSerializer


class PaymentViewSet(UpdatedAfterMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.all().select_related("invoice", "invoice__client")
    serializer_class = PaymentSerializer


class ExpenseCategoryViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer


class ExpenseViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = Expense.objects.all().select_related("project", "client", "category", "approved_by", "created_by")
    serializer_class = ExpenseSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def approve(self, request, pk=None):
        expense = self.get_object()
        expense.status = "approved"
        expense.approved_by = request.user
        expense.approved_date = timezone.now()
        expense.save(update_fields=["status", "approved_by", "approved_date"])
        return Response(ExpenseSerializer(expense).data)


class TimeEntryViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = TimeEntry.objects.all().select_related("project", "user", "invoice")
    serializer_class = TimeEntrySerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class SystemSettingsViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SystemSettings.objects.all()
    serializer_class = SystemSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]


class ClientPlanFileViewSet(UpdatedAfterMixin, viewsets.ModelViewSet):
    queryset = ClientPlanFile.objects.all().select_related("client", "project", "uploaded_by")
    serializer_class = ClientPlanFileSerializer

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)


class IncomingWorkLogViewSet(viewsets.ModelViewSet):
    queryset = IncomingWorkLog.objects.all().order_by('-created_at')
    serializer_class = IncomingWorkLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


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
    """Serve the embeddable JavaScript widget (no auth required — key is used client-side)."""

    def get(self, request, *args, **kwargs):
        api_base = request.build_absolute_uri("/api/")
        js = render_to_string("api/embed_widget.js", {"api_base": api_base})
        return HttpResponse(js, content_type="application/javascript; charset=utf-8")
