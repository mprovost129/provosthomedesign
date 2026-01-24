from django.core.management.base import BaseCommand
from help.models import HelpCategory, HelpArticle, FAQ


class Command(BaseCommand):
    help = 'Populate initial help content for the portal'

    def handle(self, *args, **options):
        self.stdout.write('Creating help categories...')
        
        # Client Categories
        client_portal = HelpCategory.objects.get_or_create(
            slug='client-portal-basics',
            defaults={
                'name': 'Client Portal Basics',
                'description': 'Learn the fundamentals of using your client portal',
                'icon': 'fa-user',
                'audience': 'client',
                'order': 1
            }
        )[0]
        
        invoices_payments = HelpCategory.objects.get_or_create(
            slug='invoices-and-payments',
            defaults={
                'name': 'Invoices & Payments',
                'description': 'Understanding invoices, proposals, and making payments',
                'icon': 'fa-file-invoice-dollar',
                'audience': 'client',
                'order': 2
            }
        )[0]
        
        projects_plans = HelpCategory.objects.get_or_create(
            slug='projects-and-plans',
            defaults={
                'name': 'Projects & Plans',
                'description': 'Viewing your projects and accessing plan files',
                'icon': 'fa-project-diagram',
                'audience': 'client',
                'order': 3
            }
        )[0]
        
        # Staff Categories
        staff_portal = HelpCategory.objects.get_or_create(
            slug='staff-portal-guide',
            defaults={
                'name': 'Staff Portal Guide',
                'description': 'Complete guide for staff members using the portal',
                'icon': 'fa-user-tie',
                'audience': 'staff',
                'order': 1
            }
        )[0]
        
        client_management = HelpCategory.objects.get_or_create(
            slug='client-management',
            defaults={
                'name': 'Client Management',
                'description': 'Managing clients, projects, and communication',
                'icon': 'fa-users',
                'audience': 'staff',
                'order': 2
            }
        )[0]
        
        invoicing = HelpCategory.objects.get_or_create(
            slug='invoicing-proposals',
            defaults={
                'name': 'Invoicing & Proposals',
                'description': 'Creating invoices, proposals, and tracking payments',
                'icon': 'fa-file-invoice',
                'audience': 'staff',
                'order': 3
            }
        )[0]
        
        time_tracking = HelpCategory.objects.get_or_create(
            slug='time-tracking',
            defaults={
                'name': 'Time Tracking',
                'description': 'Track time on projects and bill for hours worked',
                'icon': 'fa-clock',
                'audience': 'staff',
                'order': 4
            }
        )[0]
        
        self.stdout.write(self.style.SUCCESS('✓ Categories created'))
        
        # CLIENT ARTICLES
        self.stdout.write('Creating client help articles...')
        
        HelpArticle.objects.get_or_create(
            slug='getting-started-client-portal',
            defaults={
                'category': client_portal,
                'title': 'Getting Started with Your Client Portal',
                'summary': 'Learn how to access and navigate your client portal dashboard',
                'content': '''
                    <h3>Welcome to Your Client Portal</h3>
                    <p>Your client portal is your central hub for managing your projects with Provost Home Design. Here's what you can do:</p>
                    
                    <h3>Accessing Your Portal</h3>
                    <ol>
                        <li>Visit the portal login page</li>
                        <li>Enter your email address and password</li>
                        <li>Click "Login" to access your dashboard</li>
                    </ol>
                    
                    <h3>Dashboard Overview</h3>
                    <p>Your dashboard shows:</p>
                    <ul>
                        <li><strong>Active Projects:</strong> Current projects in progress</li>
                        <li><strong>Recent Invoices:</strong> Latest billing information</li>
                        <li><strong>Recent Proposals:</strong> New proposals for your review</li>
                        <li><strong>Plan Files:</strong> Access to your design plans</li>
                    </ul>
                    
                    <h3>Navigation Menu</h3>
                    <p>Use the sidebar menu to access:</p>
                    <ul>
                        <li><strong>Dashboard:</strong> Main overview page</li>
                        <li><strong>Projects:</strong> View all your projects</li>
                        <li><strong>Invoices:</strong> See billing history</li>
                        <li><strong>Proposals:</strong> Review and accept proposals</li>
                        <li><strong>Profile:</strong> Update your information</li>
                    </ul>
                ''',
                'audience': 'client',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='how-to-view-pay-invoices',
            defaults={
                'category': invoices_payments,
                'title': 'How to View and Pay Invoices',
                'summary': 'Step-by-step guide to viewing invoices and making payments',
                'content': '''
                    <h3>Viewing Your Invoices</h3>
                    <ol>
                        <li>Click "Invoices" in the sidebar menu</li>
                        <li>You'll see a list of all invoices with their status</li>
                        <li>Click on any invoice to view details</li>
                    </ol>
                    
                    <h3>Understanding Invoice Status</h3>
                    <ul>
                        <li><strong>Draft:</strong> Invoice is being prepared</li>
                        <li><strong>Sent:</strong> Invoice has been sent to you</li>
                        <li><strong>Paid:</strong> Payment has been received</li>
                        <li><strong>Overdue:</strong> Payment is past due date</li>
                    </ul>
                    
                    <h3>Making a Payment</h3>
                    <ol>
                        <li>Open the invoice you want to pay</li>
                        <li>Click the "Pay Now" button</li>
                        <li>Enter your payment information securely</li>
                        <li>Review and confirm the payment</li>
                        <li>You'll receive a confirmation email</li>
                    </ol>
                    
                    <h3>Payment Methods</h3>
                    <p>We accept the following payment methods:</p>
                    <ul>
                        <li>Credit/Debit Cards (Visa, Mastercard, Amex)</li>
                        <li>ACH Bank Transfer</li>
                    </ul>
                ''',
                'audience': 'client',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='accessing-plan-files',
            defaults={
                'category': projects_plans,
                'title': 'Accessing Your Plan Files',
                'summary': 'Learn how to view and download your design plans',
                'content': '''
                    <h3>Finding Your Plans</h3>
                    <p>You can access your plan files in two ways:</p>
                    
                    <h3>Method 1: From Your Project</h3>
                    <ol>
                        <li>Go to "Projects" in the sidebar</li>
                        <li>Click on your project</li>
                        <li>Scroll to the "Plan Files" section</li>
                        <li>Click on any plan file to view or download</li>
                    </ol>
                    
                    <h3>Method 2: From Dashboard</h3>
                    <ol>
                        <li>Your dashboard shows recent plan files</li>
                        <li>Click on any file to access it directly</li>
                    </ol>
                    
                    <h3>Viewing Plan Files</h3>
                    <p>When you click on a plan file:</p>
                    <ul>
                        <li>The file will open in a new window</li>
                        <li>You can view it online or download it</li>
                        <li>Files are stored securely in Dropbox</li>
                    </ul>
                    
                    <h3>Email Notifications</h3>
                    <p>When new plans are uploaded, you may receive an email notification with:</p>
                    <ul>
                        <li>Project name and description</li>
                        <li>Direct download link</li>
                        <li>Link to view in your portal</li>
                    </ul>
                ''',
                'audience': 'client',
                'order': 1
            }
        )
        
        # STAFF ARTICLES
        self.stdout.write('Creating staff help articles...')
        
        HelpArticle.objects.get_or_create(
            slug='staff-portal-overview',
            defaults={
                'category': staff_portal,
                'title': 'Staff Portal Overview',
                'summary': 'Complete overview of staff portal features and capabilities',
                'content': '''
                    <h3>Staff Portal Features</h3>
                    <p>As a staff member, you have access to advanced features for managing clients and projects:</p>
                    
                    <h3>Main Features</h3>
                    <ul>
                        <li><strong>Dashboard:</strong> Overview of recent activity</li>
                        <li><strong>Projects:</strong> Create and manage client projects</li>
                        <li><strong>Clients:</strong> Manage client accounts and information</li>
                        <li><strong>Employees:</strong> Manage staff accounts</li>
                        <li><strong>Invoices:</strong> Create and send invoices</li>
                        <li><strong>Proposals:</strong> Create proposals for clients</li>
                        <li><strong>Time Tracking:</strong> Track time spent on projects</li>
                        <li><strong>Plan Files:</strong> Upload design plans for clients</li>
                        <li><strong>System Settings:</strong> Configure portal settings</li>
                    </ul>
                    
                    <h3>Quick Actions</h3>
                    <p>Common tasks you can perform:</p>
                    <ul>
                        <li>Create new projects for clients</li>
                        <li>Generate invoices and proposals</li>
                        <li>Upload plan files to client projects</li>
                        <li>Track time on projects</li>
                        <li>View client payment history</li>
                        <li>Send email notifications to clients</li>
                    </ul>
                ''',
                'audience': 'staff',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='creating-invoices',
            defaults={
                'category': invoicing,
                'title': 'Creating and Sending Invoices',
                'summary': 'How to create invoices and manage billing for clients',
                'content': '''
                    <h3>Creating a New Invoice</h3>
                    <ol>
                        <li>Click "Create Invoice" in the sidebar</li>
                        <li>Select the client and project</li>
                        <li>Add line items (description, quantity, price)</li>
                        <li>Review the total</li>
                        <li>Save as draft or send immediately</li>
                    </ol>
                    
                    <h3>Adding Line Items</h3>
                    <p>Each line item includes:</p>
                    <ul>
                        <li><strong>Description:</strong> What you're billing for</li>
                        <li><strong>Quantity:</strong> How many units</li>
                        <li><strong>Unit Price:</strong> Price per unit</li>
                        <li><strong>Total:</strong> Calculated automatically</li>
                    </ul>
                    
                    <h3>Adding Time Entries to Invoices</h3>
                    <p>You can bill for tracked time:</p>
                    <ol>
                        <li>Go to Time Tracking</li>
                        <li>Click "Add to Invoice"</li>
                        <li>Select unbilled time entries</li>
                        <li>Set hourly rate</li>
                        <li>Choose existing invoice or create new</li>
                        <li>Time entries are added as line items automatically</li>
                    </ol>
                    
                    <h3>Sending Invoices</h3>
                    <p>Options for sending:</p>
                    <ul>
                        <li><strong>Email:</strong> Send via email notification</li>
                        <li><strong>Portal:</strong> Client can access in their portal</li>
                        <li><strong>Both:</strong> Send email and make available in portal</li>
                    </ul>
                    
                    <h3>Invoice Status</h3>
                    <ul>
                        <li><strong>Draft:</strong> Not yet sent</li>
                        <li><strong>Sent:</strong> Sent to client</li>
                        <li><strong>Paid:</strong> Payment received</li>
                        <li><strong>Overdue:</strong> Past due date</li>
                    </ul>
                ''',
                'audience': 'staff',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='time-tracking-guide',
            defaults={
                'category': time_tracking,
                'title': 'Using the Time Tracking System',
                'summary': 'Track time on projects and convert hours to invoices',
                'content': '''
                    <h3>Starting a Timer</h3>
                    <ol>
                        <li>Go to Time Tracking dashboard</li>
                        <li>Select a project</li>
                        <li>Click "Start Timer"</li>
                        <li>Timer runs in background</li>
                        <li>Click "Stop Timer" when done</li>
                    </ol>
                    
                    <h3>Manual Time Entries</h3>
                    <p>To add time manually:</p>
                    <ol>
                        <li>Click "Add Entry"</li>
                        <li>Select project</li>
                        <li>Enter start and end time</li>
                        <li>Add description of work</li>
                        <li>Mark as billable if needed</li>
                        <li>Save entry</li>
                    </ol>
                    
                    <h3>Viewing Time Entries</h3>
                    <p>Filter entries by:</p>
                    <ul>
                        <li>Project</li>
                        <li>Date range</li>
                        <li>Billable status</li>
                        <li>Invoice status</li>
                    </ul>
                    
                    <h3>Converting Time to Invoices</h3>
                    <ol>
                        <li>Go to Time Entries list</li>
                        <li>Click "Add to Invoice"</li>
                        <li>Select unbilled entries (use checkboxes)</li>
                        <li>Filter by project if needed</li>
                        <li>Enter hourly rate</li>
                        <li>Select existing invoice or create new</li>
                        <li>Click "Add to Invoice"</li>
                        <li>Time entries become invoice line items</li>
                        <li>Entries marked as "Invoiced"</li>
                    </ol>
                    
                    <h3>Project Time Reports</h3>
                    <p>View time summary per project:</p>
                    <ul>
                        <li>Total hours logged</li>
                        <li>Billable vs non-billable time</li>
                        <li>Invoiced vs unbilled time</li>
                        <li>All time entries for project</li>
                    </ul>
                ''',
                'audience': 'staff',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='uploading-plan-files',
            defaults={
                'category': staff_portal,
                'title': 'Uploading Plan Files for Clients',
                'summary': 'How to upload and share design plans with clients',
                'content': '''
                    <h3>Uploading a Plan File</h3>
                    <ol>
                        <li>Click "Upload Plan File" in sidebar</li>
                        <li>Select the client and project</li>
                        <li>Enter a name/description for the file</li>
                        <li>Paste the Dropbox sharing link</li>
                        <li>Optionally check "Send email notification"</li>
                        <li>Click "Upload"</li>
                    </ol>
                    
                    <h3>Getting Dropbox Links</h3>
                    <ol>
                        <li>Upload file to Dropbox</li>
                        <li>Right-click the file</li>
                        <li>Select "Copy Link"</li>
                        <li>Paste link in portal</li>
                    </ol>
                    
                    <h3>Email Notifications</h3>
                    <p>When you check "Send email notification":</p>
                    <ul>
                        <li>Client receives immediate email</li>
                        <li>Email includes direct download link</li>
                        <li>Email links to portal view</li>
                        <li>Includes project information</li>
                    </ul>
                    
                    <h3>Managing Plan Files</h3>
                    <p>Plan files can be:</p>
                    <ul>
                        <li>Viewed by client in portal</li>
                        <li>Downloaded directly</li>
                        <li>Updated or replaced</li>
                        <li>Organized by project</li>
                    </ul>
                ''',
                'audience': 'staff',
                'order': 2
            }
        )
        
        # FAQs
        self.stdout.write('Creating FAQs...')
        
        FAQ.objects.get_or_create(
            question='How do I reset my password?',
            defaults={
                'answer': 'Click the "Forgot Password" link on the login page. Enter your email address and we\'ll send you instructions to reset your password.',
                'category': client_portal,
                'audience': 'both',
                'order': 1
            }
        )
        
        FAQ.objects.get_or_create(
            question='When will I receive my invoice?',
            defaults={
                'answer': 'Invoices are typically sent within 24-48 hours after work is completed. You\'ll receive an email notification when a new invoice is available in your portal.',
                'category': invoices_payments,
                'audience': 'client',
                'order': 2
            }
        )
        
        FAQ.objects.get_or_create(
            question='What payment methods do you accept?',
            defaults={
                'answer': 'We accept credit cards (Visa, Mastercard, American Express) and ACH bank transfers. All payments are processed securely through our payment processor.',
                'category': invoices_payments,
                'audience': 'client',
                'order': 3
            }
        )
        
        FAQ.objects.get_or_create(
            question='How do I view my project status?',
            defaults={
                'answer': 'Click "Projects" in the sidebar to see all your projects. Each project shows its current status, timeline, and any associated files or invoices.',
                'category': projects_plans,
                'audience': 'client',
                'order': 4
            }
        )
        
        FAQ.objects.get_or_create(
            question='Can I download my plan files?',
            defaults={
                'answer': 'Yes! Click on any plan file in your project or dashboard to view or download it. Files are stored securely and you can access them anytime.',
                'category': projects_plans,
                'audience': 'client',
                'order': 5
            }
        )
        
        FAQ.objects.get_or_create(
            question='How do I add a new client?',
            defaults={
                'answer': 'Go to "Manage Clients" and click "Add Client". Fill in the client\'s information including name, email, and contact details. The client will automatically receive login credentials.',
                'category': client_management,
                'audience': 'staff',
                'order': 1
            }
        )
        
        FAQ.objects.get_or_create(
            question='Can I track time on multiple projects?',
            defaults={
                'answer': 'Yes! You can only have one active timer at a time, but you can switch between projects. You can also manually add time entries for multiple projects.',
                'category': time_tracking,
                'audience': 'staff',
                'order': 2
            }
        )
        
        FAQ.objects.get_or_create(
            question='How do I mark an invoice as paid?',
            defaults={
                'answer': 'Open the invoice and click "Record Payment". Enter the payment amount, method, and date. The invoice status will automatically update to "Paid".',
                'category': invoicing,
                'audience': 'staff',
                'order': 3
            }
        )
        
        self.stdout.write(self.style.SUCCESS('✓ Help content created successfully!'))
        self.stdout.write(self.style.SUCCESS(f'Categories: {HelpCategory.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'Articles: {HelpArticle.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'FAQs: {FAQ.objects.count()}'))
