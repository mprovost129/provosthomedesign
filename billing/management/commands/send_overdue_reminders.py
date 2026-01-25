from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta
from billing.models import Invoice
from core.models import SystemSettings
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Send overdue invoice reminders'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Send reminders for invoices overdue by specific number of days',
        )

    def handle(self, *args, **options):
        try:
            settings = SystemSettings.objects.first()
            if not settings:
                self.stdout.write(self.style.ERROR('SystemSettings not configured'))
                return

            today = timezone.now().date()
            
            # Get reminder days from options or settings
            reminder_days_list = []
            if options['days']:
                reminder_days_list = [options['days']]
            else:
                # Check for reminders at 30, 60, 90 days
                reminder_days_list = [30, 60, 90]

            sent_count = 0
            
            for days_overdue in reminder_days_list:
                target_date = today - timedelta(days=days_overdue)
                
                # Find invoices due on target date that are still unpaid
                invoices = Invoice.objects.filter(
                    due_date=target_date,
                    status__in=['issued', 'partial'],
                    reminder_sent=False
                )

                for invoice in invoices:
                    if self.send_reminder(invoice, settings):
                        invoice.reminder_sent = True
                        invoice.last_reminder_date = timezone.now()
                        invoice.save()
                        sent_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Reminder sent for invoice {invoice.invoice_number}'
                            )
                        )

            self.stdout.write(
                self.style.SUCCESS(f'Successfully sent {sent_count} reminders')
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {str(e)}'))

    def send_reminder(self, invoice, settings):
        """Send overdue reminder email for an invoice"""
        try:
            # Calculate days overdue
            today = timezone.now().date()
            days_overdue = (today - invoice.due_date).days

            # Get client email
            if hasattr(invoice.client, 'email') and invoice.client.email:
                client_email = invoice.client.email
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'No email found for client {invoice.client}'
                    )
                )
                return False

            # Get admin user for from_email
            admin_user = User.objects.filter(is_superuser=True).first()
            if not admin_user or not admin_user.email:
                self.stdout.write(self.style.WARNING('No admin user email configured'))
                return False

            # Prepare context
            context = {
                'client': invoice.client,
                'invoice': invoice,
                'days_overdue': days_overdue,
                'company_name': settings.company_name or 'Our Company',
                'company_email': admin_user.email,
                'company_phone': settings.phone_number or '',
            }

            # Render email
            subject = f'Invoice {invoice.invoice_number} is {days_overdue} days overdue'
            html_message = render_to_string(
                'billing/email/overdue_reminder.html',
                context
            )
            text_message = render_to_string(
                'billing/email/overdue_reminder.txt',
                context
            )

            # Send email
            send_mail(
                subject,
                text_message,
                admin_user.email,
                [client_email],
                html_message=html_message,
                fail_silently=False,
            )

            return True

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error sending reminder: {str(e)}')
            )
            return False
