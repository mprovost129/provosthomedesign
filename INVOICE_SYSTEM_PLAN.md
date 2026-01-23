# Invoice System Implementation Plan

## Current State Analysis

✅ **Already Implemented:**
- Invoice model with line items
- Client model with billing info
- Payment model with Stripe integration
- Client portal for viewing invoices
- Basic admin interface

## Architecture Decision: Employee Portal vs Django Admin

### Recommendation: **Enhanced Django Admin** (No separate employee portal needed)

**Why Django Admin is sufficient:**
1. You already have comprehensive admin interface configured
2. Only you (or small team) will create/manage invoices
3. Django admin provides excellent security & permissions
4. Saves development time - focus on customer-facing features
5. Can be customized heavily for your workflow

**We'll enhance Django admin with:**
- Quick invoice creation templates
- One-click email sending
- PDF generation preview
- Payment status dashboard
- Client quick lookup

---

## Implementation Plan

### Phase 1: Invoice Creation & Management (Day 1 - Morning)
**Goal:** Streamline invoice creation in Django admin

**Tasks:**
1. ✅ Invoice model already exists - verify fields
2. Add invoice templates for common services:
   - Custom home design
   - Plan modifications
   - Consultation services
   - Site visit fees
3. Admin enhancements:
   - Add "Clone Invoice" action
   - Add "Create from Template" button
   - Auto-calculate totals on line item changes
   - Add inline preview of invoice

**Files to modify:**
- `billing/admin.py` - Add custom admin actions
- `billing/models.py` - Add template methods
- Create: `billing/invoice_templates.py` - Predefined service templates

---

### Phase 2: PDF Generation (Day 1 - Afternoon)
**Goal:** Generate professional PDF invoices

**Approach:** Use `WeasyPrint` (better than ReportLab for HTML→PDF)

**Features:**
- Professional invoice design matching your brand
- Company logo and branding
- Itemized line items
- Payment terms
- QR code for quick payment link
- Downloadable from both admin and client portal

**Files to create:**
- `templates/billing/invoice_pdf.html` - PDF template
- `billing/pdf_generator.py` - PDF generation logic
- Update `billing/views.py` - Add download endpoint
- Update `billing/admin.py` - Add "Download PDF" button

**Dependencies to add:**
```
weasyprint==60.2
qrcode==7.4.2
Pillow==10.2.0  # Already have this
```

---

### Phase 3: Email System (Day 1 - Evening)
**Goal:** Send invoices via email with payment links

**Features:**
1. **Email Invoice Action (from admin):**
   - Button: "Send Invoice to Client"
   - Attaches PDF
   - Includes payment link
   - Updates status to "sent"
   - Logs sent date

2. **Email Template:**
   - Professional HTML email
   - Invoice summary
   - Prominent "Pay Now" button
   - PDF attachment
   - Payment terms

3. **Automatic Status Updates:**
   - Draft → Sent (when emailed)
   - Sent → Overdue (automated check)
   - Any status → Paid (when payment completes)

**Files to create:**
- `templates/billing/emails/invoice_email.html`
- `templates/billing/emails/invoice_email.txt`
- `billing/email_utils.py` - Email sending logic
- Update `billing/admin.py` - Add send email action
- Update `billing/models.py` - Add sent tracking

---

### Phase 4: Payment Integration (Already Mostly Done)
**Goal:** Seamless payment from emailed invoice

**Current State:** ✅ You already have Stripe integration

**Enhancements needed:**
1. **Unique Payment Link:**
   - Generate secure token for each invoice
   - URL: `/portal/pay/{secure_token}/`
   - No login required (magic link from email)

2. **Payment Page:**
   - Show invoice details
   - Stripe payment form
   - Option to save card (for logged-in users)
   - Receipt upon completion

3. **Webhook Handling:**
   - ✅ Already have webhook endpoint
   - Ensure it marks invoice as paid
   - Sends receipt email

