"""
Management command to import clients from Freshbooks CSV export.
Usage: python manage.py import_freshbooks_clients <csv_file_path>
"""
import csv
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.db import transaction
from billing.models import Client


class Command(BaseCommand):
    help = 'Import clients from Freshbooks CSV export'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the Freshbooks CSV export file'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview import without saving to database'
        )
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing users if email already exists'
        )

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        dry_run = options['dry_run']
        update_existing = options['update']

        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                created_count = 0
                updated_count = 0
                skipped_count = 0
                errors = []

                with transaction.atomic():
                    for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
                        try:
                            email = row.get('Email', '').strip().lower()
                            first_name = row.get('First Name', '').strip()
                            last_name = row.get('Last Name', '').strip()
                            
                            # Skip rows without email
                            if not email:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f'Row {row_num}: Skipping - no email address'
                                    )
                                )
                                skipped_count += 1
                                continue
                            
                            # Skip rows without first name
                            if not first_name:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f'Row {row_num}: Skipping {email} - no first name'
                                    )
                                )
                                skipped_count += 1
                                continue

                            # Generate username from email (everything before @)
                            username = email.split('@')[0]
                            
                            # Handle duplicate usernames by appending number
                            base_username = username
                            counter = 1
                            while User.objects.filter(username=username).exclude(email=email).exists():
                                username = f"{base_username}{counter}"
                                counter += 1

                            # Check if user exists
                            user_exists = User.objects.filter(email=email).exists()
                            
                            if user_exists and not update_existing:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f'Row {row_num}: User {email} already exists (use --update to update)'
                                    )
                                )
                                skipped_count += 1
                                continue

                            if dry_run:
                                action = "UPDATE" if user_exists else "CREATE"
                                self.stdout.write(
                                    f'[DRY RUN] {action}: {first_name} {last_name} ({email})'
                                )
                                if user_exists:
                                    updated_count += 1
                                else:
                                    created_count += 1
                            else:
                                if user_exists and update_existing:
                                    # Update existing user
                                    user = User.objects.get(email=email)
                                    user.first_name = first_name
                                    user.last_name = last_name
                                    user.username = username
                                    user.save()
                                    
                                    self.stdout.write(
                                        self.style.SUCCESS(
                                            f'Row {row_num}: Updated {first_name} {last_name} ({email})'
                                        )
                                    )
                                    updated_count += 1
                                else:
                                    # Create new user
                                    user = User.objects.create_user(
                                        username=username,
                                        email=email,
                                        first_name=first_name,
                                        last_name=last_name,
                                        is_active=True,
                                        is_staff=False,
                                        is_superuser=False
                                    )
                                    # Set unusable password - users will need to reset
                                    user.set_unusable_password()
                                    user.save()
                                    
                                    # Create corresponding Client record
                                    Client.objects.create(
                                        user=user,
                                        first_name=first_name,
                                        last_name=last_name,
                                        email=email
                                    )
                                    
                                    self.stdout.write(
                                        self.style.SUCCESS(
                                            f'Row {row_num}: Created {first_name} {last_name} ({email})'
                                        )
                                    )
                                    created_count += 1

                        except Exception as e:
                            error_msg = f'Row {row_num}: Error processing {row.get("Email", "unknown")} - {str(e)}'
                            errors.append(error_msg)
                            self.stdout.write(self.style.ERROR(error_msg))

                    # If dry run, rollback transaction
                    if dry_run:
                        transaction.set_rollback(True)

                # Summary
                self.stdout.write('\n' + '='*60)
                if dry_run:
                    self.stdout.write(self.style.WARNING('DRY RUN - No changes made to database'))
                
                self.stdout.write(f'Created: {created_count}')
                self.stdout.write(f'Updated: {updated_count}')
                self.stdout.write(f'Skipped: {skipped_count}')
                self.stdout.write(f'Errors: {len(errors)}')
                self.stdout.write('='*60)

                if errors:
                    self.stdout.write(self.style.ERROR('\nErrors encountered:'))
                    for error in errors:
                        self.stdout.write(self.style.ERROR(f'  - {error}'))

                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            '\nRun without --dry-run to actually import the data'
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'\n✅ Successfully imported {created_count + updated_count} clients!'
                        )
                    )
                    if created_count > 0:
                        self.stdout.write(
                            self.style.WARNING(
                                '\n⚠️  Note: New users have no password set. '
                                'They will need to use password reset to access their accounts.'
                            )
                        )

        except FileNotFoundError:
            raise CommandError(f'File not found: {csv_file}')
        except csv.Error as e:
            raise CommandError(f'Error reading CSV file: {e}')
        except Exception as e:
            raise CommandError(f'Unexpected error: {e}')
