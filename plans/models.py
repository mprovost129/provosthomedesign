from __future__ import annotations

from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils import timezone as dj_timezone  # avoid name shadowing


# -----------------------------
# HouseStyle
# -----------------------------
class HouseStyle(models.Model):
    style_name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    style_image = models.ImageField(upload_to="plans/styles", blank=True, null=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "style_name"]
        verbose_name = "house style"
        verbose_name_plural = "house styles"

    def __str__(self) -> str:
        return self.style_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.style_name)
        super().save(*args, **kwargs)


# -----------------------------
# Plans
# -----------------------------
class PlansQuerySet(models.QuerySet):
    def available(self):
        return self.filter(is_available=True)

    def featured(self):
        return self.filter(is_available=True, is_featured=True)


class Plans(models.Model):
    # identifiers
    plan_number = models.CharField(max_length=50, unique=True)
    plan_name = models.CharField(max_length=120, blank=True, help_text="Descriptive public name, such as 'The Rehoboth Ranch'")
    slug = models.SlugField(max_length=80, unique=True)

    # specs
    square_footage = models.PositiveIntegerField(help_text="Total heated sq ft")
    bedrooms = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)])
    bathrooms = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        help_text="e.g. 2.5",
        validators=[MinValueValidator(Decimal("0.0"))],
    )
    stories = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)])
    garage_stalls = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)])

    # dimensions stored in inches
    house_width_in = models.PositiveIntegerField(help_text="Overall width in inches")
    house_depth_in = models.PositiveIntegerField(help_text="Overall depth in inches")

    description = models.TextField(blank=True)
    ideal_for = models.TextField(blank=True, help_text="Who this plan suits, including lifestyle, lot, or buyer needs")
    key_features = models.TextField(blank=True, help_text="One feature per line")
    layout_highlights = models.TextField(blank=True, help_text="Room layout, circulation, and daily-living highlights")
    foundation_framing = models.TextField(blank=True, help_text="Foundation options and framing assumptions")
    exterior_character = models.TextField(blank=True, help_text="Roof form, materials, and architectural character")
    package_contents = models.TextField(blank=True, help_text="One included plan-package item per line")
    delivery_details = models.TextField(blank=True, help_text="File formats, delivery method, and typical timing")
    common_modifications = models.TextField(blank=True, help_text="One common modification per line")

    # optional meta/marketing fields referenced by templates
    sku = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    meta_description = models.CharField(max_length=180, blank=True)

    # public price only shown on detail page
    plan_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    # cover image (front perspective)
    main_image = models.ImageField(upload_to="plans/main", blank=True, null=True)

    house_styles = models.ManyToManyField(HouseStyle, related_name="plans", blank=True)

    # Searchable design attributes used by the catalog and curated categories.
    is_adu = models.BooleanField(default=False, verbose_name="ADU or carriage house")
    first_floor_primary = models.BooleanField(default=False)
    has_home_office = models.BooleanField(default=False)
    has_walk_in_pantry = models.BooleanField(default=False)
    has_mudroom = models.BooleanField(default=False)
    has_porch_or_deck = models.BooleanField(default=False)
    has_bonus_room = models.BooleanField(default=False)
    basement_compatible = models.BooleanField(default=False)
    narrow_lot = models.BooleanField(default=False)
    multigenerational = models.BooleanField(default=False)

    is_available = models.BooleanField(default=True)
    # Hand-pick which plans to feature on the home page, etc.
    is_featured = models.BooleanField(default=False, db_index=True)

    created_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)

    # custom manager
    objects = PlansQuerySet.as_manager()

    class Meta:
        ordering = ["-created_date"]
        verbose_name = "plan"
        verbose_name_plural = "plans"
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["plan_number"]),
            models.Index(fields=["is_available", "created_date"]),
            models.Index(fields=["is_featured", "created_date"]),
        ]

    def __str__(self) -> str:
        return self.plan_number

    # keep slug stable & predictable
    def save(self, *args, **kwargs):
        if not self.slug:
            # Using plan_number ensures stable, unique slugs since plan_number is unique
            self.slug = slugify(self.plan_number)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        """
        Generate URL using the first house style, or 'general' if no styles assigned.
        """
        first_style = self.house_styles.first()
        style_slug = first_style.slug if first_style else "general"
        return reverse("plans:plan_detail", args=[style_slug, self.slug])

    # ---- Validation ----
    def clean(self):
        """
        Keep bathroom counts to .0 or .5 (common for full/half baths)
        without being overly strict.
        """
        super().clean()
        if self.bathrooms is None:
            return
        try:
            # Multiply by 2 and ensure it's an integer (i.e., steps of 0.5)
            doubled = self.bathrooms * Decimal("2")
        except (InvalidOperation, TypeError):
            raise ValidationError({"bathrooms": "Enter a valid bathroom count (e.g., 2, 2.5)."})
        if doubled != doubled.to_integral_value():
            raise ValidationError({"bathrooms": "Bathrooms must be in 0.5 increments (e.g., 2, 2.5, 3)."})

    # ---- Convenience display helpers ----
    @property
    def main_image_url(self) -> str | None:
        try:
            return self.main_image.url if self.main_image else None
        except Exception:
            return None

    @staticmethod
    def _inches_to_feet_inches(total_in: int) -> tuple[int, int]:
        feet = total_in // 12
        inches = total_in % 12
        return feet, inches

    @property
    def house_width_display(self) -> str:
        ft, inch = self._inches_to_feet_inches(int(self.house_width_in or 0))
        return f'{ft}′ {inch}″'

    @property
    def house_depth_display(self) -> str:
        ft, inch = self._inches_to_feet_inches(int(self.house_depth_in or 0))
        return f'{ft}′ {inch}″'

    @staticmethod
    def _nonempty_lines(value: str) -> list[str]:
        return [line.strip() for line in (value or "").splitlines() if line.strip()]

    @property
    def key_features_list(self) -> list[str]:
        return self._nonempty_lines(self.key_features)

    @property
    def package_contents_list(self) -> list[str]:
        return self._nonempty_lines(self.package_contents)

    @property
    def common_modifications_list(self) -> list[str]:
        return self._nonempty_lines(self.common_modifications)


