from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.files.storage import default_storage


class Command(BaseCommand):
    help = "Print the active media storage configuration and sample plan image URLs."

    def handle(self, *args, **options):
        self.stdout.write(f"DEBUG={settings.DEBUG}")
        self.stdout.write(f"USE_S3_MEDIA={getattr(settings, 'USE_S3_MEDIA', None)}")
        self.stdout.write(f"MEDIA_URL={settings.MEDIA_URL}")
        self.stdout.write(f"DEFAULT_STORAGE={default_storage.__class__.__module__}.{default_storage.__class__.__name__}")
        self.stdout.write(f"AWS_STORAGE_BUCKET_NAME={getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')}")
        self.stdout.write(f"AWS_S3_REGION_NAME={getattr(settings, 'AWS_S3_REGION_NAME', '')}")
        self.stdout.write(f"AWS_S3_CUSTOM_DOMAIN={getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', '')}")

        try:
            from plans.models import Plans, PlanGallery
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Could not import plan models: {exc}"))
            return

        plan = Plans.objects.exclude(main_image="").first()
        if plan and plan.main_image:
            self.stdout.write(f"sample_plan_main_image_name={plan.main_image.name}")
            self.stdout.write(f"sample_plan_main_image_url={plan.main_image.url}")
        else:
            self.stdout.write("sample_plan_main_image=None")

        gallery = PlanGallery.objects.exclude(image="").first()
        if gallery and gallery.image:
            self.stdout.write(f"sample_gallery_image_name={gallery.image.name}")
            self.stdout.write(f"sample_gallery_image_url={gallery.image.url}")
        else:
            self.stdout.write("sample_gallery_image=None")
