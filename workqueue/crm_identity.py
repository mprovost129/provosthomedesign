"""Conservative internal CRM matching; never grants client access.

Also used with historical migration models. Keep backfill behavior compatible.
"""
import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def normalized_text(value):
    # These keys only suggest matches. Bound expanded Unicode text to the index field.
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())[:200]


def company_key(value):
    key = normalized_text(value)
    return "" if key in {"", "homeowner", "home owner", "individual", "personal", "none", "n/a", "na", "not applicable"} else key


def phone_key(value):
    number = re.split(r"\b(?:ext\.?|extension|x)\s*|#", (value or "").lower())[0]
    digits = re.sub(r"\D", "", number)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 10 else ""


def email_key(value):
    email = (value or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return None
    return email


def legacy_link_item(item, Client, Contact, *, using="default", fill_blanks=False):
    """Caller holds the queue lock; blank/invalid emails never collapse together."""
    if item.client_contact_id:
        return False
    email = email_key(item.contact_email)
    contact = Contact.objects.using(using).filter(email=email).first() if email else None
    if contact is None:
        company = company_key(item.company)
        name = item.company.strip() if company else item.contact_full_name.strip() or item.reference
        client = Client.objects.using(using).create(name=name, normalized_name=normalized_text(name),
            kind="company" if company else "individual", billing_street=item.billing_street,
            billing_city=item.billing_city, billing_state=item.billing_state, billing_zip=item.billing_zip)
        contact = Contact.objects.using(using).create(client=client, email=email,
            full_name=item.contact_full_name, phone=item.contact_phone, submitted_company=item.company,
            normalized_name=normalized_text(item.contact_full_name), normalized_phone=phone_key(item.contact_phone),
            normalized_company=company)
    elif fill_blanks:
        # Import older incomplete records without replacing any supplied value.
        for field, value in {"full_name": item.contact_full_name, "phone": item.contact_phone,
                             "submitted_company": item.company}.items():
            if not getattr(contact, field) and value:
                setattr(contact, field, value)
        contact.normalized_name = normalized_text(contact.full_name)
        contact.normalized_phone = phone_key(contact.phone)
        contact.normalized_company = company_key(contact.submitted_company)
        contact.save(using=using)
        client = contact.client
        for field in ["billing_street", "billing_city", "billing_state", "billing_zip"]:
            if not getattr(client, field) and getattr(item, field):
                setattr(client, field, getattr(item, field))
        client.save(using=using)
    item.client_contact = contact
    item.save(using=using, update_fields=["client_contact"])
    return True


def backfill(apps, schema_editor):
    using = schema_editor.connection.alias
    State = apps.get_model("workqueue", "QueueState")
    State.objects.using(using).select_for_update().filter(pk=1).first()
    Client = apps.get_model("workqueue", "Client")
    Contact = apps.get_model("workqueue", "ClientContact")
    Item = apps.get_model("workqueue", "WorkItem")
    for item in Item.objects.using(using).filter(client_contact__isnull=True).order_by("received_at", "id").iterator():
        legacy_link_item(item, Client, Contact, using=using, fill_blanks=True)


def matching_contact(Client, Contact, email, company, *, using="default"):
    if not email: return None
    contacts = Contact.objects.using(using).filter(email=email, archived_at__isnull=True,
        client__merged_into__isnull=True).select_related('client')
    key = company_key(company)
    contacts = contacts.filter(client__normalized_name=key, client__kind='company') if key else contacts.filter(client__kind='individual')
    choices = list(contacts[:2])
    return choices[0] if len(choices) == 1 else None


def link_item(item, Client, Contact, *, using="default", fill_blanks=False):
    if item.client_contact_id: return False
    email, company = email_key(item.contact_email), company_key(item.company)
    contact = matching_contact(Client, Contact, email, item.company, using=using)
    if contact is None:
        # Company-name grouping is private CRM organization, never an access grant.
        candidates = list(Client.objects.using(using).filter(normalized_name=company,
            kind='company', merged_into__isnull=True)[:2]) if company else []
        client = candidates[0] if len(candidates) == 1 else Client.objects.using(using).create(
            name=item.company.strip() if company else item.contact_full_name.strip() or item.reference,
            normalized_name=company or normalized_text(item.contact_full_name or item.reference),
            kind='company' if company else 'individual', billing_street=item.billing_street,
            billing_city=item.billing_city, billing_state=item.billing_state, billing_zip=item.billing_zip)
        contact = Contact.objects.using(using).create(client=client,email=email,full_name=item.contact_full_name,
            phone=item.contact_phone,submitted_company=item.company,normalized_name=normalized_text(item.contact_full_name),
            normalized_phone=phone_key(item.contact_phone),normalized_company=company)
    elif fill_blanks:
        for field,value in {'full_name':item.contact_full_name,'phone':item.contact_phone,'submitted_company':item.company}.items():
            if not getattr(contact,field) and value:setattr(contact,field,value)
        contact.normalized_name,contact.normalized_phone=normalized_text(contact.full_name),phone_key(contact.phone)
        contact.normalized_company=company_key(contact.submitted_company);contact.save(using=using)
        client=contact.client
        for field in ['billing_street','billing_city','billing_state','billing_zip']:
            if not getattr(client,field) and getattr(item,field):setattr(client,field,getattr(item,field))
        client.save(using=using)
    item.client_contact=contact;item.save(using=using,update_fields=['client_contact'])
    return True
