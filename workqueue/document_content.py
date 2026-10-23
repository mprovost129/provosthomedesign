"""Shared, small document projections and formatting; never expose private CRM fields."""
from django.conf import settings
from django.core.exceptions import ValidationError
from .rich_email import EmailHTML

SECTIONS = [('scope', 'Scope of work'), ('deliverables', 'Deliverables'), ('revisions', 'Included revisions'),
            ('exclusions', 'Exclusions'), ('timeline', 'Expected timing'), ('notes', 'Notes'), ('terms', 'Payment and other terms')]


class DocumentHTML(EmailHTML):
    tags = {'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'ul', 'ol', 'li', 'blockquote', 'div'}

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, [])  # No links, images, styles or attributes.

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == 'div':
            self.text.append('\n')


def safe_rich(value):
    parser = DocumentHTML()
    parser.feed(value or '')
    parser.close()
    while parser.stack:
        parser.html.append(f'</{parser.stack.pop()}>')
    return ''.join(parser.html), ''.join(parser.text).strip()


def checked_rich(data):
    supplied = data.get('rich_sections') or {}
    if not isinstance(supplied, dict):
        raise ValidationError('Review the document formatting.')
    result = {}
    for key, _ in SECTIONS:
        value = supplied.get(key, '')
        if not isinstance(value, str) or len(value) > 40000:
            raise ValidationError('A formatted document section is too long.')
        if value:
            html, text = safe_rich(value)
            # A plain-text edit (including an older browser or template) wins
            # over stale rich content. Nothing formatted can hide extra terms.
            if ' '.join(text.split()) == ' '.join(data.get(key, '').split()):
                result[key] = html
    return result


def issuer():
    return {'name': 'Provost Home Design', 'address': getattr(settings, 'CONTACT_ADDRESS', ''),
            'email': getattr(settings, 'CONTACT_EMAIL', ''), 'phone': getattr(settings, 'CONTACT_PHONE', ''),
            'website': 'www.provosthomedesign.com'}


def client_projection(client):
    return {'name': client.name, 'address': '\n'.join(filter(None, [client.billing_street,
        ' '.join(filter(None, [client.billing_city, client.billing_state, client.billing_zip]))]))}


def project_projection(project):
    return {'name': project.name, 'reference': project.canonical_reference or '',
            'address': '\n'.join(filter(None, [project.street, ' '.join(filter(None, [project.city, project.state, project.zip_code]))]))}


def section_rows(snapshot):
    # Re-sanitize even saved content before rendering. Old documents remain plain.
    rich = checked_rich(snapshot)
    return [{'key': key, 'label': label, 'text': snapshot[key], 'html': rich.get(key, '')}
            for key, label in SECTIONS if snapshot.get(key)]
