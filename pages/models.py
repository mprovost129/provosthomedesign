from __future__ import annotations

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.templatetags.static import static
from plans.models import HouseStyle  # dynamic link to plans app
from django.db.models import Case, When, Value, IntegerField
from django.urls import reverse
from django.utils.text import slugify


CONTACT_METHOD_CHOICES = [
    ("email", "Email"),
    ("phone", "Phone"),
    ("text", "Text"),
]

PROJECT_TYPE_CHOICES = [
    ("new-home", "New custom home"),
    ("stock-plan", "Stock plan purchase"),
    ("addition", "Addition or renovation"),
    ("plan-modification", "Stock plan modification"),
    ("framing", "Framing plans"),
    ("adu", "ADU or accessory building"),
    ("not-sure", "Not sure yet"),
]

PROJECT_SIZE_CHOICES = [
    ("under-1000", "Under 1,000 sq ft"),
    ("1000-1999", "1,000-1,999 sq ft"),
    ("2000-2999", "2,000-2,999 sq ft"),
    ("3000-plus", "3,000+ sq ft"),
    ("not-sure", "Not sure yet"),
]

PROJECT_TIMELINE_CHOICES = [
    ("asap", "As soon as practical"),
    ("3-6-months", "Within 3-6 months"),
    ("6-12-months", "Within 6-12 months"),
    ("12-plus-months", "More than 12 months"),
    ("researching", "Researching options"),
]

BUDGET_RANGE_CHOICES = [
    ("under-250k", "Under $250,000"),
    ("250k-500k", "$250,000-$500,000"),
    ("500k-750k", "$500,000-$750,000"),
    ("750k-plus", "$750,000+"),
    ("not-sure", "Not sure yet"),
]

FOUNDATION_CHOICES = [
    ("crawl_space", "Crawl Space"),
    ("8_ft", "8 ft"),
    ("9_ft", "9 ft"),
    ("10_ft", "10 ft"),
    ("other", "Other"),
]

FLOOR_HEIGHT_CHOICES = [
    ("8_ft", "8 ft"),
    ("9_ft", "9 ft"),
    ("10_ft", "10 ft"),
    ("other", "Other"),
]

CEILING_CHOICES = [
    ("vaulted", "Vaulted"),
    ("tray", "Tray"),
    ("stepped", "Stepped"),
    ("coffered", "Coffered"),
    ("other", "Other"),
]

STATUS_CHOICES = [
    ("new", "New"),
    ("reviewed", "Reviewed"),
    ("archived", "Archived"),
]

TESTIMONIAL_ROLE_CHOICES = [
    ("homeowner", "Homeowner"),
    ("builder", "Builder"),
    ("architect", "Architect"),
    ("engineer", "Engineer"),
    ("designer", "Designer"),
    ("other", "Other"),
]


