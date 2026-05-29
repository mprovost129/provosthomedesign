from django.contrib.auth.models import User
from rest_framework import serializers
from plans.models import Plans, PlanGallery, HouseStyle



class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email"]


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
