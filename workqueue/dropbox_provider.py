"""Dropbox OAuth and bounded, private file operations. No sharing or deletion API."""
import json
import secrets
import base64
import hashlib
from datetime import timedelta
from urllib.parse import urlencode
import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .dropbox_models import DropboxConnection

SCOPES = 'account_info.read files.metadata.read files.content.write'
CALLBACK = 'https://www.provosthomedesign.com/work-queue/dropbox/callback/'


class DropboxError(Exception):
    def __init__(self, message='Dropbox is unavailable. Retry after checking the connection.', status=None, detail=''):
        super().__init__(message)
        self.status, self.detail = status, detail


def configured():
    return all(getattr(settings, name, '') for name in (
        'DROPBOX_APP_KEY', 'DROPBOX_APP_SECRET', 'DROPBOX_TOKEN_ENCRYPTION_KEY'))


def cipher():
    try:
        if not configured():
            raise ValueError
        return Fernet(settings.DROPBOX_TOKEN_ENCRYPTION_KEY.encode('ascii'))
    except (ValueError, UnicodeError):
        raise DropboxError('Dropbox connection settings need to be configured.') from None


def authorize(state, verifier):
    cipher()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return 'https://www.dropbox.com/oauth2/authorize?' + urlencode(dict(
        client_id=settings.DROPBOX_APP_KEY, response_type='code', redirect_uri=CALLBACK,
        token_access_type='offline', state=state, scope=SCOPES,
        code_challenge_method='S256', code_challenge=challenge))


def request(url, *, headers=None, body=None, data=None, timeout=(5,20)):
    try:
        response = requests.post(url, headers=headers, json=body, data=data,
                                 timeout=timeout, allow_redirects=False)
    except requests.RequestException:
        raise DropboxError('Dropbox did not confirm the operation. Review before retrying.') from None
    if not 200 <= response.status_code < 300:
        detail = ''
        if response.status_code == 409:
            try:
                detail = str(response.json().get('error_summary', ''))[:100]
            except (ValueError, AttributeError):
                pass
        raise DropboxError('Dropbox needs attention. Check connection, space, permissions, or destination conflicts.',
                           response.status_code, detail)
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError:
        raise DropboxError('Dropbox returned an unreadable response.') from None


def grant(**values):
    return request('https://api.dropboxapi.com/oauth2/token', data={
        'client_id':settings.DROPBOX_APP_KEY, 'client_secret':settings.DROPBOX_APP_SECRET, **values})


def save_tokens(connection, result, refresh=None):
    try:
        access = result['access_token']
        refresh = result.get('refresh_token', refresh)
        seconds = int(result['expires_in'])
        if not access or not refresh or seconds <= 0:
            raise ValueError
        connection.encrypted_tokens = cipher().encrypt(json.dumps({'access':access, 'refresh':refresh}).encode()).decode()
        connection.expires_at = timezone.now()+timedelta(seconds=seconds)
        connection.save()
        return access
    except (KeyError, TypeError, ValueError):
        raise DropboxError('Dropbox authorization was incomplete. Connect again.') from None


@transaction.atomic
def token():
    connection = DropboxConnection.objects.select_for_update().filter(pk=1).first()
    if not connection or not connection.encrypted_tokens:
        raise DropboxError('Connect Dropbox first.')
    try:
        tokens = json.loads(cipher().decrypt(connection.encrypted_tokens.encode()))
        if connection.expires_at and connection.expires_at > timezone.now()+timedelta(minutes=2):
            return tokens['access']
        return save_tokens(connection, grant(grant_type='refresh_token', refresh_token=tokens['refresh']), tokens['refresh'])
    except (InvalidToken, KeyError, ValueError):
        raise DropboxError('Dropbox authorization needs reconnecting.') from None


def api(endpoint, data, *, access=None, namespace=None):
    # Endpoint names are constants supplied only by this module and the filing service.
    allowed = {'users/get_current_account','files/get_metadata','files/list_folder',
               'files/list_folder/continue','files/create_folder_v2'}
    if endpoint not in allowed:
        raise DropboxError('Unsupported Dropbox operation.')
    headers = {'Authorization':'Bearer '+(access or token())}
    if namespace is None:
        connection = DropboxConnection.objects.filter(pk=1).first()
        namespace = connection.namespace_id if connection else ''
    if namespace:
        headers['Dropbox-API-Path-Root'] = json.dumps({'.tag':'root','root':namespace})
    return request('https://api.dropboxapi.com/2/'+endpoint, headers=headers, body=data)


