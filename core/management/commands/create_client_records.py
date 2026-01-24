"""
Management command to create Client records for users who don't have them.
Usage: python manage.py create_client_records
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from billing.models import Client
from django.db import transaction


class Command(BaseCommand):
    help = 'Create Client records for users who do not have them (non-staff users only)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be created without saving to database'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Get all non-staff users without a client_profile
        users_without_clients = User.objects.filter(
            is_staff=False,
            is_superuser=False,
            client_profile__isnull=True
        ).select_related('client_profile')

        if not users_without_clients.exists():
            self.stdout.write(
                self.style.SUCCESS('All non-staff users already have Client records!')
            )
            return

        created_count = 0
        errors = []

        with transaction.atomic():
            for user in users_without_clients:
                try:
                    if dry_run:
                        self.stdout.write(
                            f'[DRY RUN] Would create Client for: {user.first_name} {user.last_name} ({user.email})'
                        )
                        created_count += 1
                    else:
                        Client.objects.create(
                            user=user,
                            first_name=user.first_name,
                            last_name=user.last_name,
                            email=user.email
                        )
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Created Client record for: {user.first_name} {user.last_name} ({user.email})'
                            )
                        )
                        created_count += 1

                except Exception as e:
                    error_msg = f'Error creating Client for {user.email}: {str(e)}'
                    errors.append(error_msg)
                    self.stdout.write(self.style.ERROR(error_msg))

            # If dry run, rollback transaction
            if dry_run:
                transaction.set_rollback(True)

        # Summary
        self.stdout.write('\n' + '='*60)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes made to database'))
        
        self.stdout.write(f'Client records created: {created_count}')
        self.stdout.write(f'Errors: {len(errors)}')
        self.stdout.write('='*60)

        if errors:
            self.stdout.write(self.style.ERROR('\nErrors encountered:'))
            for error in errors:
                self.stdout.write(self.style.ERROR(f'  - {error}'))

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '\nRun without --dry-run to actually create the Client records'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSuccessfully created {created_count} Client records!'
                )
            )
