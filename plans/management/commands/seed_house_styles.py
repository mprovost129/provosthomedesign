# plans/management/commands/seed_house_styles.py
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

# --- Helpers that adapt to your model field names ---
def _get_name_field() -> str:
    # Prefer 'name'; fall back to 'style_name'
    fields = {f.name for f in HouseStyle._meta.get_fields() if hasattr(f, "attname")}
    if "name" in fields:
        return "name"
    elif "style_name" in fields:
        return "style_name"
    # Last resort: assume 'name'
    return "name"

def _get_order_field() -> str | None:
    fields = {f.name for f in HouseStyle._meta.get_fields() if hasattr(f, "attname")}
    return "order" if "order" in fields else None

NAME_FIELD = _get_name_field()
ORDER_FIELD = _get_order_field()

def _get_name(obj: HouseStyle) -> str:
    return getattr(obj, NAME_FIELD, "") or ""

def _set_name(obj: HouseStyle, value: str):
    setattr(obj, NAME_FIELD, value)

def _set_order_if_present(obj: HouseStyle, idx: int):
    if ORDER_FIELD:
        setattr(obj, ORDER_FIELD, idx)

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
        "Use --sync to update existing rows' name/order to match DEFAULT_STYLES."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="If a style exists for the slug/name, update name and order to match DEFAULT_STYLES.",
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
                # Compare current stored name (case-insensitive)
                if _get_name(obj).strip().casefold() != name.strip().casefold():
                    if sync:
                        if not dry_run:
                            _set_name(obj, name)
                            _set_order_if_present(obj, idx)
                            update_fields = [NAME_FIELD]
                            if ORDER_FIELD:
                                update_fields.append(ORDER_FIELD)
                            obj.save(update_fields=update_fields)
                        updated += 1
                        self.stdout.write(f"↻ Synced name/order: {name} ({base_slug})")
                    else:
                        # Create a separate row with a unique slug (don't rename existing)
                        new_slug = _unique_slug(base_slug)
                        if not dry_run:
                            new_obj = HouseStyle(slug=new_slug)
                            _set_name(new_obj, name)
                            _set_order_if_present(new_obj, idx)
                            new_obj.save()
                        created += 1
                        self.stdout.write(f"✔ Created (new slug): {name} ({new_slug})")
                else:
                    # Same name: maybe just order needs syncing/backfilling
                    needs_order = False
                    if ORDER_FIELD:
                        needs_order = getattr(obj, ORDER_FIELD, None) != idx
                    if sync and needs_order:
                        if not dry_run:
                            _set_order_if_present(obj, idx)
                            obj.save(update_fields=[ORDER_FIELD])
                        updated += 1
                        self.stdout.write(f"↻ Synced order: {name} ({base_slug})")
                    else:
                        skipped += 1
                continue

            # 2) No slug match; try case-insensitive name match (stable slug)
            obj_by_name = HouseStyle.objects.filter(**{f"{NAME_FIELD}__iexact": name}).first()
            if obj_by_name:
                if sync and ORDER_FIELD and getattr(obj_by_name, ORDER_FIELD, None) != idx:
                    if not dry_run:
                        _set_order_if_present(obj_by_name, idx)
                        obj_by_name.save(update_fields=[ORDER_FIELD])
                    updated += 1
                    self.stdout.write(f"↻ Synced order by name: {name} ({obj_by_name.slug})")
                else:
                    skipped += 1
                continue

            # 3) Create new row with the base slug (or a unique variant if taken concurrently)
            new_slug = base_slug if not HouseStyle.objects.filter(slug=base_slug).exists() else _unique_slug(base_slug)
            if not dry_run:
                new_obj = HouseStyle(slug=new_slug)
                _set_name(new_obj, name)
                _set_order_if_present(new_obj, idx)
                new_obj.save()
            created += 1
            self.stdout.write(f"✔ Created: {name} ({new_slug})")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete — created: {created}, updated: {updated}, skipped: {skipped}."
                + (" (dry-run)" if dry_run else "")
            )
        )