class PlanFAQ(models.Model):
    plan = models.ForeignKey(Plans, on_delete=models.CASCADE, related_name="faqs")
    question = models.CharField(max_length=240)
    answer = models.TextField()
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "id")
        verbose_name = "plan FAQ"
        verbose_name_plural = "plan FAQs"

    def __str__(self) -> str:
        return f"{self.plan.plan_number}: {self.question}"


# -----------------------------
# Plan Gallery
# -----------------------------
IMAGE_KIND_CHOICES = [
    ("front", "Front Perspective"),
    ("floor", "Floor Plan"),
    ("elevation", "Elevation"),
    ("other", "Other"),
]


class PlanGallery(models.Model):
    plan = models.ForeignKey(Plans, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="plans/gallery")
    kind = models.CharField(max_length=20, choices=IMAGE_KIND_CHOICES, default="other")
    caption = models.CharField(max_length=120, blank=True)
    order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(default=dj_timezone.now, editable=False, db_index=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "plan gallery image"
        verbose_name_plural = "plan gallery images"

    def __str__(self) -> str:
        return f"{self.plan.plan_number} image #{self.pk}"


# -----------------------------
# Saved Plans (Favorites/Wishlist)
# -----------------------------
class SavedPlan(models.Model):
    """Track user's favorite/saved plans via session"""
    session_key = models.CharField(max_length=40, db_index=True)
    plan = models.ForeignKey(Plans, on_delete=models.CASCADE, related_name="saved_by")
    saved_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-saved_at"]
        unique_together = [["session_key", "plan"]]
        verbose_name = "saved plan"
        verbose_name_plural = "saved plans"
        indexes = [
            models.Index(fields=["session_key", "-saved_at"]),
        ]
    
    def __str__(self) -> str:
        return f"{self.session_key[:8]}... saved {self.plan.plan_number}"


# -----------------------------
# Plan Comparison
# -----------------------------
class PlanComparison(models.Model):
    """Track plans user wants to compare"""
    session_key = models.CharField(max_length=40, db_index=True)
    plans = models.ManyToManyField(Plans, related_name="in_comparisons")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "plan comparison"
        verbose_name_plural = "plan comparisons"
        indexes = [
            models.Index(fields=["session_key", "-updated_at"]),
        ]
    
    def __str__(self) -> str:
        count = self.plans.count()
        return f"{self.session_key[:8]}... comparing {count} plan(s)"
