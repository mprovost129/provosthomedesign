from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DeviceTokenAuthView,
    PublicPlanViewSet,
    PlanEmbedWidgetView,
)

router = DefaultRouter()
router.register(r"plans", PublicPlanViewSet, basename="public-plans")

urlpatterns = [
    path("auth/token/", DeviceTokenAuthView.as_view(), name="api_token_auth"),
    path("embed/widget.js", PlanEmbedWidgetView.as_view(), name="plan_embed_widget"),
    path("", include(router.urls)),
]
