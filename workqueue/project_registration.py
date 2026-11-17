"""Read-only provider verification followed by an atomic local registration."""
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from . import dropbox_provider as dropbox, freshbooks as fb
from .dropbox_filing import folder_number, validate_child
from .dropbox_models import DropboxConnection, DropboxFolder, ProjectFolder
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink
from .freshbooks_project_models import FreshBooksProject
from .freshbooks_projects import positive_id, remote_project, projects
from .models import ClientContact, WorkProject, ProjectRegistration
from .pm_models import ProjectEvent
from .services import lock_queue, require_queue_staff

SALT = 'workqueue.register-existing-project.v1'
PROJECT_FIELDS = ('name', 'street', 'city', 'state', 'zip_code')


def require_registration_staff(actor):
    require_queue_staff(actor, 'workqueue.add_workproject')
    require_queue_staff(actor, 'workqueue.change_workitem')
    require_queue_staff(actor, 'workqueue.add_workitem')


def local_identity(contact_id, account):
    contact = ClientContact.objects.select_related('client').filter(
        pk=contact_id, archived_at__isnull=True, client__merged_into__isnull=True).first()
    if not contact:
        raise ValidationError('Choose an active contact on an unmerged CRM client.')
    link = FreshBooksClientLink.objects.filter(client=contact.client, account_id=account).first()
    if not link:
        raise ValidationError('Connect this CRM client to its existing FreshBooks billing client first. Registration never creates a billing client.')
    return contact, {'contact': str(contact.pk), 'client': str(contact.client_id),
        'client_name': contact.client.name, 'client_version': contact.client.version,
        'contact_name': contact.full_name, 'email': contact.email, 'phone': contact.phone,
        'customer': link.customer_id, 'billing_label': link.label}


def verify(data):
    """Verify authoritative remote records, never infer identity from a mirror ID."""
    connection = FreshBooksConnection.objects.filter(pk=1, needs_reconnect=False).first()
    if not connection or not connection.encrypted_tokens or not {'user:projects:read', 'user:clients:read'}.issubset(connection.granted_scopes):
        raise ValidationError('Connect FreshBooks with client and project read access first.')
    account, business = fb.account_id(), positive_id(connection.business_id)
    identity = fb.api('GET', '/auth/api/v1/users/me').get('response', {})
    memberships = identity.get('business_memberships', [])
    if not any(str(row.get('business', {}).get('id')) == str(business)
               and row.get('business', {}).get('account_id') == account
               and row.get('role') in {'owner', 'admin', 'business_partner', 'business_manager'}
               for row in memberships):
        raise ValidationError('The FreshBooks account and business no longer match the connected identity.')
    contact, profile = local_identity(data['contact'], account)
    customer = fb.remote_client(profile['customer'])
    remote = remote_project(business, data['remote_id'])
    if positive_id(remote.get('client_id')) != profile['customer']:
        raise ValidationError('That FreshBooks project belongs to a different billing client.')
    db, root = dropbox.root()
    identity = dropbox.api('users/get_current_account', None, namespace='')
    if (identity.get('account_id') != db.account_id
            or identity.get('root_info', {}).get('root_namespace_id') != db.namespace_id
            or root.get('id') != db.root_id):
        raise ValidationError('The Dropbox account or Projects root changed. Review the connection.')
    entries = [validate_child(entry) for entry in dropbox.children()]
    folder = next((entry for entry in entries if entry['id'] == data['folder']), None)
    if not folder:
        raise ValidationError('Choose an existing folder directly inside the connected Dropbox Projects root.')
    number = folder_number(folder['name'])
    if not number or number.endswith('00') or folder_number(remote.get('title', '')) != number:
        raise ValidationError('Dropbox and FreshBooks must have the same six-digit project number followed by a hyphen. Correct their identities before registering.')
    if sum(folder_number(entry['name']) == number for entry in entries) != 1:
        raise ValidationError('More than one Dropbox folder uses this project number. Resolve the collision first.')
    matches = [row for row in projects(business) if folder_number(row.get('title', '')) == number]
    if len(matches) != 1 or positive_id(matches[0].get('id')) != positive_id(remote['id']):
        raise ValidationError('FreshBooks has a missing or ambiguous project number. Resolve the collision before registering.')
    project = {name: data.get(name, '').strip() for name in PROJECT_FIELDS}
    WorkProject(**project).full_clean()
    return {'input': {**project, 'contact': str(contact.pk), 'folder': folder['id'], 'remote_id': positive_id(remote['id'])},
        'profile': profile, 'account': account, 'business': business, 'business_name': connection.business_name,
        'customer_name': customer.get('organization') or ' '.join(filter(None, [customer.get('fname'), customer.get('lname')])),
        'remote_title': remote['title'], 'dropbox_account': db.account_id, 'dropbox_email': db.account_email,
        'namespace': db.namespace_id, 'root': db.root_id, 'folder_name': folder['name'],
        'folder_path': folder['path_display'], 'number': number}


