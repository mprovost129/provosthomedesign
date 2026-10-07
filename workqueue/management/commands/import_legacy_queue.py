import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from workqueue.legacy_import import ImportConflict, import_snapshot


class Command(BaseCommand):
    help = "Validate/import a private Sheet snapshot while all queue features are disabled. Defaults to dry run."

    def add_arguments(self, parser):
        parser.add_argument("snapshot")
        parser.add_argument("--reference-high-water", type=int, required=True)
        parser.add_argument("--connections", help="Private JSON mapping child request reference to root reference")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            snapshot = Path(options["snapshot"])
            if snapshot.stat().st_size > 20 * 1024 * 1024:
                raise ImportConflict("Snapshot exceeds the 20 MB import limit.")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            links = json.loads(Path(options["connections"]).read_text(encoding="utf-8")) if options["connections"] else {}
            summary = import_snapshot(payload, high_water=options["reference_high_water"], links=links, apply=options["apply"])
        except ImportConflict as exc:
            raise CommandError(str(exc)) from None
        except (ValidationError, KeyError, TypeError, ValueError, ArithmeticError, OSError):
            # Never print private source answers or provider details into deployment logs.
            raise CommandError("Import stopped: source validation or reconciliation failed. Inspect the private source and database before retrying.") from None
        self.stdout.write(json.dumps(summary, sort_keys=True))
