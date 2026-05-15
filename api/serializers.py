from django.contrib.auth.models import User
from rest_framework import serializers

from billing.models import Client, Project, Invoice, Payment, Expense, ExpenseCategory, SystemSettings, ClientPlanFile, IncomingWorkLog
from plans.models import Plans, PlanGallery, HouseStyle
from timetracking.models import TimeEntry


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email"]


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = "__all__"


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = "__all__"


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = "__all__"
        read_only_fields = fields


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = "__all__"


class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = "__all__"


class TimeEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeEntry
        fields = "__all__"
        read_only_fields = ["user", "created_at", "updated_at", "duration"]


class SystemSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemSettings
        fields = "__all__"


class ClientPlanFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientPlanFile
        fields = "__all__"
        read_only_fields = ["uploaded_by", "uploaded_at"]


class IncomingWorkLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomingWorkLog
        fields = "__all__"


# ---------------------------------------------------------------------------
# Public Plans API (partner embed)
# ---------------------------------------------------------------------------

class HouseStyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = HouseStyle
        fields = ["id", "style_name", "slug"]


class PlanGallerySerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = PlanGallery
        fields = ["id", "kind", "caption", "order", "image_url"]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class PlanSerializer(serializers.ModelSerializer):
    house_styles = HouseStyleSerializer(many=True, read_only=True)
    gallery = PlanGallerySerializer(many=True, read_only=True, source="images")
    main_image_url = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    house_width_display = serializers.CharField(read_only=True)
    house_depth_display = serializers.CharField(read_only=True)

    class Meta:
        model = Plans
        fields = [
            "plan_number",
            "slug",
            "square_footage",
            "bedrooms",
            "bathrooms",
            "stories",
            "garage_stalls",
            "house_width_in",
            "house_depth_in",
            "house_width_display",
            "house_depth_display",
            "description",
            "plan_price",
            "main_image_url",
            "gallery",
            "house_styles",
            "is_featured",
            "url",
        ]

    def get_main_image_url(self, obj):
        request = self.context.get("request")
        if obj.main_image and request:
            return request.build_absolute_uri(obj.main_image.url)
        return None

    def get_url(self, obj):
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.get_absolute_url())
        return None
