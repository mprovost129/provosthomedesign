from django.core.management.base import BaseCommand, CommandError

from pages.models import AboutPage, ProjectCaseStudy
from plans.models import Plans


class Command(BaseCommand):
    help = "Report authentic content still needed before the residential site is launch-ready."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-gaps",
            action="store_true",
            help="Exit with an error when available plans or trust content have gaps.",
        )

    def handle(self, *args, **options):
        gaps = 0
        available_plans = Plans.objects.filter(is_available=True).order_by("plan_number")

        self.stdout.write(self.style.MIGRATE_HEADING("Available plan content"))
        if not available_plans.exists():
            gaps += 1
            self.stdout.write(self.style.WARNING("No available house plans."))
        for plan in available_plans:
            missing = plan.content_missing_fields
            if missing:
                gaps += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"{plan.plan_number}: missing {', '.join(missing)}"
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"{plan.plan_number}: ready"))

        self.stdout.write(self.style.MIGRATE_HEADING("Trust content"))
        published_studies = ProjectCaseStudy.objects.filter(is_published=True).count()
        featured_studies = ProjectCaseStudy.objects.filter(
            is_published=True,
            is_featured=True,
        ).count()
        if not published_studies:
            gaps += 1
            self.stdout.write(self.style.WARNING("No published project case studies."))
        elif not featured_studies:
            gaps += 1
            self.stdout.write(
                self.style.WARNING(
                    f"{published_studies} case study/studies published, but none featured."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{published_studies} published case study/studies; {featured_studies} featured."
                )
            )

        if AboutPage.objects.filter(is_published=True).exists():
            self.stdout.write(self.style.SUCCESS("Published About page: ready"))
        else:
            gaps += 1
            self.stdout.write(self.style.WARNING("No published About page."))

        if gaps:
            summary = f"Content audit found {gaps} launch-readiness gap(s)."
            if options["fail_on_gaps"]:
                raise CommandError(summary)
            self.stdout.write(self.style.WARNING(summary))
            return

        self.stdout.write(self.style.SUCCESS("Main-site content audit passed."))
