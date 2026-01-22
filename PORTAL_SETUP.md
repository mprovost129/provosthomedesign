# Client Portal - Setup & Usage Guide

## Overview

The client portal provides a secure, user-friendly interface for clients to:
- View and manage their invoices
- Pay invoices online with credit cards (Stripe)
- Download PDF invoices
- Update their profile information
- Track payment history

## Features Implemented

### ✅ Authentication System
- User registration with email verification
- Secure login/logout
- Password reset via email
- Session-based authentication (2-week cookie expiry)
- Profile management

### ✅ Invoice Management
- View all invoices with status filtering (Sent, Paid, Overdue)
- Detailed invoice view with line items
- Auto-calculated totals, tax, and balance due
- Invoice status tracking (Draft, Sent, Paid, Overdue, Cancelled)
- Download invoices as PDF

### ✅ Payment Processing
- Stripe integration for credit card payments
- Secure payment page with Stripe Elements
- Real-time payment processing
- Automatic invoice status updates
- Payment history tracking
- Webhook support for payment confirmations

### ✅ Client Dashboard
- Account overview with key metrics:
  - Outstanding balance
  - Total paid to date
  - Pending invoice count
- Recent invoices list
- Recent payments history
- Quick access to payment and invoice actions

### ✅ Admin Interface
- Comprehensive Django admin for staff
- Color-coded status badges
- Inline editing of invoice line items
- Bulk actions (mark sent/paid, recalculate totals)
- Search and filtering capabilities
- Client management with Stripe customer tracking

## Setup Instructions

### 1. Environment Variables

Add the following to your `.env` file:

```env
# Stripe Configuration (REQUIRED for payments)
STRIPE_PUBLISHABLE_KEY=pk_test_your_key_here
STRIPE_SECRET_KEY=sk_test_your_secret_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret

# Company Information (for invoices and emails)
COMPANY_NAME=Provost Home Design
CONTACT_EMAIL=contact@provosthomedesign.com
CONTACT_PHONE=(555) 123-4567
CONTACT_ADDRESS=123 Main St, Your City, ST 12345
```

### 2. Get Stripe API Keys

1. Create a Stripe account at https://stripe.com
2. Get your API keys from https://dashboard.stripe.com/test/apikeys
3. For testing, use test mode keys (start with `pk_test_` and `sk_test_`)
4. Add them to your `.env` file

### 3. Set Up Stripe Webhook

1. Go to https://dashboard.stripe.com/test/webhooks
2. Click "Add endpoint"
3. Enter your webhook URL: `https://yourdomain.com/portal/webhook/stripe/`
4. Select events to listen for:
   - `payment_intent.succeeded`
   - `payment_intent.payment_failed`
5. Copy the webhook secret and add to `.env` as `STRIPE_WEBHOOK_SECRET`

### 4. Database Migration

The migrations have already been applied. If you need to reset:

```bash
python manage.py migrate billing
```

### 5. Create a Test Client

#### Option A: Through Admin Interface
1. Log in to Django admin at `/admin/`
2. Go to "Clients" and click "Add client"
3. Create or select a user
4. Fill in company details
5. Save

#### Option B: Through Registration Page
1. Visit `/portal/register/`
2. Fill in the registration form
3. Submit to create account

### 6. Create Test Invoices

1. Log in to Django admin
2. Go to "Invoices" → "Add invoice"
3. Select the client
4. Add invoice details (dates, status, notes)
5. Add line items using the inline form
6. Save

The invoice will auto-calculate subtotal, tax, and total.

## URL Structure

- `/portal/login/` - Client login
- `/portal/register/` - New client registration
- `/portal/logout/` - Logout
- `/portal/password-reset/` - Password reset request
- `/portal/dashboard/` - Client dashboard (requires login)
- `/portal/profile/` - Edit profile (requires login)
- `/portal/invoices/` - Invoice list (requires login)
- `/portal/invoice/<id>/` - Invoice detail (requires login)
- `/portal/invoice/<id>/pdf/` - Download PDF (requires login)
- `/portal/invoice/<id>/pay/` - Payment page (requires login)
- `/portal/webhook/stripe/` - Stripe webhook endpoint

