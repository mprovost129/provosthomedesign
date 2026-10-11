"""Previewed, repeatable contact imports. Ambiguous identities require explicit choices."""
import csv
import io
import json
import re
from difflib import SequenceMatcher
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .crm import changed, require_staff
from .crm_identity import company_key, email_key, normalized_text, phone_key
from .models import Client, ClientContact, ClientImport
from .services import EditConflict, lock_queue

HEADERS = ['Client ID', 'Contact ID', 'Organization', 'First Name', 'Last Name', 'Full Name', 'Email',
           'Phone', 'Address Line 1', 'Address Line 2', 'City', 'Province/State', 'Country', 'Postal Code', 'Notes', 'Role']


def organization_hint(value):
    words = re.findall(r'[\w]+', normalized_text(value))
    suffixes = {'inc', 'incorporated', 'llc', 'ltd', 'limited', 'corp', 'corporation', 'co', 'company'}
    return ' '.join('development' if word == 'dev' else word for word in words if word not in suffixes)


def parse_csv(upload):
    if upload.size > 1024 * 1024:
        raise ValidationError('Use a CSV smaller than 1 MB, with up to 500 contacts.')
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValidationError('Save the CSV as UTF-8 and try again.') from None
    rows = []
    aliases = {'company': 'organization', 'email address': 'email', 'phone number': 'phone',
        'billing street': 'address line 1', 'address': 'address line 1', 'billing city': 'city',
        'state': 'province/state', 'billing state': 'province/state', 'zip': 'postal code',
        'billing zip': 'postal code', 'internal notes': 'notes', 'name': 'full name'}
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        headers = reader.fieldnames or []
        normalized = [aliases.get(h.strip().lower(), h.strip().lower()) for h in headers]
        if len(set(normalized)) != len(normalized):
            raise ValidationError('The CSV contains duplicate column headings.')
        if not set(normalized) & {'organization', 'first name', 'last name', 'full name', 'email'}:
            raise ValidationError('Use the template headings or Organization, First Name, Last Name and Email.')
        unknown = set(normalized) - {h.lower() for h in HEADERS}
        if unknown:
            raise ValidationError('Unrecognized columns: ' + ', '.join(sorted(unknown)) + '. Rename them using the template before importing.')
        for row_number, raw in enumerate(reader, 2):
            if None in raw:
                raise ValidationError(f'Row {row_number} has more values than column headings.')
            row = {key: (raw.get(original) or '').strip() for key, original in zip(normalized, headers)}
            if not any(row.values()):
                continue
            row['full name'] = row.get('full name') or ' '.join(filter(None, [row.get('first name'), row.get('last name')]))
            row['full_name'] = row['full name']
            company = row.get('organization', '')
            row['name'] = company if company_key(company) else row['full name'] or row.get('email', '')
            row['kind'] = 'company' if company_key(company) else 'individual'
            row['billing_street'] = ', '.join(filter(None, [row.get('address line 1'), row.get('address line 2')]))
            row['number'] = row_number
            error = ''
            if not row['name']:
                error = 'A name, organization or email is required.'
            if row.get('email') and not email_key(row['email']):
                error = 'Invalid email. Correct the CSV or skip this row.'
            if row.get('role') and row['role'] not in ClientContact.Role.values:
                error = 'Unknown contact role. Use homeowner, contractor, billing, designer or other.'
            limits = {'name': 200, 'full name': 200, 'organization': 200, 'email': 254, 'phone': 50,
                      'billing_street': 250, 'city': 100, 'province/state': 50, 'postal code': 20, 'notes': 5000}
            if any(len(row.get(key, '')) > maximum for key, maximum in limits.items()):
                error = 'One or more fields exceed their allowed length.'
            for key in ['client id', 'contact id']:
                if row.get(key):
                    try:
                        row[key] = str(uuid.UUID(row[key]))
                    except ValueError:
                        error = f'Invalid {key}.'
            row['email'] = email_key(row.get('email')) or ''
            row['error'] = error
            rows.append(row)
            if len(rows) > 500:
                raise ValidationError('Import no more than 500 contacts at a time.')
    except csv.Error:
        raise ValidationError('The CSV has invalid quoting. Save it again as a CSV.') from None
    if not rows:
        raise ValidationError('The CSV contains no contacts.')
    return rows