class SiteSettings(models.Model):
    """
    Singleton-ish site settings row editable in admin.
    Use SiteSettings.load() to get/create the single row (pk=1).
    """
    company_name = models.CharField(max_length=160, blank=True)
    contact_name = models.CharField(max_length=160, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    contact_address = models.CharField(max_length=200, blank=True)

    # Legacy freeform hours (kept for now; structured hours live in BusinessHour)
    business_hours = models.TextField(blank=True, help_text="One entry per line, e.g. 'Mon–Fri: 9–5'")

    brand_logo = models.ImageField(upload_to="brand/", blank=True, null=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self) -> str:
        return self.company_name or "Site Settings"

    @classmethod
    def load(cls) -> "SiteSettings":  # sourcery skip: use-contextlib-suppress
        obj, _ = cls.objects.get_or_create(pk=1)
        # Auto-seed a full week of BusinessHour rows for nice inline editing
        try:
            existing = set(obj.hours.values_list("day", flat=True)) # type: ignore
            for code, _label in BusinessHour.DAYS:
                if code not in existing:
                    BusinessHour.objects.create(site=obj, day=code, is_closed=(code == "sun"))
        except Exception:
            # best effort; don't explode admin if seeding fails
            pass
        return obj

    @property
    def logo_url(self) -> str:  # sourcery skip: use-contextlib-suppress
        try:
            if self.brand_logo and self.brand_logo.url:
                return self.brand_logo.url
        except Exception:
            pass
        return static("images/phdlogo.svg")


class ProjectInquiry(models.Model):
    project_type = models.CharField(max_length=30, choices=PROJECT_TYPE_CHOICES, blank=True)
    project_location = models.CharField(max_length=120, blank=True)
    approximate_size = models.CharField(max_length=20, choices=PROJECT_SIZE_CHOICES, blank=True)
    project_timeline = models.CharField(max_length=20, choices=PROJECT_TIMELINE_CHOICES, blank=True)
    budget_range = models.CharField(max_length=20, choices=BUDGET_RANGE_CHOICES, blank=True)
    consultation_requested = models.BooleanField(default=False)

    # Contact
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField(db_index=True)
    alt_email = models.EmailField(blank=True)
    company = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20)
    alt_phone_number = models.CharField(max_length=20, blank=True)
    preferred_contact_method = models.CharField(
        max_length=10, choices=CONTACT_METHOD_CHOICES, default="email"
    )

    # Address (optional)
    street_address = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=50, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=10, blank=True)

    # Preferences
    house_style = models.ForeignKey(
        HouseStyle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_inquiries",
    )
    min_square_footage = models.PositiveIntegerField(null=True, blank=True)
    max_square_footage = models.PositiveIntegerField(null=True, blank=True)
    budget = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    number_of_floors = models.PositiveSmallIntegerField(null=True, blank=True)
    number_of_bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    number_of_bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    number_of_garage_spaces = models.PositiveSmallIntegerField(null=True, blank=True)

    # Land
    land_purchased = models.BooleanField(default=False)
    land_address = models.CharField(max_length=100, blank=True)
    land_city = models.CharField(max_length=50, blank=True)
    land_state = models.CharField(max_length=50, blank=True)
    land_zip_code = models.CharField(max_length=10, blank=True)
    land_size = models.CharField(max_length=50, blank=True)

    # Plans / structure
    pre_existing_plans = models.BooleanField(default=False)

    foundation_height = models.CharField(max_length=20, choices=FOUNDATION_CHOICES, blank=True)
    first_floor_height = models.CharField(max_length=20, choices=FLOOR_HEIGHT_CHOICES, blank=True)
    second_floor_height = models.CharField(max_length=20, choices=FLOOR_HEIGHT_CHOICES, blank=True)
    third_floor_height = models.CharField(max_length=20, choices=FLOOR_HEIGHT_CHOICES, blank=True)

    ceiling_feature_1 = models.CharField(max_length=20, choices=CEILING_CHOICES, blank=True)
    ceiling_feature_2 = models.CharField(max_length=20, choices=CEILING_CHOICES, blank=True)
    ceiling_feature_3 = models.CharField(max_length=20, choices=CEILING_CHOICES, blank=True)

    additional_notes = models.TextField(blank=True)

    # Meta
    terms_accepted = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="new", db_index=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} - {self.email} - {self.submitted_at:%Y-%m-%d}"

    def clean(self):
# sourcery skip: merge-nested-ifs
        if self.min_square_footage and self.max_square_footage:
            if self.min_square_footage > self.max_square_footage:
                from django.core.exceptions import ValidationError
                raise ValidationError({"max_square_footage": "Max square footage must be ≥ min square footage."})


def inquiry_upload_to(instance: "InquiryAttachment", filename: str) -> str:
    return f"inquiries/{instance.inquiry_id or 'pending'}/{filename}"  # type: ignore


class InquiryAttachment(models.Model):
    inquiry = models.ForeignKey(ProjectInquiry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=inquiry_upload_to)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self) -> str:
        return f"Attachment #{self.pk} for inquiry #{self.inquiry_id}"  # type: ignore


class Testimonial(models.Model):
    """
    Client testimonials submitted via the contact page.
    These are moderated (approved) before being shown publicly.
    """
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1–5 stars",
    )
    message = models.TextField()

    consent_to_publish = models.BooleanField(
        default=False,
        help_text="Client gave consent to display this testimonial on the website.",
    )
    approved = models.BooleanField(
        default=False,
        help_text="Checked by staff; only approved + consented testimonials are visible.",
    )
    role = models.CharField(
        max_length=20,
        choices=TESTIMONIAL_ROLE_CHOICES,
        blank=True,          # optional
        db_index=True,       # handy for filtering in admin
        help_text="Reviewer role (e.g., Homeowner, Builder)"
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["approved", "consent_to_publish", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.rating}/5)"