## Testing Payments

### Test Card Numbers

Use these test cards in the payment form:

| Card Number | Result |
|------------|--------|
| 4242 4242 4242 4242 | Success |
| 4000 0000 0000 0002 | Declined |
| 4000 0025 0000 3155 | 3D Secure required |

- Use any future expiry date (e.g., 12/25)
- Use any 3-digit CVC
- Use any ZIP code

### Payment Flow

1. Client logs in
2. Views invoice with outstanding balance
3. Clicks "Pay Now"
4. Enters credit card details
5. Submits payment
6. Payment processes via Stripe
7. Client redirected to invoice (now marked paid)
8. Payment recorded in history

## Admin Features

### Creating Invoices

1. **Basic Info**: Select client, set dates, choose status
2. **Line Items**: Add products/services with quantity and price
3. **Auto-calculation**: System calculates subtotal, tax, total
4. **Invoice Number**: Auto-generated (format: INV-YYYYMMDD-0001)

### Managing Payments

Payments are automatically created when clients pay online. Admins can also:
- View all payments in the admin
- Filter by status, client, invoice
- Search by payment ID or Stripe ID

### Bulk Actions

- **Mark as Sent**: Update multiple invoices to "Sent" status
- **Mark as Paid**: Mark invoices as fully paid
- **Recalculate Totals**: Refresh calculated fields

## Invoice PDF Generation

PDFs are generated on-demand using ReportLab. They include:
- Company header with contact info
- Client billing address
- Invoice number and dates
- Line items table
- Subtotal, tax, and total
- Payment history
- Notes section

## Email Notifications

Password reset emails are sent automatically. To enable invoice notifications:

1. Configure email backend in `settings.py`
2. Create email templates in `templates/billing/emails/`
3. Add email sending logic to invoice signals

## Security Features

- ✅ CSRF protection on all forms
- ✅ Login required decorators on portal views
- ✅ Secure session cookies (HTTPOnly, SameSite)
- ✅ Password hashing with Django's auth system
- ✅ Stripe's secure payment processing
- ✅ No credit card data stored locally
- ✅ Webhook signature verification

## Database Models

### Client
- OneToOne with Django User
- Company information
- Billing address
- Stripe customer ID

### Invoice
- Auto-generated invoice numbers
- Status workflow (Draft → Sent → Paid/Overdue)
- Automatic total calculation
- Balance due tracking

### InvoiceLineItem
- Links to Invoice (and optionally to Plans)
- Quantity, unit price, description
- Auto-calculates line total

### Payment
- UUID-based payment IDs
- Stripe PaymentIntent integration
- Status tracking
- Automatic invoice updates

## Troubleshooting

### Stripe Payments Not Working

1. Check that Stripe keys are in `.env`
2. Verify keys are test mode (`pk_test_`, `sk_test_`)
3. Check browser console for JavaScript errors
4. Verify Stripe.js is loading

### Invoice Totals Wrong

1. Use "Recalculate Totals" bulk action in admin
2. Check that line items have correct quantities/prices
3. Verify tax rate is set correctly

### Emails Not Sending

1. Check email backend configuration in `settings.py`
2. Verify email credentials in `.env`
3. Check Django logs for errors

## Next Steps

### Recommended Enhancements

1. **Email Notifications**
   - Send invoice notifications when marked "Sent"
   - Payment confirmation emails
   - Overdue payment reminders

2. **Recurring Invoices**
   - Monthly/annual billing schedules
   - Automatic invoice generation
   - Subscription management

3. **Client Communication**
   - In-portal messaging
   - File attachments
   - Project updates

4. **Reporting**
   - Revenue reports
   - Payment analytics
   - Client activity tracking

5. **Multi-Currency Support**
   - International payments
   - Currency conversion

## Support

For questions or issues:
- Check Django admin logs
- Review Stripe dashboard for payment issues
- Check browser console for JavaScript errors
- Review Django error logs

---

**Built with Django 5.2.5, Stripe, and ReportLab**
