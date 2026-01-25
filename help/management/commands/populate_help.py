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
        
        # NEW: Expenses Category (Staff Only)
        expenses_category = HelpCategory.objects.get_or_create(
            slug='expense-management',
            defaults={
                'name': 'Expense Management',
                'description': 'Track, manage, and approve business expenses',
                'icon': 'fa-money-bill',
                'audience': 'staff',
                'order': 7
            }
        )[0]
        
        # NEW: Expense Articles
        HelpArticle.objects.get_or_create(
            slug='creating-expenses',
            defaults={
                'category': expenses_category,
                'title': 'Creating & Submitting Expenses',
                'summary': 'How to create and submit expense reports for reimbursement',
                'content': '''
                    <h3>Creating a New Expense</h3>
                    <ol>
                        <li>Navigate to <strong>Billing → Expenses → Create</strong></li>
                        <li>Fill in the expense details:
                            <ul>
                                <li><strong>Description:</strong> What was purchased?</li>
                                <li><strong>Amount:</strong> Total cost (e.g., $127.50)</li>
                                <li><strong>Category:</strong> Type of expense (Office Supplies, Travel, etc.)</li>
                                <li><strong>Date:</strong> When the expense occurred</li>
                            </ul>
                        </li>
                        <li>Optionally add:
                            <ul>
                                <li><strong>Vendor:</strong> Where was it purchased from?</li>
                                <li><strong>Receipt URL:</strong> Link to receipt (Dropbox, Google Drive, etc.)</li>
                                <li><strong>Project:</strong> Link to associated project</li>
                                <li><strong>Tax Category:</strong> For accounting purposes</li>
                                <li><strong>Notes:</strong> Additional details</li>
                            </ul>
                        </li>
                        <li>Click <strong>Save</strong> to submit</li>
                    </ol>
                    
                    <h3>Expense Status</h3>
                    <p>Your expense will have one of these statuses:</p>
                    <ul>
                        <li><strong>Pending:</strong> Awaiting approval</li>
                        <li><strong>Approved:</strong> Approved by manager</li>
                        <li><strong>Rejected:</strong> Needs revision</li>
                        <li><strong>Reimbursed:</strong> Payment processed</li>
                    </ul>
                ''',
                'audience': 'staff',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='approving-expenses',
            defaults={
                'category': expenses_category,
                'title': 'Approving Expenses',
                'summary': 'How to review and approve submitted expenses',
                'content': '''
                    <h3>Reviewing Pending Expenses</h3>
                    <ol>
                        <li>Go to <strong>Billing → Expenses</strong></li>
                        <li>Filter by <strong>Status: Pending</strong> to see expenses needing approval</li>
                        <li>Click on an expense to view full details</li>
                        <li>Check:
                            <ul>
                                <li>Receipt is attached (Dropbox link, etc.)</li>
                                <li>Amount is reasonable for the category</li>
                                <li>Expense is properly categorized</li>
                                <li>Project association is correct (if applicable)</li>
                            </ul>
                        </li>
                    </ol>
                    
                    <h3>Approving or Rejecting</h3>
                    <p>On the expense detail page:</p>
                    <ul>
                        <li>Click <strong>Approve</strong> to accept the expense</li>
                        <li>System records approver and approval date</li>
                        <li>Status changes to <strong>Approved</strong></li>
                        <li>Expense is ready for accounting</li>
                    </ul>
                    
                    <h3>Bulk Actions</h3>
                    <p>In the admin panel, you can:</p>
                    <ul>
                        <li>Select multiple pending expenses</li>
                        <li>Use <strong>Bulk Approve</strong> action</li>
                        <li>All selected expenses approved at once</li>
                    </ul>
                ''',
                'audience': 'staff',
                'is_featured': True,
                'order': 2
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='expense-reports',
            defaults={
                'category': expenses_category,
                'title': 'Viewing Expense Reports & Analytics',
                'summary': 'Generate and analyze expense reports',
                'content': '''
                    <h3>Accessing the Expense Dashboard</h3>
                    <ol>
                        <li>Navigate to <strong>Billing → Expenses → Dashboard</strong></li>
                        <li>View at-a-glance metrics:
                            <ul>
                                <li>Pending approval count and amount</li>
                                <li>Total expenses submitted</li>
                                <li>Current month spending</li>
                            </ul>
                        </li>
                    </ol>
                    
                    <h3>Breakdown by Category</h3>
                    <p>The dashboard shows expense distribution:</p>
                    <ul>
                        <li>Each expense category with total amounts</li>
                        <li>Count of expenses per category</li>
                        <li>Click to drill down and see details</li>
                    </ul>
                    
                    <h3>Monthly Trends</h3>
                    <p>View historical spending:</p>
                    <ul>
                        <li>Last 6 months of expense data</li>
                        <li>Identify spending patterns</li>
                        <li>Budget planning and forecasting</li>
                    </ul>
                    
                    <h3>Tax Deductible Summary</h3>
                    <p>Track tax information:</p>
                    <ul>
                        <li>Total tax-deductible expenses</li>
                        <li>Non-deductible expenses</li>
                        <li>Use for year-end accounting</li>
                    </ul>
                    
                    <h3>Generating Reports</h3>
                    <ol>
                        <li>Go to <strong>Billing → Expenses → Report</strong></li>
                        <li>Select optional filters:
                            <ul>
                                <li>Date range (from/to)</li>
                                <li>Grouping: Category, Project, or Month</li>
                            </ul>
                        </li>
                        <li>View detailed breakdown table</li>
                        <li>Export data if needed (admin)</li>
                    </ol>
                ''',
                'audience': 'staff',
                'order': 3
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='expense-tips',
            defaults={
                'category': expenses_category,
                'title': 'Best Practices for Expense Tracking',
                'summary': 'Tips for accurate and efficient expense management',
                'content': '''
                    <h3>Receipt Storage</h3>
                    <ul>
                        <li>Use Dropbox, Google Drive, or OneDrive for cloud storage</li>
                        <li>Share the link (view-only) in the Receipt URL field</li>
                        <li>Keep receipts organized in folders by category/month</li>
                    </ul>
                    
                    <h3>Categorization Tips</h3>
                    <ul>
                        <li><strong>Office Supplies:</strong> Pens, paper, desk items</li>
                        <li><strong>Travel:</strong> Hotels, flights, mileage</li>
                        <li><strong>Meals:</strong> Client lunches, team meals</li>
                        <li><strong>Equipment:</strong> Software, hardware purchases</li>
                        <li><strong>Utilities:</strong> Internet, phone, subscriptions</li>
                    </ul>
                    
                    <h3>Tax Deductibility</h3>
                    <ul>
                        <li>Mark as "Yes" for business-related expenses</li>
                        <li>Mark as "No" for personal reimbursements</li>
                        <li>Use tax category code if required (e.g., 6500 for supplies)</li>
                    </ul>
                    
                    <h3>Project Linking</h3>
                    <ul>
                        <li>Link expenses to projects for cost tracking</li>
                        <li>Helps with budget analysis per project</li>
                        <li>Useful for client billing and profitability</li>
                    </ul>
                    
                    <h3>Timely Submission</h3>
                    <ul>
                        <li>Submit expenses within 30 days of occurrence</li>
                        <li>Include detailed notes if requested</li>
                        <li>Gather receipt within a few days</li>
                    </ul>
                ''',
                'audience': 'staff',
                'order': 4
            }
        )
        
        # NEW: FAQs for Expenses
        FAQ.objects.get_or_create(
            question='Can I edit an expense after submitting it?',
            defaults={
                'answer': 'You can only edit expenses with "Pending" status. Once approved, rejected, or reimbursed, the expense is locked. Contact your manager if you need to make changes.',
                'category': expenses_category,
                'audience': 'staff',
                'order': 1
            }
        )
        
        FAQ.objects.get_or_create(
            question='How long does expense approval take?',
            defaults={
                'answer': 'Managers typically review and approve expenses within 2-3 business days. For faster approval, include complete information and a valid receipt URL when submitting.',
                'category': expenses_category,
                'audience': 'staff',
                'order': 2
            }
        )
        
        FAQ.objects.get_or_create(
            question='What information should I include in the receipt?',
            defaults={
                'answer': 'Include a link to the original receipt showing date, amount, vendor name, and itemization if possible. Cloud storage links (Dropbox, Google Drive) work perfectly.',
                'category': expenses_category,
                'audience': 'staff',
                'order': 3
            }
        )
        
        # NEW: Reminders Category
        reminders_category = HelpCategory.objects.get_or_create(
            slug='invoice-reminders',
            defaults={
                'name': 'Invoice Reminders',
                'description': 'Automatic reminders for unpaid invoices',
                'icon': 'fa-bell',
                'audience': 'staff',
                'order': 8
            }
        )[0]
        
        HelpArticle.objects.get_or_create(
            slug='how-overdue-reminders-work',
            defaults={
                'category': reminders_category,
                'title': 'How Overdue Invoice Reminders Work',
                'summary': 'Understand automatic reminder emails for unpaid invoices',
                'content': '''
                    <h3>Automated Reminder System</h3>
                    <p>The system automatically sends professional reminder emails to clients when invoices become overdue:</p>
                    
                    <h3>Reminder Schedule</h3>
                    <ul>
                        <li><strong>30 days overdue:</strong> First reminder sent automatically</li>
                        <li><strong>60 days overdue:</strong> Second reminder sent</li>
                        <li><strong>90 days overdue:</strong> Final reminder sent</li>
                    </ul>
                    
                    <h3>What Happens Automatically</h3>
                    <ol>
                        <li>System checks daily for unpaid invoices past due date</li>
                        <li>Identifies invoices at 30/60/90 day thresholds</li>
                        <li>Sends professional HTML email to client</li>
                        <li>Email includes:
                            <ul>
                                <li>Invoice number and amount</li>
                                <li>Days overdue (highlighted)</li>
                                <li>Original due date</li>
                                <li>Company contact information</li>
                                <li>Request for payment</li>
                            </ul>
                        </li>
                        <li>System records that reminder was sent</li>
                        <li>Prevents duplicate emails</li>
                    </ol>
                    
                    <h3>Tracking Reminders</h3>
                    <p>In the invoices list, you can see:</p>
                    <ul>
                        <li><strong>reminder_sent:</strong> Whether reminder was sent</li>
                        <li><strong>last_reminder_date:</strong> When reminder was sent</li>
                        <li><strong>reminder_sent_count:</strong> Number of reminders sent</li>
                    </ul>
                    
                    <h3>Email Content</h3>
                    <p>Reminders are sent from your company email with:</p>
                    <ul>
                        <li>Professional formatting</li>
                        <li>Company name and branding</li>
                        <li>Contact information (phone, email)</li>
                        <li>Payment instructions</li>
                    </ul>
                ''',
                'audience': 'staff',
                'is_featured': True,
                'order': 1
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='configuring-reminders',
            defaults={
                'category': reminders_category,
                'title': 'Configuring Email Reminders',
                'summary': 'Set up automatic reminder emails for your system',
                'content': '''
                    <h3>Email Configuration</h3>
                    <p>To enable reminder emails, configure your email settings:</p>
                    
                    <h3>In settings.py</h3>
                    <pre><code>EMAIL_HOST = 'smtp.gmail.com'  # or your provider
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@example.com'
EMAIL_HOST_PASSWORD = 'your-app-password'
DEFAULT_FROM_EMAIL = 'noreply@example.com'</code></pre>
                    
                    <h3>System Settings Configuration</h3>
                    <ol>
                        <li>Go to Admin → Billing → System Settings</li>
                        <li>Fill in:
                            <ul>
                                <li><strong>Company Name:</strong> Used in email signature</li>
                                <li><strong>Phone Number:</strong> Optional, shown in email</li>
                            </ul>
                        </li>
                        <li>Save settings</li>
                    </ol>
                    
                    <h3>Testing Email Setup</h3>
                    <p>To verify email is working:</p>
                    <pre><code>python manage.py shell
from django.core.mail import send_mail
send_mail('Test', 'Message', 'from@example.com', ['to@example.com'])
# Check inbox for test email</code></pre>
                    
                    <h3>Manual Reminder Testing</h3>
                    <p>To send reminders manually without waiting:</p>
                    <pre><code>python manage.py send_overdue_reminders</code></pre>
                    
                    <p>Or for specific day threshold:</p>
                    <pre><code>python manage.py send_overdue_reminders --days 30</code></pre>
                ''',
                'audience': 'staff',
                'order': 2
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='scheduling-reminders',
            defaults={
                'category': reminders_category,
                'title': 'Scheduling Daily Reminder Emails',
                'summary': 'Set up reminders to run automatically each day',
                'content': '''
                    <h3>Scheduling Reminders (Production)</h3>
                    <p>Schedule reminders to run automatically each day at 8:00 AM:</p>
                    
                    <h3>Option 1: Cron Job (Recommended)</h3>
                    <p><strong>Edit crontab:</strong></p>
                    <pre><code>crontab -e</code></pre>
                    
                    <p><strong>Add this line:</strong></p>
                    <pre><code>0 8 * * * /path/to/venv/bin/python /path/to/manage.py send_overdue_reminders >> /var/log/overdue_reminders.log 2>&1</code></pre>
                    
                    <p>This runs the reminder command:</p>
                    <ul>
                        <li>At 8:00 AM (0 8)</li>
                        <li>Every day (* * *)</li>
                        <li>Logs output to file</li>
                    </ul>
                    
                    <h3>Option 2: APScheduler</h3>
                    <p>For Django-based scheduling (if installed):</p>
                    <pre><code># In settings.py
APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"

# In app's ready() method
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(send_reminders_job, 'cron', hour=8, minute=0)
scheduler.start()</code></pre>
                    
                    <h3>Option 3: Celery Beat (Distributed Systems)</h3>
                    <p>For scaled deployments with Celery:</p>
                    <pre><code>CELERY_BEAT_SCHEDULE = {
    'send-overdue-reminders': {
        'task': 'billing.tasks.send_overdue_reminders_task',
        'schedule': crontab(hour=8, minute=0),
    },
}</code></pre>
                    
                    <h3>Verifying Scheduled Reminders</h3>
                    <ol>
                        <li>Check cron was added: <code>crontab -l</code></li>
                        <li>Monitor logs: <code>tail -f /var/log/overdue_reminders.log</code></li>
                        <li>Check database for reminder_sent flag updates</li>
                        <li>Verify clients receive emails on schedule</li>
                    </ol>
                ''',
                'audience': 'staff',
                'order': 3
            }
        )
        
        HelpArticle.objects.get_or_create(
            slug='reminder-troubleshooting',
            defaults={
                'category': reminders_category,
                'title': 'Troubleshooting Reminder Emails',
                'summary': 'Solve common issues with overdue reminder emails',
                'content': '''
                    <h3>Problem: Reminders Not Being Sent</h3>
                    
                    <h4>Check 1: Email Configuration</h4>
                    <p>Verify email settings in settings.py:</p>
                    <ul>
                        <li>EMAIL_HOST is correct (smtp.gmail.com, etc.)</li>
                        <li>EMAIL_PORT is correct (587 for TLS, 465 for SSL)</li>
                        <li>EMAIL_HOST_USER is your email address</li>
                        <li>EMAIL_HOST_PASSWORD is correct app password</li>
                    </ul>
                    
                    <h4>Check 2: System Settings</h4>
                    <ul>
                        <li>Go to Admin → System Settings</li>
                        <li>Ensure Company Name is filled in</li>
                        <li>Check that admin user has an email address</li>
                    </ul>
                    
                    <h4>Check 3: Invoice Data</h4>
                    <ul>
                        <li>Invoice has client with email address</li>
                        <li>Invoice status is "issued" or "partial" (unpaid)</li>
                        <li>Invoice due_date is exactly 30/60/90 days ago</li>
                    </ul>
                    
                    <h4>Check 4: Run Command Manually</h4>
                    <pre><code>python manage.py send_overdue_reminders</code></pre>
                    <p>Look for error messages in output</p>
                    
                    <h3>Problem: Duplicate Reminders Being Sent</h3>
                    <p>The system prevents duplicates automatically. Check:</p>
                    <ul>
                        <li>reminder_sent flag is working</li>
                        <li>Database was migrated: <code>python manage.py migrate billing</code></li>
                        <li>Invoice.reminder_sent field exists</li>
                    </ul>
                    
                    <h3>Problem: Emails Not Reaching Clients</h3>
                    <ul>
                        <li>Check client email in system (Admin → Clients)</li>
                        <li>Test email delivery: <code>python manage.py shell</code></li>
                        <li>Look for bounces in email service</li>
                        <li>Check spam/junk folders</li>
                        <li>Verify company email is trusted sender</li>
                    </ul>
                    
                    <h3>Testing the System</h3>
                    <p>Create test data and send reminder:</p>
                    <pre><code># In shell
from billing.models import Invoice, Client
from datetime import date, timedelta

client = Client.objects.first()
overdue_date = date.today() - timedelta(days=30)

Invoice.objects.create(
    client=client,
    invoice_number='TEST-001',
    due_date=overdue_date,
    total=1000,
    status='issued'
)

# Run command
python manage.py send_overdue_reminders

# Check result
inv = Invoice.objects.get(invoice_number='TEST-001')
print(inv.reminder_sent)  # Should be True</code></pre>
                ''',
                'audience': 'staff',
                'order': 4
            }
        )
        
        # NEW: FAQs for Reminders
        FAQ.objects.get_or_create(
            question='How do I send reminders without waiting for the scheduled time?',
            defaults={
                'answer': 'Run the command manually: `python manage.py send_overdue_reminders`. This sends all pending reminders immediately. You can also use `--days N` to send reminders for specific day thresholds.',
                'category': reminders_category,
                'audience': 'staff',
                'order': 1
            }
        )
        
        FAQ.objects.get_or_create(
            question='Why are reminders not going out?',
            defaults={
                'answer': 'Check: (1) Email settings configured in settings.py, (2) System settings filled in (Company Name), (3) Client has email address, (4) Invoice is unpaid and past due. Run the command manually to see error messages.',
                'category': reminders_category,
                'audience': 'staff',
                'order': 2
            }
        )
        
        FAQ.objects.get_or_create(
            question='Can I customize the reminder email?',
            defaults={
                'answer': 'The email templates are in templates/billing/email/. You can edit overdue_reminder.html for the HTML version and overdue_reminder.txt for the text version to customize the message and branding.',
                'category': reminders_category,
                'audience': 'staff',
                'order': 3
            }
        )
        
        self.stdout.write(self.style.SUCCESS('✓ Help content created successfully!'))
        self.stdout.write(self.style.SUCCESS(f'Categories: {HelpCategory.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'Articles: {HelpArticle.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'FAQs: {FAQ.objects.count()}'))
