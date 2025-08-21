from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from plans.models import HouseStyle

# Keep the order you want displayed in filters/menus
DEFAULT_STYLES = [
    "Colonial",
    "Cape Cod",
    "Farmhouse",
    "Modern Farmhouse",
    "Craftsman",
    "Ranch",
    "Split Level",
    "Contemporary",
    "Modern",
    "Traditional",
    "Tudor",
    "Cottage",
    "Bungalow",
    "Victorian",
    "Mediterranean",
    "Spanish",
    "French Country",
    "Beach / Coastal",
    "Mountain / Rustic",
    "Barndominium",
    "Duplex",
    "Townhouse / Rowhouse",
    "A-Frame",
    "Scandinavian",
    "Mid-Century Modern",
    "Prairie",
    "Southwest / Pueblo",
    "Low Country",
    "Lake House",
    "Carriage House / ADU",
]


def _unique_slug(base: str) -> str:
    """
    Return a unique slug by appending -2, -3, ... as needed.
    """
    base = base or "style"
    if not HouseStyle.objects.filter(slug=base).exists():
        return base
    i = 2
    while True:
        candidate = f"{base}-{i}"
        if not HouseStyle.objects.filter(slug=candidate).exists():
            return candidate
        i += 1


class Command(BaseCommand):
    help = (
        "Seed initial HouseStyle options (creates missing). "
        "Use --sync to update existing rows' style_name/order to match DEFAULT_STYLES."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="If a style exists for the slug/name, update style_name and order to match DEFAULT_STYLES.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing to the database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        sync = bool(options.get("sync"))
        dry_run = bool(options.get("dry_run"))

        created = 0
        updated = 0
        skipped = 0

        for idx, name in enumerate(DEFAULT_STYLES, start=1):
            base_slug = slugify(name) or "style"

            # 1) Exact slug match has priority
            obj = HouseStyle.objects.filter(slug=base_slug).first()
            if obj:
                # Style name differs (case-insensitive compare)
                if (obj.style_name or "").strip().casefold() != name.strip().casefold():
                    if sync:
                        if not dry_run:
                            obj.style_name = name
                            # Backfill/normalize display order
                            if getattr(obj, "order", None) != idx:
                                obj.order = idx
                            obj.save(update_fields=["style_name", "order"])
                        updated += 1
                        self.stdout.write(f"↻ Synced name/order: {name} ({base_slug})")
                    else:
                        # Create a separate row with a unique slug (don't rename existing)
                        new_slug = _unique_slug(base_slug)
                        if not dry_run:
                            HouseStyle.objects.create(slug=new_slug, style_name=name, order=idx)
                        created += 1
                        self.stdout.write(f"✔ Created (new slug): {name} ({new_slug})")
                else:
                    # Same name: maybe just order needs syncing/backfilling
                    if sync and getattr(obj, "order", None) != idx:
                        if not dry_run:
                            obj.order = idx
                            obj.save(update_fields=["order"])
                        updated += 1
                        self.stdout.write(f"↻ Synced order: {name} ({base_slug})")
                    else:
                        skipped += 1
                continue

            # 2) No slug match; try case-insensitive name match (stable slug)
            obj_by_name = HouseStyle.objects.filter(style_name__iexact=name).first()
            if obj_by_name:
                if sync and getattr(obj_by_name, "order", None) != idx:
                    if not dry_run:
                        obj_by_name.order = idx
                        obj_by_name.save(update_fields=["order"])
                    updated += 1
                    self.stdout.write(f"↻ Synced order by name: {name} ({obj_by_name.slug})")
                else:
                    skipped += 1
                continue

            # 3) Create new row with the base slug (or a unique variant if taken concurrently)
            new_slug = base_slug if not HouseStyle.objects.filter(slug=base_slug).exists() else _unique_slug(base_slug)
            if not dry_run:
                HouseStyle.objects.create(slug=new_slug, style_name=name, order=idx)
            created += 1
            self.stdout.write(f"✔ Created: {name} ({new_slug})")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete — created: {created}, updated: {updated}, skipped: {skipped}."
                + (" (dry-run)" if dry_run else "")
            )
        )