class ProjectCaseStudy(models.Model):
    PROJECT_TYPES = [
        ("custom-home", "Custom Home"),
        ("addition", "Addition"),
        ("renovation", "Renovation"),
        ("adu", "ADU / Carriage House"),
        ("plan-modification", "Plan Modification"),
        ("framing", "Framing / Structural Coordination"),
        ("other", "Other Residential Project"),
    ]

    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True, blank=True)
    project_type = models.CharField(max_length=30, choices=PROJECT_TYPES, db_index=True)
    location = models.CharField(
        max_length=120,
        blank=True,
        help_text="Town and state only when the client permits disclosure.",
    )
    summary = models.CharField(max_length=320)
    client_objective = models.TextField()
    design_challenge = models.TextField()
    solution = models.TextField()
    deliverables = models.TextField(blank=True, help_text="One deliverable per line.")
    outcome = models.TextField()
    client_quote = models.TextField(blank=True)
    hero_image = models.ImageField(upload_to="projects/hero/", blank=True, null=True)
    meta_description = models.CharField(max_length=180, blank=True)
    completed_date = models.DateField(blank=True, null=True)
    is_published = models.BooleanField(default=False, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_featured", "-completed_date", "-created_at")
        verbose_name = "project case study"
        verbose_name_plural = "project case studies"
        indexes = [models.Index(fields=("is_published", "is_featured", "completed_date"))]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("pages:case_study_detail", args=[self.slug])

    @property
    def deliverables_list(self) -> list[str]:
        return [line.strip() for line in self.deliverables.splitlines() if line.strip()]


class ProjectCaseStudyImage(models.Model):
    IMAGE_TYPES = [
        ("completed", "Completed Project"),
        ("construction", "Construction Progress"),
        ("drawing", "Drawing / Plan Detail"),
        ("existing", "Existing Condition"),
        ("rendering", "Rendering"),
    ]

    case_study = models.ForeignKey(
        ProjectCaseStudy,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="projects/gallery/")
    image_type = models.CharField(max_length=20, choices=IMAGE_TYPES, default="completed")
    caption = models.CharField(max_length=200, blank=True)
    alt_text = models.CharField(
        max_length=200,
        help_text="Describe what is visibly useful in this image.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "id")
        verbose_name = "case study image"
        verbose_name_plural = "case study images"

    def __str__(self) -> str:
        return f"{self.case_study.title}: {self.get_image_type_display()}"


class AboutPage(models.Model):
    """
    Single-row editable About content.
    Enter ONE paragraph per line in `body` (easier in admin).
    Put one item per line in list fields.
    """
    title = models.CharField(max_length=120, default="About")
    subtitle = models.CharField(max_length=200, blank=True)
    owner_name = models.CharField(max_length=120, blank=True)

    photo_main = models.ImageField(upload_to="about/", blank=True, null=True)
    photo_secondary = models.ImageField(upload_to="about/", blank=True, null=True)

    body = models.TextField(blank=True, help_text="One paragraph per line.")
    highlights = models.TextField(blank=True, help_text="One item per line.")
    badges = models.TextField(blank=True, help_text="One item per line.")
    knowledge_skills = models.TextField(blank=True, help_text="Knowledge & skills - one item per line.")
    licenses = models.TextField(blank=True, help_text="Licenses/registrations - one item per line.")

    is_published = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "About page"
        verbose_name_plural = "About page"

    def __str__(self) -> str:
        return self.title

    @classmethod
    def load(cls) -> "AboutPage":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    # ---------- Helpers ----------
    def paragraphs(self) -> list[str]:
        """
        ONE LINE = ONE PARAGRAPH.
        Remove simple bullet prefixes (•, -, –, -). Ignore empties.
        """
        lines = (self.body or "").splitlines()
        out: list[str] = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith(("•", "-", "–", "-")):
                ln = ln.lstrip("•-–- ").strip()
            out.append(ln)
        return out

    def highlights_list(self) -> list[str]:
        return [l.strip("•- ").strip() for l in self.highlights.splitlines() if l.strip()]

    def badges_list(self) -> list[str]:
        return [l.strip() for l in self.badges.splitlines() if l.strip()]

    def knowledge_skills_list(self) -> list[str]:
        return [l.strip("•- ").strip() for l in self.knowledge_skills.splitlines() if l.strip()]

    def licenses_list(self) -> list[str]:
        return [l.strip("•- ").strip() for l in self.licenses.splitlines() if l.strip()]


class BusinessHour(models.Model):
    DAYS = [
        ("sun", "Sunday"),
        ("mon", "Monday"),
        ("tue", "Tuesday"),
        ("wed", "Wednesday"),
        ("thu", "Thursday"),
        ("fri", "Friday"),
        ("sat", "Saturday"),
    ]
    site = models.ForeignKey("SiteSettings", on_delete=models.CASCADE, related_name="hours")
    day = models.CharField(max_length=3, choices=DAYS)
    is_closed = models.BooleanField(default=False)
    by_appointment = models.BooleanField(default=False)
    open_time = models.TimeField(blank=True, null=True)
    close_time = models.TimeField(blank=True, null=True)
    note = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ("site", "day")
        ordering = (
            Case(
                When(day="sun", then=Value(0)),
                When(day="mon", then=Value(1)),
                When(day="tue", then=Value(2)),
                When(day="wed", then=Value(3)),
                When(day="thu", then=Value(4)),
                When(day="fri", then=Value(5)),
                When(day="sat", then=Value(6)),
                default=Value(7),
                output_field=IntegerField(),
            ),
        )

    def __str__(self):
        label = dict(self.DAYS).get(self.day, self.day)
        if self.is_closed:
            return f"{label}: Closed"
        if self.by_appointment:
            return f"{label}: By Appointment"
        if self.open_time and self.close_time:
            return f"{label}: {self.open_time.strftime('%I:%M %p').lstrip('0')} – {self.close_time.strftime('%I:%M %p').lstrip('0')}"
        return f"{label}: -"

WEB_PROJECT_TYPE_CHOICES = [
    ("business_site", "Business Website"),
    ("web_app", "Web Application"),
    ("ecommerce", "E-commerce Site"),
    ("redesign", "Redesign / Refresh"),
    ("other", "Other"),
]

WEB_BUDGET_RANGE_CHOICES = [
    ("", "Budget range (optional)"),
    ("under_3k", "Under $3,000"),
    ("3k_7k", "$3,000-$7,000"),
    ("7k_15k", "$7,000-$15,000"),
    ("15k_plus", "$15,000+"),
    ("not_sure", "Not sure yet"),
]

WEB_TIMELINE_CHOICES = [
    ("", "Desired timing (optional)"),
    ("asap", "As soon as practical"),
    ("1_2_months", "Within 1-2 months"),
    ("3_6_months", "Within 3-6 months"),
    ("flexible", "Flexible / exploring"),
]


class WebDesignInquiry(models.Model):
    name = models.CharField(max_length=120)
    company_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=30, blank=True)
    current_website = models.URLField(blank=True)
    project_type = models.CharField(max_length=30, choices=WEB_PROJECT_TYPE_CHOICES, blank=True)
    budget_range = models.CharField(max_length=20, choices=WEB_BUDGET_RANGE_CHOICES, blank=True)
    timeline = models.CharField(max_length=20, choices=WEB_TIMELINE_CHOICES, blank=True)
    message = models.TextField()
    terms_accepted = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    STATUS_CHOICES = [("new", "New"), ("reviewed", "Reviewed"), ("archived", "Archived")]
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="new", db_index=True)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Web design inquiry"
        verbose_name_plural = "Web design inquiries"
        indexes = [
            models.Index(fields=["status", "submitted_at"]),
        ]

    def __str__(self) -> str:
        pt = dict(WEB_PROJECT_TYPE_CHOICES).get(self.project_type, self.project_type)
        return f"{self.name} - {pt} - {self.submitted_at:%Y-%m-%d}"


