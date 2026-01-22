"""
Quick setup script for the client portal.
Creates a test client and sample invoices for development.
"""

import os
import django
from datetime import date, timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User
from django.db.models import Sum
from billing.models import Client, Invoice, InvoiceLineItem

def create_test_data():
    """Create test client and invoices."""
    
    print("🔧 Creating test data for client portal...")
    
    # Create test user
    username = "testclient"
    if User.objects.filter(username=username).exists():
        print(f"✓ Test user '{username}' already exists")
        user = User.objects.get(username=username)
    else:
        user = User.objects.create_user(
            username=username,
            email="testclient@example.com",
            password="TestPass123!",
            first_name="John",
            last_name="Smith"
        )
        print(f"✓ Created test user: {username} / TestPass123!")
    
    # Create or get client profile
    if hasattr(user, 'client_profile'):
        client = user.client_profile
        print(f"✓ Client profile already exists")
    else:
        client = Client.objects.create(
            user=user,
            company_name="Test Company LLC",
            phone="(555) 123-4567",
            address_line1="123 Test Street",
            city="Test City",
            state="TX",
            zip_code="12345"
        )
        print(f"✓ Created client profile for {client}")
    
    # Create sample invoices
    today = date.today()
    
    # Invoice 1: Paid
    if not Invoice.objects.filter(client=client, status='paid').exists():
        inv1 = Invoice.objects.create(
            client=client,
            issue_date=today - timedelta(days=60),
            due_date=today - timedelta(days=30),
            status='paid',
            notes="Thank you for your payment!"
        )
        InvoiceLineItem.objects.create(
            invoice=inv1,
            description="Custom Home Design - Initial Consultation",
            quantity=1,
            unit_price=Decimal('500.00')
        )
        InvoiceLineItem.objects.create(
            invoice=inv1,
            description="Floor Plan Design",
            quantity=1,
            unit_price=Decimal('2500.00')
        )
        inv1.amount_paid = inv1.total
        inv1.save()
        print(f"✓ Created paid invoice: {inv1.invoice_number}")
    
    # Invoice 2: Sent (unpaid)
    if not Invoice.objects.filter(client=client, status='sent').exists():
        inv2 = Invoice.objects.create(
            client=client,
            issue_date=today - timedelta(days=15),
            due_date=today + timedelta(days=15),
            status='sent',
            tax_rate=Decimal('8.25'),
            notes="Payment due within 30 days. Thank you for your business!"
        )
        InvoiceLineItem.objects.create(
            invoice=inv2,
            description="3D Rendering Services",
            quantity=5,
            unit_price=Decimal('350.00')
        )
        InvoiceLineItem.objects.create(
            invoice=inv2,
            description="Revision Fees",
            quantity=2,
            unit_price=Decimal('150.00')
        )
        print(f"✓ Created unpaid invoice: {inv2.invoice_number}")
    
    # Invoice 3: Overdue
    if not Invoice.objects.filter(client=client, status='overdue').exists():
        inv3 = Invoice.objects.create(
            client=client,
            issue_date=today - timedelta(days=45),
            due_date=today - timedelta(days=15),
            status='overdue',
            tax_rate=Decimal('8.25'),
            notes="OVERDUE: Please remit payment immediately."
        )
        InvoiceLineItem.objects.create(
            invoice=inv3,
            description="Construction Documents",
            quantity=1,
            unit_price=Decimal('4500.00')
        )
        print(f"✓ Created overdue invoice: {inv3.invoice_number}")
    
    print("\n" + "="*60)
    print("🎉 Test data created successfully!")
    print("="*60)
    print(f"\n📋 Test Account Details:")
    print(f"   URL: http://127.0.0.1:8000/portal/login/")
    print(f"   Username: {username}")
    print(f"   Password: TestPass123!")
    print(f"\n💼 Client: {client}")
    print(f"   Total Invoices: {client.invoices.count()}")
    
    outstanding = sum(inv.get_balance_due() for inv in client.invoices.exclude(status='paid'))
    print(f"   Outstanding: ${outstanding:.2f}")
    
    print("\n📄 Invoices:")
    for inv in client.invoices.all().order_by('-issue_date'):
        print(f"   • {inv.invoice_number} - {inv.get_status_display()} - ${inv.total} (Due: {inv.due_date})")
    
    print("\n🧪 Testing Stripe Payments:")
    print("   1. Log in with the test account")
    print("   2. Click 'Pay Now' on an unpaid invoice")
    print("   3. Use test card: 4242 4242 4242 4242")
    print("   4. Use any future date, CVC, and ZIP")
    print("\n⚠️  Remember to set up Stripe keys in .env!")
    print("="*60 + "\n")

if __name__ == '__main__':
    create_test_data()
