"""
Seed a curated starter set of Amazon affiliate products.

Usage:
    python manage.py seed_affiliate_products --tag yourtag-20
    python manage.py seed_affiliate_products --tag yourtag-20 --update   # refresh URLs on existing rows

The Amazon Associates tracking ID ("tag", e.g. "provosthome-20") is appended to
every product link so the links earn commission. The tag can also be set once via
the AMAZON_ASSOCIATES_TAG environment variable / Django setting instead of --tag.

Idempotent: rows are matched on (category, title). Re-running won't create
duplicates. Prices and images are intentionally left blank — Amazon's Operating
Agreement requires those to come from SiteStripe / the Product Advertising API,
not hardcoded values. Add images later via SiteStripe in the Django admin.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pages.models import AffiliateProduct, AffiliateCategory


# Curated starter products. ASINs verified against amazon.com listings.
# (category, title, asin, description)
CURATED = [
    # --- Home / architecture & design (shows on the Home page) ---
    (
        AffiliateCategory.HOME_DESIGN,
        "A Pattern Language: Towns, Buildings, Construction",
        "0195019199",
        "Christopher Alexander's classic - 253 timeless patterns for designing homes and spaces people love.",
    ),
    (
        AffiliateCategory.HOME_DESIGN,
        "101 Things I Learned in Architecture School",
        "0262062666",
        "Matthew Frederick's quick, illustrated lessons on design, drawing, and how buildings really work.",
    ),
    (
        AffiliateCategory.HOME_DESIGN,
        "Architecture: Form, Space, and Order",
        "1119853370",
        "Francis D.K. Ching's beautifully illustrated introduction to the vocabulary of architectural design.",
    ),
    (
        AffiliateCategory.HOME_DESIGN,
        "Architectural Graphics",
        "1394206240",
        "Ching's essential guide to drafting conventions and hand drawing for clear design presentations.",
    ),
    (
        AffiliateCategory.HOME_DESIGN,
        "Sakura Pigma Micron Fineliner Pen Set (6 Sizes)",
        "B0008G8G8Y",
        "Archival, waterproof ink pens in assorted nib sizes - a go-to for crisp linework and inking plans.",
    ),
    (
        AffiliateCategory.HOME_DESIGN,
        "Strathmore 400 Series Sketch Pad (9x12, 100 Sheets)",
        "B0027A39PY",
        "Heavyweight, fine-tooth sketch paper for concept sketches, elevations, and quick design studies.",
    ),

    # --- Web / coding (shows on the Web Design page) ---
    (
        AffiliateCategory.WEB_DEV,
        "Python Crash Course, 3rd Edition",
        "1718502702",
        "Eric Matthes' hands-on, project-based intro to Python - the book I recommend to start with.",
    ),
    (
        AffiliateCategory.WEB_DEV,
        "HTML and CSS: Design and Build Websites",
        "1118008189",
        "Jon Duckett's full-color, beginner-friendly guide to writing clean HTML and CSS.",
    ),
    (
        AffiliateCategory.WEB_DEV,
        "Web Design with HTML, CSS, JavaScript and jQuery Set",
        "1118907442",
        "Jon Duckett's two-book set covering front-end structure, style, and interactivity.",
    ),
    (
        AffiliateCategory.WEB_DEV,
        "Automate the Boring Stuff with Python, 3rd Edition",
        "1718503407",
        "Al Sweigart's practical programming for total beginners - automate real-world tasks with Python.",
    ),
]


def _build_url(asin: str, tag: str) -> str:
    return f"https://www.amazon.com/dp/{asin}?tag={tag}"


class Command(BaseCommand):
    help = "Seed a curated starter set of Amazon affiliate products with your Associates tag."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tag",
            default=getattr(settings, "AMAZON_ASSOCIATES_TAG", ""),
            help="Your Amazon Associates tracking ID, e.g. 'provosthome-20'. "
                 "Defaults to the AMAZON_ASSOCIATES_TAG setting if not given.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Update the URL/description on products that already exist.",
        )

    def handle(self, *args, **options):
        tag = (options["tag"] or "").strip()
        if not tag:
            raise CommandError(
                "No Amazon Associates tag provided. Pass --tag yourtag-20 "
                "or set AMAZON_ASSOCIATES_TAG in your settings/environment."
            )

        do_update = options["update"]
        created = updated = skipped = 0

        for order, (category, title, asin, description) in enumerate(CURATED, start=1):
            url = _build_url(asin, tag)
            obj, was_created = AffiliateProduct.objects.get_or_create(
                category=category,
                title=title,
                defaults={
                    "url": url,
                    "description": description,
                    "order": order,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  + {title}"))
            elif do_update:
                obj.url = url
                obj.description = description
                obj.save(update_fields=["url", "description"])
                updated += 1
                self.stdout.write(f"  ~ {title} (updated)")
            else:
                skipped += 1
                self.stdout.write(f"  = {title} (exists, skipped)")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created {created}, updated {updated}, skipped {skipped}. "
                f"Tag applied: {tag}"
            )
        )
        if not do_update and skipped:
            self.stdout.write("Tip: re-run with --update to refresh links on existing rows.")