class PricingPage(models.Model):
    """Singleton admin-editable pricing page content."""
    title = models.CharField(max_length=120, default="Pricing")
    subtitle = models.CharField(max_length=200, blank=True)
    included_heading = models.CharField(max_length=200, blank=True, default="What's Included")
    included_body = models.TextField(
        blank=True,
        help_text="One bullet point per line. Lines starting with -, •, or — are stripped of the prefix.",
    )
    is_published = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pricing page"
        verbose_name_plural = "Pricing page"

    def __str__(self) -> str:
        return self.title

    @classmethod
    def load(cls) -> "PricingPage":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def bullets(self) -> list[str]:
        lines = (self.included_body or "").splitlines()
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith(("•", "-", "–", "—")):
                ln = ln.lstrip("•-–— ").strip()
            out.append(ln)
        return out


class PricingItem(models.Model):
    PRICE_TYPE_FLAT = "flat"
    PRICE_TYPE_HOURLY = "hourly"
    PRICE_TYPE_CHOICES = [
        (PRICE_TYPE_FLAT, "Flat Rate"),
        (PRICE_TYPE_HOURLY, "Per Hour"),
    ]

    page = models.ForeignKey(PricingPage, on_delete=models.CASCADE, related_name="items")
    label = models.CharField(max_length=150, help_text="e.g. 'Custom Home Design'")
    description = models.CharField(max_length=300, blank=True, help_text="Optional short description shown on the page.")
    price_type = models.CharField(max_length=10, choices=PRICE_TYPE_CHOICES, default=PRICE_TYPE_FLAT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    unit_label = models.CharField(max_length=60, blank=True, help_text="Shown next to the price, e.g. 'per project', 'per page'.")
    show_in_calculator = models.BooleanField(default=True, help_text="Include this item in the pricing calculator.")
    default_quantity = models.DecimalField(
        max_digits=8, decimal_places=1, default=1,
        help_text="Default quantity pre-filled in the calculator.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Lower numbers appear first.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Pricing item"
        verbose_name_plural = "Pricing items"

    def __str__(self) -> str:
        type_label = dict(self.PRICE_TYPE_CHOICES).get(self.price_type, self.price_type)
        return f"{self.label} — ${self.amount} ({type_label})"


class AffiliateCategory(models.TextChoices):
    HOME_DESIGN = "home_design", "Home Design Products"
    WEB_DEV = "web_dev", "Web / Coding Books & Tools"


# Backwards-compatible alias for any code/templates that referenced the old list.
AFFILIATE_CATEGORY_CHOICES = AffiliateCategory.choices


class AffiliateProduct(models.Model):
    """
    Amazon Associates (or other affiliate) product link, managed in admin.
    Grouped by `category` so the right products show on the right page:
    `home_design` products appear on the Home page, `web_dev` on the Web Design page.
    """
    title = models.CharField(max_length=200)
    category = models.CharField(
        max_length=20,
        choices=AffiliateCategory.choices,
        db_index=True,
        help_text="Which page section this product appears in.",
    )
    url = models.URLField(
        "Affiliate URL",
        max_length=600,
        help_text="Your full Amazon affiliate link (includes your Associates tag).",
    )
    image_url = models.URLField(
        "Image URL",
        max_length=600,
        blank=True,
        help_text=(
            "Optional. Use a compliant image link from Amazon SiteStripe "
            "(Get Link → Image) or the Product Advertising API — do not hotlink "
            "arbitrary product images. Leave blank to show a themed placeholder icon."
        ),
    )
    description = models.CharField(
        max_length=300,
        blank=True,
        help_text="Optional short blurb shown under the title.",
    )
    price_note = models.CharField(
        max_length=40,
        blank=True,
        help_text="Optional free text shown as the price, e.g. '$24.99' or 'From $19'.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Lower numbers appear first.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "Affiliate product"
        verbose_name_plural = "Affiliate products"
        indexes = [
            models.Index(fields=["category", "is_active", "order"]),
        ]

    def __str__(self) -> str:
        cat = dict(AFFILIATE_CATEGORY_CHOICES).get(self.category, self.category)
        return f"{self.title} ({cat})"


# models.py
class ContactMessage(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        READ = "read", "Read"
        REPLIED = "replied", "Replied"

    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    subject = models.CharField(max_length=150)
    message = models.TextField()

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    referer = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["email", "created_at"]),
        ]

    def __str__(self):
        return f"{self.subject} - {self.name} <{self.email}>"