**Files to modify:**
- `billing/models.py` - Add `payment_token` field to Invoice
- `billing/urls.py` - Add token-based payment URL
- `billing/views.py` - Add public payment view
- Update webhook handler to mark invoice paid

---

### Phase 5: Client Portal Enhancements (Day 2 - Morning)
**Goal:** Better invoice viewing experience

**Features:**
1. **Invoice List View:** ✅ Already exists - enhance with:
   - Filter by status
   - Sort by date/amount
   - Visual status badges
   - Quick pay buttons

2. **Invoice Detail View:**
   - Full invoice display
   - Payment history
   - Download PDF button
   - Pay balance button

3. **Payment History:**
   - All past payments
   - Receipts downloadable
   - Applied to which invoice

**Files to modify:**
- `templates/billing/invoice_list.html` - Enhance existing
- `templates/billing/invoice_detail.html` - Enhance existing
- `billing/views.py` - Add filtering/sorting

---

### Phase 6: Automation & Notifications (Day 2 - Afternoon)
**Goal:** Reduce manual work with automation

**Automated Tasks:**
1. **Overdue Invoice Reminders:**
   - Check daily for overdue invoices
   - Send reminder email at 1, 7, 14 days overdue
   - Update status to "overdue"

2. **Payment Confirmation:**
   - Instant email upon payment
   - PDF receipt attached
   - Thank you message

3. **Invoice Aging Report:**
   - Daily summary email to you
   - Unpaid invoices by age
   - Cash flow projection

**Implementation:**
- Django management command: `python manage.py check_overdue_invoices`
- Add to cron/scheduled task on server
- Create: `billing/management/commands/check_overdue_invoices.py`

---

### Phase 7: Reporting Dashboard (Day 2 - Optional)
**Goal:** Financial insights at a glance

**Admin Dashboard Widget:**
- Total outstanding
- Paid this month
- Overdue amount
- Top clients by revenue
- Revenue by service type

**Implementation:**
- Custom admin dashboard view
- Charts using Chart.js
- Exportable reports

---

## Database Schema Updates Needed

### Invoice Model - Add Fields:
```python
# New fields to add:
payment_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
email_sent_date = models.DateTimeField(null=True, blank=True)
email_sent_count = models.PositiveIntegerField(default=0)
reminder_sent_count = models.PositiveIntegerField(default=0)
last_reminder_date = models.DateTimeField(null=True, blank=True)
```

### InvoiceTemplate Model - New:
```python
class InvoiceTemplate(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    default_line_items = models.JSONField()  # Store common line items
    default_terms = models.TextField()
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
```

---

## Email Templates Needed

1. **Invoice Notification** (`invoice_email.html`)
   - Subject: "Invoice #{number} from Provost Home Design"
   - Body: Invoice details + Pay Now button

2. **Payment Confirmation** (`payment_confirmation.html`)
   - Subject: "Payment Received - Invoice #{number}"
   - Body: Thank you + receipt

3. **Overdue Reminder** (`overdue_reminder.html`)
   - Subject: "Reminder: Invoice #{number} is past due"
   - Body: Gentle reminder + payment link

4. **Receipt Email** (`receipt_email.html`)
   - Subject: "Receipt for Payment - Invoice #{number}"
   - Body: Payment details + PDF receipt

---

## URL Structure

```python
# Admin URLs (existing)
/admin/billing/invoice/  # List invoices
/admin/billing/invoice/add/  # Create invoice
/admin/billing/invoice/{id}/change/  # Edit invoice

# Client Portal URLs (existing)
/portal/invoices/  # List client's invoices
/portal/invoices/{id}/  # View invoice detail
/portal/invoices/{id}/pay/  # Payment page (requires login)

# New Public URLs (no login required)
/pay/{token}/  # Magic link payment page
/invoice/{token}/view/  # View invoice without login
/invoice/{token}/download/  # Download PDF without login
```