def existing_project(snapshot):
    data = snapshot['input']
    folder = ProjectFolder.objects.filter(Q(folder_id=data['folder']) | Q(number=snapshot['number'])).first()
    remote = FreshBooksProject.objects.filter(business_id=snapshot['business'], remote_id=data['remote_id']).first()
    canonical = WorkProject.objects.filter(canonical_reference=snapshot['number']).first()
    if not any((folder, remote, canonical)):
        return None
    if (folder and remote and folder.project_id == remote.project_id
            and folder.folder_id == data['folder'] and folder.number == snapshot['number']
            and remote.account_id == snapshot['account'] and remote.customer_id == snapshot['profile']['customer']
            and str(remote.client_id) == snapshot['profile']['client'] and remote.state == 'linked'
            and (not canonical or canonical.pk == folder.project_id)):
        registration = ProjectRegistration.objects.filter(project_id=folder.project_id).first()
        if registration and registration.snapshot == snapshot:
            return folder.project
    raise ValidationError('This project number, Dropbox folder or FreshBooks project is already connected. Open the existing PHD project or resolve the conflicting identity; nothing was registered.')


def preview(*, data, actor):
    require_registration_staff(actor)
    snapshot = verify(data)
    existing = existing_project(snapshot)
    return snapshot, signing.dumps({'actor': actor.pk, 'snapshot': snapshot}, salt=SALT, compress=True), existing


def register(*, signed, actor):
    require_registration_staff(actor)
    try:
        payload = signing.loads(signed, salt=SALT, max_age=900)
    except signing.BadSignature:
        raise ValidationError('This registration review expired or changed. Verify the details again.') from None
    if payload['actor'] != actor.pk:
        raise ValidationError('This review belongs to another staff member. Verify the details again.')
    # Provider reads can rotate OAuth credentials. Commit those refreshes outside
    # the project transaction so a rejected review cannot roll them back.
    snapshot = verify(payload['snapshot']['input'])
    if snapshot != payload['snapshot']:
        raise ValidationError('The client, provider identity or project details changed. Verify and review again.')
    try:
        with transaction.atomic():
            # Same provider lock order as filing; concurrent registrations serialize.
            db = DropboxConnection.objects.select_for_update().get(pk=1)
            connection = FreshBooksConnection.objects.select_for_update().get(pk=1)
            if (not db.enabled or (db.account_id, db.root_id, db.namespace_id) !=
                    (snapshot['dropbox_account'], snapshot['root'], snapshot['namespace'])
                    or connection.needs_reconnect or not connection.encrypted_tokens
                    or (connection.account_id, connection.business_id) != (snapshot['account'], snapshot['business'])
                    or not {'user:projects:read', 'user:clients:read'}.issubset(connection.granted_scopes)):
                raise ValidationError('A provider connection changed during verification. Review again.')
            # Serialize with CRM/queue edits only after remote reads finish. This lock
            # does not allocate a reference, position, or business project number.
            lock_queue()
            contact, profile = local_identity(snapshot['input']['contact'], snapshot['account'])
            if profile != snapshot['profile']:
                raise ValidationError('The CRM contact changed during verification. Review again.')
            existing = existing_project(snapshot)
            if existing:
                return existing, False
            data = snapshot['input']
            project = WorkProject.objects.create(**{name: data[name] for name in PROJECT_FIELDS})
            folder, _ = DropboxFolder.objects.update_or_create(pk=data['folder'], defaults={
                'name': snapshot['folder_name'], 'path': snapshot['folder_path'],
                'number': snapshot['number'], 'available': True})
            ProjectFolder.objects.create(project=project, folder=folder, number=snapshot['number'], intended_name=folder.name)
            FreshBooksProject.objects.create(project=project, client=contact.client,
                account_id=snapshot['account'], business_id=snapshot['business'], customer_id=profile['customer'],
                remote_id=data['remote_id'], title=snapshot['remote_title'], state='linked', phase='project',
                marker='PHD-WEB-PROJECT-' + project.pk.hex, actor=actor)
            ProjectRegistration.objects.create(project=project, contact=contact, snapshot=snapshot, actor=actor)
            ProjectEvent.objects.create(project=project, actor=actor, action='existing_project_registered',
                details={'number': snapshot['number'], 'folder': data['folder'], 'freshbooks_project': data['remote_id'],
                         'contact': str(contact.pk)})
            return project, True
    except IntegrityError:
        raise ValidationError('Another registration claimed this identity. Verify the project again; no partial registration was saved.') from None
    except (DropboxConnection.DoesNotExist, FreshBooksConnection.DoesNotExist):
        raise ValidationError('A provider connection is no longer available. Connect it and review again.') from None