def connect(code, verifier):
    # Do not replace a working connection if its newly authorized account differs.
    result = grant(grant_type='authorization_code', code=code, code_verifier=verifier, redirect_uri=CALLBACK)
    if (not isinstance(result.get('access_token'),str) or not result.get('access_token')
            or not isinstance(result.get('refresh_token'),str) or not result.get('refresh_token')):
        raise DropboxError('Dropbox authorization was incomplete. Connect again.')
    identity = api('users/get_current_account', None, access=result['access_token'], namespace='')
    namespace = identity.get('root_info', {}).get('root_namespace_id', '')
    root = api('files/get_metadata', {'path':'/Projects'}, access=result['access_token'], namespace=namespace)
    if (root.get('.tag') != 'folder' or root.get('path_lower') != '/projects'
            or not str(root.get('id','')).startswith('id:') or not namespace or not identity.get('account_id')):
        raise DropboxError('The authorized Dropbox account needs a Projects folder at its root.')
    with transaction.atomic():
        connection, _ = DropboxConnection.objects.get_or_create(pk=1)
        connection = DropboxConnection.objects.select_for_update().get(pk=1)
        if connection.account_id and (connection.account_id != identity['account_id'] or connection.root_id != root['id']):
            raise DropboxError('This account or Projects folder differs from the connected destination. No connection was changed.')
        connection.account_id = identity['account_id']
        connection.account_email = identity.get('email', '')
        connection.namespace_id = namespace
        connection.root_id = root['id']
        connection.root_path = root['path_display']
        connection.enabled = True
        save_tokens(connection, result)
    return connection


def root():
    connection = DropboxConnection.objects.filter(pk=1, enabled=True).first()
    if not connection or not connection.root_id:
        raise DropboxError('Connect and enable Dropbox before filing documents.')
    metadata = api('files/get_metadata', {'path':connection.root_id})
    if metadata.get('.tag') != 'folder' or metadata.get('path_lower') != '/projects':
        raise DropboxError('The Projects root was moved or renamed. Review the connection before filing.')
    return connection, metadata


def children():
    connection, _ = root()
    result = api('files/list_folder', {'path':connection.root_id, 'recursive':False, 'include_deleted':False})
    entries = []
    for _ in range(100):
        entries.extend(item for item in result.get('entries', []) if item.get('.tag') == 'folder')
        if not result.get('has_more'):
            return entries
        result = api('files/list_folder/continue', {'cursor':result['cursor']})
    raise DropboxError('The Projects folder index is too large. Review its structure.')


def metadata(identifier, missing=False):
    try:
        return api('files/get_metadata', {'path':identifier})
    except DropboxError as exc:
        if missing and exc.status == 409 and exc.detail.startswith('path/not_found'):
            return None
        raise


def create_folder(parent_id, name):
    # Unlike get_metadata/upload, create_folder_v2 accepts a path, not an id-relative path.
    parent = metadata(parent_id)
    path = parent.get('path_lower','')
    if parent.get('.tag')!='folder' or not (path=='/projects' or path.startswith('/projects/')):
        raise DropboxError('Folder creation must remain inside the approved Projects root.')
    result = api('files/create_folder_v2', {'path':parent['path_display']+'/'+name, 'autorename':False})['metadata']
    # This endpoint returns concrete FolderMetadata, which need not include a union tag.
    result['.tag']='folder'
    return result


def upload(parent_id, name, file, content_hash):
    connection, _ = root()
    return request('https://content.dropboxapi.com/2/files/upload',
        headers={'Authorization':'Bearer '+token(), 'Content-Type':'application/octet-stream',
                 'Dropbox-API-Path-Root':json.dumps({'.tag':'root','root':connection.namespace_id}),
                 'Dropbox-API-Arg':json.dumps({'path':parent_id+'/'+name, 'mode':'add',
                     'autorename':False, 'strict_conflict':True, 'content_hash':content_hash}, ensure_ascii=True)}, data=file, timeout=(5,65))