---

## Workflow Example

### Creating & Sending an Invoice:

1. **You (in Django Admin):**
   - Go to Invoices → Add Invoice
   - Select client (or create new)
   - Add line items (or use template)
   - Set due date
   - Click "Save and Send"

2. **Automated Process:**
   - Invoice saved with status "sent"
   - PDF generated
   - Email sent to client with PDF + payment link
   - Payment token created

3. **Client Receives Email:**
   - Opens email
   - Sees invoice summary
   - Clicks "Pay Now" button
   - Redirects to: `/pay/{token}/`

4. **Client Pays:**
   - No login required
   - Enters card details
   - Stripe processes payment
   - Webhook confirms payment

5. **Automated Completion:**
   - Invoice marked "paid"
   - Receipt email sent to client
   - You get notification
   - Client portal updated

---

## Security Considerations

1. **Payment Tokens:**
   - UUID4 for uniqueness
   - One-time use or time-limited
   - Rate limiting on payment endpoints

2. **Email Verification:**
   - Only send to client's verified email
   - Include security notice in emails

3. **Admin Access:**
   - Only staff users can access invoice admin
   - Permissions: view, add, change, delete
   - Audit log of who sent invoices

4. **Client Portal:**
   - Clients can only view their own invoices
   - Can't modify invoice details
   - Can only pay unpaid invoices

---

## Testing Checklist

- [ ] Create invoice in admin
- [ ] Send invoice email (test mode)
- [ ] Click payment link from email
- [ ] Complete test payment with Stripe
- [ ] Verify invoice marked paid
- [ ] Check receipt email sent
- [ ] Test overdue detection
- [ ] Test reminder emails
- [ ] Download PDF invoice
- [ ] View invoice in client portal
- [ ] Test payment with saved card
- [ ] Test partial payments (if supported)

---

## Dependencies to Install

```bash
pip install weasyprint==60.2
pip install qrcode==7.4.2
pip install python-dateutil==2.8.2  # For date calculations
```

**Note:** WeasyPrint requires system dependencies:
- Windows: Install GTK+ from https://gtk.org/
- Linux: `apt-get install python3-weasyprint`
- Mac: `brew install weasyprint`

---

## Estimated Timeline

**Day 1 (Tomorrow):**
- Morning (2-3 hours): Invoice templates + admin enhancements
- Afternoon (2-3 hours): PDF generation
- Evening (2 hours): Email system

**Day 2:**
- Morning (2 hours): Client portal enhancements  
- Afternoon (2 hours): Automation + testing
- Optional: Reporting dashboard (1-2 hours)

**Total:** ~12-15 hours of development

---

## Alternative: If You Want Separate Employee Portal

If you'd prefer a dedicated employee portal instead of Django admin:

**Pros:**
- Custom interface tailored exactly to your workflow
- Can add features like time tracking, project management
- Better mobile experience
- Client-facing design

**Cons:**
- 2-3x more development time
- Additional maintenance
- Need to duplicate some Django admin features
- More security surface area

**Recommendation:** Start with enhanced Django admin. If you outgrow it, we can build a custom portal later using the same backend models.

---

## Questions to Decide:

1. **Tax Handling:** Do you always charge the same tax rate, or does it vary by state?
2. **Partial Payments:** Should clients be able to pay partial amounts?
3. **Recurring Invoices:** Do you need subscriptions or recurring billing?
4. **Multi-Currency:** Just USD or other currencies?
5. **Late Fees:** Should overdue invoices automatically add late fees?
6. **Deposits/Retainers:** Do you collect deposits before work begins?
7. **Invoice Numbering:** Happy with INV-YYYYMMDD-XXXX format?

---

## Next Steps for Tomorrow:

1. Review this plan
2. Answer the questions above
3. I'll start with Phase 1: Invoice templates and admin enhancements
4. We'll iterate through each phase with testing

Ready to build a professional invoicing system! 🚀
