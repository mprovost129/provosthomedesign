from __future__ import annotations

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.templatetags.static import static
from plans.models import HouseStyle  # dynamic link to plans app
from django.db.models import Case, When, Value, IntegerField


CONTACT_METHOD_CHOICES = [
    ("email", "Email"),
    ("phone", "Phone"),
    ("text", "Text"),
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
        return f"{self.first_name} {self.last_name} — {self.email} — {self.submitted_at:%Y-%m-%d}"

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
    knowledge_skills = models.TextField(blank=True, help_text="Knowledge & skills — one item per line.")
    licenses = models.TextField(blank=True, help_text="Licenses/registrations — one item per line.")

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
        Remove simple bullet prefixes (•, -, –, —). Ignore empties.
        """
        lines = (self.body or "").splitlines()
        out: list[str] = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith(("•", "-", "–", "—")):
                ln = ln.lstrip("•-–— ").strip()
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
        return f"{label}: —"

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
        return f"{self.subject} — {self.name} <{self.email}>"
