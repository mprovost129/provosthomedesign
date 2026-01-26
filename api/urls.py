from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DeviceTokenAuthView,
    ClientViewSet,
    ProjectViewSet,
    InvoiceViewSet,
    PaymentViewSet,
    ExpenseCategoryViewSet,
    ExpenseViewSet,
    TimeEntryViewSet,
    SystemSettingsViewSet,
    ClientPlanFileViewSet,
)

router = DefaultRouter()
router.register(r"clients", ClientViewSet)
router.register(r"projects", ProjectViewSet)
router.register(r"invoices", InvoiceViewSet)
router.register(r"payments", PaymentViewSet)
router.register(r"expense-categories", ExpenseCategoryViewSet)
router.register(r"expenses", ExpenseViewSet)
router.register(r"time-entries", TimeEntryViewSet)
router.register(r"system-settings", SystemSettingsViewSet)
router.register(r"plan-files", ClientPlanFileViewSet)

urlpatterns = [
    path("auth/token/", DeviceTokenAuthView.as_view(), name="api_token_auth"),
    path("", include(router.urls)),
]