def row_matches(row):
    active = Client.objects.filter(merged_into__isnull=True)
    email = row.get('email')
    exact = ClientContact.objects.filter(email=email, archived_at__isnull=True).select_related('client').first() if email else None
    if exact:
        if row.get('client id') and str(exact.client_id) != row['client id']:
            return None, [], 'The email belongs to a different client than Client ID.'
        return exact.client, [], ''
    if row.get('client id'):
        target = active.filter(pk=row['client id']).first()
        return target, [], '' if target else 'Client ID is missing or archived.'
    if row.get('contact id'):
        contact = ClientContact.objects.select_related('client').filter(pk=row['contact id'], archived_at__isnull=True,
            client__merged_into__isnull=True).first()
        if not contact:
            return None, [], 'Contact ID is missing or archived.'
        return contact.client, [], ''
    conditions = Q(normalized_name=normalized_text(row['name']))
    name, phone = normalized_text(row['full name']), phone_key(row.get('phone', ''))
    if name:
        conditions |= Q(contacts__normalized_name=name, contacts__archived_at__isnull=True)
    if phone:
        conditions |= Q(contacts__normalized_phone=phone, contacts__archived_at__isnull=True)
    if email:
        conditions |= Q(contacts__archived_email=email)
    candidates = list(active.filter(conditions).distinct())
    organization = organization_hint(row.get('organization', ''))
    if len(organization) >= 6:
        existing = {client.pk for client in candidates}
        for client in active:
            name = organization_hint(client.name)
            if len(name) >= 6 and (organization.startswith(name) or name.startswith(organization)
                    or SequenceMatcher(None, name, organization).ratio() >= .82):
                if client.pk not in existing:
                    candidates.append(client)
                    existing.add(client.pk)
    return None, candidates, ''


def build_plan(rows):
    plan, seen, companies = [], {}, {}
    for index, row in enumerate(rows):
        entry = {'row': index, 'number': row['number'], 'name': row['name'], 'email': row.get('email', ''),
                 'error': row['error'], 'target': '', 'version': None, 'candidates': [], 'duplicate': None, 'group': None}
        if not entry['error']:
            exact, candidates, error = row_matches(row)
            entry['error'] = error
            if exact:
                entry.update(target=str(exact.pk), version=exact.version, status='Update existing client')
            else:
                entry['candidates'] = [{'id': str(client.pk), 'name': client.name, 'version': client.version} for client in candidates]
                entry['status'] = 'Review possible match' if candidates else 'Add new client'
            key = ('email', row['email']) if row['email'] else ('row', json.dumps(
                {key: value for key, value in row.items() if key not in {'number', 'error'}}, sort_keys=True))
            if key in seen:
                entry.update(duplicate=seen[key], status='Repeated contact in this file')
            else:
                seen[key] = index
            company = company_key(row.get('organization'))
            if company and not exact and not candidates and entry['duplicate'] is None:
                if company in companies:
                    entry['group'] = companies[company]
                    entry['status'] = 'Additional contact at the same organization'
                else:
                    companies[company] = index
        if entry['error']:
            entry['status'] = 'Cannot import'
        plan.append(entry)
    return plan


