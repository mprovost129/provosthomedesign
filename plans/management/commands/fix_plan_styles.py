"""
Management command to fix plans without house styles
"""
from django.core.management.base import BaseCommand
from plans.models import Plans, HouseStyle


class Command(BaseCommand):
    help = 'Fix plans that have no house styles assigned'

    def add_arguments(self, parser):
        parser.add_argument(
            '--create-general',
            action='store_true',
            help='Create a General house style for fallback',
        )
        parser.add_argument(
            '--assign-default',
            type=str,
            help='Assign this style slug to plans without styles',
        )

    def handle(self, *args, **options):
        # Find plans without styles
        plans_without_styles = []
        for plan in Plans.objects.all():
            if not plan.house_styles.exists():
                plans_without_styles.append(plan)

        if not plans_without_styles:
            self.stdout.write(self.style.SUCCESS('✓ All plans have house styles assigned'))
            return

        self.stdout.write(self.style.WARNING(
            f'Found {len(plans_without_styles)} plans without house styles:'
        ))
        for plan in plans_without_styles:
            self.stdout.write(f'  - {plan.plan_number}')

        if options['create_general']:
            # Create General house style
            general, created = HouseStyle.objects.get_or_create(
                slug='general',
                defaults={
                    'style_name': 'General',
                    'description': 'General house plans',
                    'order': 999
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Created General house style'))
            
            # Assign to plans without styles
            for plan in plans_without_styles:
                plan.house_styles.add(general)
            
            self.stdout.write(self.style.SUCCESS(
                f'✓ Assigned General style to {len(plans_without_styles)} plans'
            ))

        elif options['assign_default']:
            try:
                style = HouseStyle.objects.get(slug=options['assign_default'])
                for plan in plans_without_styles:
                    plan.house_styles.add(style)
                
                self.stdout.write(self.style.SUCCESS(
                    f'✓ Assigned {style.style_name} to {len(plans_without_styles)} plans'
                ))
            except HouseStyle.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'✗ House style "{options["assign_default"]}" does not exist'
                ))
                self.stdout.write('Available styles:')
                for style in HouseStyle.objects.all():
                    self.stdout.write(f'  - {style.slug} ({style.style_name})')
        else:
            self.stdout.write(self.style.WARNING('\nTo fix, run one of:'))
            self.stdout.write('  python manage.py fix_plan_styles --create-general')
            self.stdout.write('  python manage.py fix_plan_styles --assign-default=colonial')