@transaction.atomic
def apply_import(batch_id, choices, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    batch = ClientImport.objects.select_for_update().get(pk=batch_id, actor=actor)
    if batch.applied_at:
        return batch.result
    if batch.created_at < timezone.now() - timedelta(hours=2):
        raise ValidationError('This preview expired. Upload the CSV again.')
    resolved, created_ids, summary = {}, set(), {'new_clients': 0, 'new_contacts': 0, 'updated': 0, 'skipped': 0}
    for index, (row, entry) in enumerate(zip(batch.rows, batch.plan)):
        choice = choices.get(str(index), '')
        if choice == 'skip' or row['error'] or entry['error']:
            summary['skipped'] += 1
            continue
        if entry['duplicate'] is not None or entry['group'] is not None:
            target = resolved.get(entry['duplicate'] if entry['duplicate'] is not None else entry['group'])
            if not target:
                summary['skipped'] += 1
                continue
        else:
            exact, candidates, error = row_matches(row)
            candidates = [candidate for candidate in candidates if candidate.pk not in created_ids]
            if error:
                raise EditConflict(f'Row {row["number"]} changed. Upload a new preview.')
            if entry['target']:
                if not exact or str(exact.pk) != entry['target'] or exact.version != entry['version']:
                    raise EditConflict('A matched client changed. Upload a new preview before importing.')
                target = exact
            elif exact:
                raise EditConflict('An email was added after this preview. Upload the CSV again to match it safely.')
            elif entry['candidates']:
                allowed = {candidate['id']: candidate for candidate in entry['candidates']}
                if choice in allowed:
                    target = Client.objects.filter(pk=choice, merged_into__isnull=True).first()
                    if not target or target.version != allowed[choice]['version']:
                        raise EditConflict('A possible match changed. Upload a new preview.')
                elif choice == 'new':
                    target = None
                else:
                    raise ValidationError(f'Review row {row["number"]}: select an existing client, add as new, or skip.')
            else:
                if candidates:
                    raise EditConflict('A possible match was added since the preview. Upload the CSV again.')
                target = None
        # Versions bumped by earlier rows in the same batch are still ours, not concurrent edits.
        if target is None:
            target = Client.objects.create(name=row['name'], normalized_name=normalized_text(row['name']), kind=row['kind'])
            created_ids.add(target.pk)
            summary['new_clients'] += 1
        contact = target.contacts.filter(email=row['email'], archived_at__isnull=True).first() if row['email'] else None
        if not contact and row.get('contact id'):
            contact = target.contacts.filter(pk=row['contact id'], archived_at__isnull=True).first()
            if not contact:
                raise ValidationError(f'Row {row["number"]}: Contact ID does not belong to the selected client.')
        if not contact:
            candidates = target.contacts.filter(archived_at__isnull=True, normalized_name=normalized_text(row['full name']))
            if row.get('phone'):
                candidates = candidates.filter(Q(normalized_phone=phone_key(row['phone'])) | Q(phone=''))
            if candidates.count() == 1:
                candidate = candidates.first()
                if not candidate.email or candidate.email == row['email'] or not row['email']:
                    contact = candidate
        created = contact is None
        if created:
            contact = ClientContact(client=target)
            summary['new_contacts'] += 1
        changes = {}
        for field, value in {'full_name': row['full name'], 'phone': row.get('phone', ''), 'email': row.get('email') or None,
                'submitted_company': row.get('organization', ''), 'role': row.get('role', '')}.items():
            if value and not getattr(contact, field):
                setattr(contact, field, value)
                changes['contact_' + field] = value
        contact.normalized_name, contact.normalized_phone = normalized_text(contact.full_name), phone_key(contact.phone)
        contact.normalized_company = company_key(contact.submitted_company)
        contact.full_clean()
        contact.save()
        for field, value in {'billing_street': row['billing_street'], 'billing_city': row.get('city', ''),
                'billing_state': row.get('province/state', ''), 'billing_zip': row.get('postal code', '')}.items():
            if value and not getattr(target, field):
                setattr(target, field, value)
                changes[field] = value
        note = row.get('notes', '')
        country = row.get('country', '')
        if country and country.lower() not in {'united states', 'us', 'usa'}:
            note += f'\nCountry: {country}'
        if note.strip() and note.strip() not in target.internal_notes:
            target.internal_notes = '\n\n'.join(filter(None, [target.internal_notes, note.strip()]))
            changes['notes_added'] = True
        # Delay version bumps until the whole batch is matched so repeated company contacts stay consistent.
        target.full_clean()
        target.save()
        if changes or created:
            from .models import ClientEvent
            ClientEvent.objects.create(client=target, actor=actor, action='Contact imported from CSV',
                                       changes={'batch': str(batch.pk), 'row': row['number'], **changes})
            summary['updated'] += int(not created)
        resolved[index] = target
    for client_id in {target.pk for target in resolved.values()}:
        target = Client.objects.get(pk=client_id)
        target.version += 1
        target.save(update_fields=['version', 'updated_at'])
    batch.applied_at, batch.result = timezone.now(), summary
    batch.save(update_fields=['applied_at', 'result'])
    return summary


def safe_cell(value):
    value = str(value or '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def export_csv(template=False):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    if not template:
        for client in Client.objects.filter(merged_into__isnull=True).prefetch_related('contacts'):
            contacts = [contact for contact in client.contacts.all() if not contact.archived_at] or [None]
            for contact in contacts:
                writer.writerow([safe_cell(value) for value in [client.pk, contact.pk if contact else '',
                    client.name if client.kind == 'company' else '', '', '', contact.full_name if contact else client.name,
                    contact.email if contact else '', contact.phone if contact else '', client.billing_street, '',
                    client.billing_city, client.billing_state, '', client.billing_zip, client.internal_notes,
                    contact.role if contact else '']])
    return '\ufeff' + output.getvalue()
