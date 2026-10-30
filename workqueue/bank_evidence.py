"""Original payout exports and explicitly reviewed bank CSV matches. No money moves."""
import base64
import csv
import hashlib
import io
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone as utc
from pathlib import PureWindowsPath
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .billing_mail import require_owner
from .crm import require_staff
from .freshbooks_mirror import selected
from .freshbooks_mirror_data import signature
from .live_reconciliation import cents, amount, month_start, EASTERN
from .services import lock_queue, EditConflict
from .bank_evidence_models import (PayoutEvidenceImport, LivePayoutEvidence, BankStatementImport,
    BankStatementEntry, LiveDepositMatch)

PAYOUT_HEADERS = ['id','Amount','Created (UTC)','Currency','Livemode','Arrival Date (UTC)','Source Type',
    'Destination','Status','Type','Method','Description','Balance Transaction','Failure Balance Transaction',
    'Failure Message','Failure Code','Statement Descriptor','Trace ID','Trace ID Status','Destination Name',
    'Destination Country','Destination Last 4']
MAX_BYTES = 1024 * 1024
MAX_ROWS = 5000
SALT = 'workqueue.bank-evidence.v1'


def clean_text(value, limit=500):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\r\n\t' for c in value):
        raise ValueError('Unsupported text')
    return value.strip()


def csv_rows(content, encoding='utf-8-sig'):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValidationError('Choose a nonempty CSV up to 1 MB.')
    try:
        if encoding not in ('utf-8-sig', 'cp1252'):
            raise ValueError('Unsupported encoding')
        rows = []
        for row in csv.reader(io.StringIO(content.decode(encoding)), strict=True):
            if len(rows) >= MAX_ROWS + 20 or len(row) > 80:
                raise ValueError('Too many rows or columns')
            rows.append([clean_text(cell, 4000) for cell in row])
        if not rows:
            raise ValueError('Empty file')
        return rows
    except (ValueError, UnicodeError, csv.Error):
        raise ValidationError('The CSV is too large, has unsupported text or is malformed. Use UTF-8 or choose Windows text encoding, and export a shorter period.') from None


def parse_payouts(content, month):
    rows = csv_rows(content)
    if rows[0] != PAYOUT_HEADERS:
        raise ValidationError('Export payouts with All columns (22) from the LIVE FreshBooks-linked Stripe dashboard. Other payout formats are not supported yet.')
    result = []; seen = set(); outside = 0
    try:
        for values in rows[1:]:
            if not values:
                continue
            if len(values) != len(PAYOUT_HEADERS):
                raise ValueError('Incomplete row')
            raw = dict(zip(PAYOUT_HEADERS, values)); identifier = raw['id']
            if not re.fullmatch(r'po_[A-Za-z0-9]{1,150}', identifier) or identifier in seen or raw['Livemode'].casefold() != 'true':
                raise ValueError('Duplicate, invalid or test payout')
            seen.add(identifier)
            created = datetime.strptime(raw['Created (UTC)'], '%Y-%m-%d %H:%M').replace(tzinfo=utc.utc)
            if created>timezone.now()+timedelta(minutes=1):raise ValueError('Future payout')
            arrival = datetime.strptime(raw['Arrival Date (UTC)'], '%Y-%m-%d %H:%M').date()
            code = raw['Currency'].upper(); last4 = raw['Destination Last 4']
            if not re.fullmatch('[A-Z]{3}', code) or not re.fullmatch('[0-9]{4}', last4):
                raise ValueError('Missing currency or destination')
            value = cents(raw['Amount']); status = raw['Status'].lower()
            if created.astimezone(EASTERN).date().replace(day=1) != month:
                outside += 1; continue
            result.append({'id':identifier,'amount':value,'currency':code,'created':created.isoformat(),
                'initiated_date':created.astimezone(EASTERN).date().isoformat(),'arrival_date':arrival.isoformat(),
                'last4':last4,'status':status,'method':clean_text(raw['Method'],40),'type':clean_text(raw['Type'],40),
                'trace':clean_text(raw['Trace ID'],100),'trace_status':clean_text(raw['Trace ID Status'],40)})
    except (ValueError, TypeError, OverflowError):
        raise ValidationError('Payout evidence contains invalid amounts, dates, duplicate IDs or test data. Import an unedited live payout export.') from None
    if not result:
        raise ValidationError('No payouts were initiated in this month in Eastern time. An empty file does not prove a verified zero.')
    return {'schema':1,'rows':result,'outside_period':outside}


def selected_business():
    connection = selected()
    if not connection or not connection.business_id:
        raise ValidationError('Select the FreshBooks business first.')
    return connection


@transaction.atomic
def import_payouts(*, actor, content, filename, month):
    require_owner(actor); require_staff(actor,'workqueue.change_workitem'); lock_queue()
    connection = selected_business(); start = month_start(month); source = parse_payouts(content,start)
    batch, created = PayoutEvidenceImport.objects.get_or_create(account_id=connection.account_id,month=start,
        file_hash=hashlib.sha256(content).hexdigest(), defaults={'business_id':connection.business_id,
            'business_name':connection.business_name,'filename':PureWindowsPath(filename).name[:150],
            'source':source,'source_hash':signature(source),'created_by':actor})
    # Replaying an old file never restores superseded payout facts.
    if not created:
        return batch
    identifiers = set()
    for data in source['rows']:
        identifiers.add(data['id']); stamp = signature(data)
        record, new = LivePayoutEvidence.objects.get_or_create(account_id=connection.account_id,provider_id=data['id'],
            defaults={'business_id':connection.business_id,'source_import':batch,'source':data,'source_hash':stamp})
        if not new:
            if record.business_id != connection.business_id:
                raise ValidationError('The payout business identity needs review.')
            record.version += 1; record.source = data; record.source_hash = stamp; record.source_import = batch; record.missing = False; record.save()
    for record in LivePayoutEvidence.objects.filter(account_id=connection.account_id).exclude(provider_id__in=identifiers):
        if record.source.get('initiated_date','')[:7] == start.strftime('%Y-%m') and not record.missing:
            record.missing = True; record.version += 1; record.save(update_fields=['missing','version'])
    return batch


def token(data, actor):
    connection = selected_business()
    return signing.dumps({'actor':actor.pk,'account':connection.account_id,'business':connection.business_id,**data},salt=SALT,compress=True)


def unpack(value, actor, stage):
    require_owner(actor)
    try:
        if not isinstance(value,str) or len(value)>2*MAX_BYTES:
            raise ValueError('Oversized preview')
        data = signing.loads(value,salt=SALT,max_age=1800); connection = selected_business()
        if (data['actor'],data['account'],data['business'],data['stage']) != (actor.pk,connection.account_id,connection.business_id,stage):
            raise ValueError('Wrong preview context')
        return data
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError('This preview expired, changed or belongs to another account. Start the bank CSV preview again.') from None


def bank_upload(*, actor, content, filename, month, bank_label, bank_last4, currency, encoding, header_row):
    require_owner(actor); require_staff(actor,'workqueue.change_workitem')
    start = month_start(month)
    try:
        label = clean_text(bank_label,60)
        if not label or not re.fullmatch('[0-9]{4}',bank_last4) or not re.fullmatch('[A-Z]{3}',currency):
            raise ValueError('Missing bank identity')
        if type(header_row) is not int or not 1<=header_row<=20:
            raise ValueError('Invalid header row')
        rows = csv_rows(content,encoding); headers = rows[header_row-1]
        if not 2<=len(headers)<=80 or any(not h for h in headers) or len(set(headers))!=len(headers):
            raise ValueError('Select a header row with unique nonempty column names')
    except (ValueError, IndexError):
        raise ValidationError('Choose the correct CSV header row, a bank label, last four account digits and statement currency.') from None
    return token({'stage':'mapping','month':start.isoformat(),'label':label,'last4':bank_last4,'currency':currency,
        'encoding':encoding,'header_row':header_row,'filename':PureWindowsPath(filename).name[:150],
        'file_hash':hashlib.sha256(content).hexdigest(),'content':base64.b64encode(content).decode()},actor)


def uploaded_rows(data):
    return csv_rows(base64.b64decode(data['content'],validate=True),data['encoding'])


def bank_cents(value):
    value = clean_text(value,100)
    if not value:
        return None
    negative = value.startswith('(') and value.endswith(')')
    if negative:
        value = value[1:-1]
    if value.startswith('$'):
        value = value[1:]
    if not re.fullmatch(r'[+-]?(?:[0-9]{1,12}|[0-9]{1,3}(?:,[0-9]{3}){1,3})(?:\.[0-9]{1,8})?',value):
        raise ValueError('Unsupported bank amount')
    if negative and value[0] in '+-':
        raise ValueError('Conflicting amount signs')
    return cents(value.replace(',','').lstrip('+')) * (-1 if negative else 1)


def map_bank(*, actor, payload, mapping):
    data = unpack(payload,actor,'mapping'); rows = uploaded_rows(data); headers = rows[data['header_row']-1]
    positive = []; withdrawals = 0; zeros = 0; outside = 0; seen_ids = Counter()
    formats = {'us':'%m/%d/%Y','iso':'%Y-%m-%d','us_short':'%m/%d/%y'}
    selected_month = date.fromisoformat(data['month']); end_month = (selected_month.replace(day=28)+timedelta(days=4)).replace(day=1)
    def cell(row,key,optional=False):
        index = mapping.get(key)
        if index in ('',None):
            if optional:return ''
            raise ValueError('Missing mapping')
        if type(index) is not int or not 0<=index<len(headers):raise ValueError('Invalid mapping')
        return row[index]
    try:
        if mapping['mode'] not in ('signed','split') or mapping['date_format'] not in formats:
            raise ValueError('Unsupported format')
        required = ['date_column','description_column'] + (['amount_column'] if mapping['mode']=='signed' else ['credit_column','debit_column'])
        if len({mapping.get(k) for k in required}) != len(required):
            raise ValueError('Columns have conflicting roles')
        for line,row in enumerate(rows[data['header_row']:], data['header_row']+1):
            if not row or all(not value for value in row):continue
            if len(row)!=len(headers):raise ValueError('Incomplete row')
            row_currency=cell(row,'currency_column',True).upper()
            if row_currency and row_currency!=data['currency']:raise ValueError('Mixed statement currencies')
            day = datetime.strptime(cell(row,'date_column'),formats[mapping['date_format']]).date()
            if not 2000<=day.year<=2099:raise ValueError('Unsupported year')
            if mapping['mode']=='signed':
                value = bank_cents(cell(row,'amount_column'))
                if value is None:raise ValueError('Missing amount')
            else:
                credit=bank_cents(cell(row,'credit_column'));debit=bank_cents(cell(row,'debit_column'))
                if credit is not None and credit<0 or debit is not None and debit<0 or credit and debit:
                    raise ValueError('Ambiguous split amounts')
                value=(credit or 0)-(debit or 0)
            if not selected_month-timedelta(days=31)<=day<end_month+timedelta(days=31):
                outside+=1;continue
            if value<0:
                withdrawals+=1;continue
            if value==0:
                zeros+=1;continue
            if day>timezone.localdate():raise ValueError('A future deposit is not posted evidence')
            reference = clean_text(cell(row,'reference_column',True),240)
            identifier = clean_text(cell(row,'id_column',True),160)
            description = clean_text(cell(row,'description_column'),500)
            facts={'line':line,'date':day.isoformat(),'amount':value,'currency':data['currency'],
                   'description':description,'reference':reference,'bank_id':identifier}
            identity = signature([data['last4'],data['currency'],'id',identifier]) if identifier else signature([data['last4'],facts['date'],value,reference,description,data['currency']])
            economic = signature([data['last4'],data['currency'],facts['date'],value])
            facts.update(identity=identity,economic=economic);positive.append(facts);seen_ids[identity]+=1
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ValidationError('The selected columns contain an invalid date, amount, missing value or conflicting roles. Review the mapping. Use dot decimals, exact cents, and no totals rows.') from None
    if not positive:
        raise ValidationError('No positive deposits were found in the selected month and adjacent-month window. No verified zero will be saved.')
    for facts in positive:facts['duplicate']=seen_ids[facts['identity']]>1
    source={'schema':1,'rows':positive,'mapping':mapping,'withdrawals_ignored':withdrawals,'zeros_ignored':zeros,'outside_period':outside}
    context={k:data[k] for k in ['month','label','last4','currency','filename','file_hash']}
    return token({'stage':'preview',**context,'source':source},actor)


@transaction.atomic
def save_statement(*, actor, payload, confirm):
    require_owner(actor);require_staff(actor,'workqueue.change_workitem');lock_queue()
    if confirm is not True:raise ValidationError('Confirm the statement identity and mapping before saving.')
    data=unpack(payload,actor,'preview');connection=selected_business();source=data['source'];mapping_hash=signature(source['mapping'])
    batch,new=BankStatementImport.objects.get_or_create(account_id=connection.account_id,bank_last4=data['last4'],currency=data['currency'],
        file_hash=data['file_hash'],mapping_hash=mapping_hash,defaults={'business_id':connection.business_id,'month':date.fromisoformat(data['month']),
            'bank_label':data['label'],'filename':data['filename'],'source':source,'source_hash':signature(source),'created_by':actor})
    if new:
        BankStatementEntry.objects.bulk_create([BankStatementEntry(statement=batch,line_number=row['line'],source=row,
            posted_date=row['date'],amount=row['amount'],source_hash=signature(row),identity_key=row['identity'],
            economic_key=row['economic'],duplicate_in_file=row['duplicate']) for row in source['rows']])
    return batch


def payout_intact(row):
    batch=row.source_import
    return (not row.missing and row.provider_id==row.source.get('id') and signature(row.source)==row.source_hash and signature(batch.source)==batch.source_hash
        and batch.business_id==row.business_id and batch.account_id==row.account_id
        and any(source==row.source for source in batch.source['rows']))


def entry_intact(entry):
    batch=entry.statement
    if (batch.revoked_at or signature(batch.source)!=batch.source_hash or signature(entry.source)!=entry.source_hash or entry.duplicate_in_file
        or entry.source.get('duplicate') or entry.source.get('identity')!=entry.identity_key
        or entry.source.get('economic')!=entry.economic_key or entry.source.get('amount')!=entry.amount
        or entry.source.get('date')!=entry.posted_date.isoformat() or entry.source.get('currency')!=batch.currency):
        return False
    if not any(row==entry.source for row in batch.source['rows']):return False
    aliases=BankStatementEntry.objects.filter(statement__account_id=batch.account_id,statement__revoked_at__isnull=True,identity_key=entry.identity_key)
    # Different mappings may change the source line number; compare financial
    # identity and content without treating line numbers as bank identifiers.
    facts=lambda r:{k:v for k,v in r.items() if k not in ('line','duplicate')}
    return not any(other.duplicate_in_file or facts(other.source)!=facts(entry.source) for other in aliases)


def trace_match(payout,entry):
    text=entry.source['reference']+' '+entry.source['description'];trace=payout.source['trace']
    return bool(trace and re.search(r'(?<![A-Za-z0-9])'+re.escape(trace)+r'(?![A-Za-z0-9])',text)) or payout.provider_id in text.split()


def compatible(payout,entry):
    source=payout.source;statement=entry.statement;data=entry.source
    if (payout.account_id,payout.business_id,source['last4'],source['currency'],source['amount']) != (
            statement.account_id,statement.business_id,statement.bank_last4,statement.currency,data['amount']):return False
    return (source['status']=='paid' and source['type']=='bank_account' and source['amount']>0
        and date.fromisoformat(data['date'])<=timezone.localdate()
        and date.fromisoformat(source['initiated_date'])<=date.fromisoformat(data['date'])<=date.fromisoformat(source['arrival_date'])+timedelta(days=31))


def match_state(record):
    if record.revoked_at:return 'corrected'
    if signature(record.evidence)!=record.evidence_hash:return 'review'
    if (record.bank_identity!=record.bank_entry.identity_key or record.bank_economic_key!=record.bank_entry.economic_key
        or record.account_id!=record.payout.account_id or record.account_id!=record.bank_entry.statement.account_id):return 'review'
    if record.evidence['payout_hash']!=record.payout.source_hash or record.evidence['entry_hash']!=record.bank_entry.source_hash:return 'review'
    return 'confirmed' if payout_intact(record.payout) and entry_intact(record.bank_entry) and compatible(record.payout,record.bank_entry) else 'review'


@transaction.atomic
def confirm_match(*,actor,payout_id,entry_id,version,confirm,note):
    require_owner(actor);require_staff(actor,'workqueue.change_workitem');lock_queue();connection=selected_business()
    payout=LivePayoutEvidence.objects.select_related('source_import').get(pk=payout_id,account_id=connection.account_id,business_id=connection.business_id)
    entry=BankStatementEntry.objects.select_related('statement').get(pk=entry_id,statement__account_id=connection.account_id,statement__business_id=connection.business_id)
    if payout.version!=version:raise EditConflict('This payout changed. Reload and review it before matching.')
    if confirm is not True:raise ValidationError('Review the bank statement and confirm that this deposit belongs to this payout.')
    try:note=clean_text(note,500)
    except ValueError:raise ValidationError('Use a short review note without control characters.') from None
    if not trace_match(payout,entry) and len(note)<12:
        raise ValidationError('No exact payout or trace reference was found. Explain how you verified this deposit; an equal amount alone is not enough.')
    if not payout_intact(payout) or not entry_intact(entry) or not compatible(payout,entry):
        raise ValidationError('The amount, currency, account, date, source integrity or payout status does not match. Resolve the evidence before confirming.')
    active=LiveDepositMatch.objects.filter(account_id=connection.account_id,revoked_at__isnull=True)
    if active.filter(payout=payout).exists() or active.filter(bank_identity=entry.identity_key).exists():
        raise ValidationError('This payout or bank deposit is already matched. Correct the existing match first.')
    for existing in active.filter(bank_economic_key=entry.economic_key).select_related('bank_entry'):
        old=existing.bank_entry.source.get('bank_id');new=entry.source.get('bank_id')
        if not old or not new or old==new:
            raise ValidationError('A deposit for this account, date and amount is already matched. Distinct bank transaction IDs are needed to prove another deposit.')
    evidence={'payout_hash':payout.source_hash,'payout':payout.source,'payout_import':str(payout.source_import_id),
        'entry_hash':entry.source_hash,'bank_entry':entry.source,'statement':str(entry.statement_id),
        'statement_hash':entry.statement.source_hash,'exact_reference':trace_match(payout,entry),'review_note':note}
    record=LiveDepositMatch.objects.create(account_id=connection.account_id,payout=payout,bank_entry=entry,
        bank_identity=entry.identity_key,bank_economic_key=entry.economic_key,evidence=evidence,evidence_hash=signature(evidence),created_by=actor)
    payout.version+=1;payout.save(update_fields=['version']);return record


@transaction.atomic
def undo_match(*,actor,match_id,reason):
    require_owner(actor);require_staff(actor,'workqueue.change_workitem');lock_queue();connection=selected_business()
    record=LiveDepositMatch.objects.select_related('payout').get(pk=match_id,account_id=connection.account_id,payout__business_id=connection.business_id)
    try:reason=clean_text(reason,500)
    except ValueError:raise ValidationError('Enter a short correction reason.') from None
    if not reason:raise ValidationError('Explain the correction; the original history will be retained.')
    if record.revoked_at:return record
    record.revoked_at=timezone.now();record.revoked_by=actor;record.revoke_reason=reason
    record.save(update_fields=['revoked_at','revoked_by','revoke_reason'])
    record.payout.version+=1;record.payout.save(update_fields=['version']);return record


@transaction.atomic
def void_statement(*,actor,statement_id,reason):
    require_owner(actor);require_staff(actor,'workqueue.change_workitem');lock_queue();connection=selected_business()
    record=BankStatementImport.objects.get(pk=statement_id,account_id=connection.account_id,business_id=connection.business_id)
    try:reason=clean_text(reason,500)
    except ValueError:raise ValidationError('Enter a short source correction reason.') from None
    if not reason:raise ValidationError('Explain why this statement import is being excluded.')
    if not record.revoked_at:
        record.revoked_at=timezone.now();record.revoked_by=actor;record.revoke_reason=reason
        record.save(update_fields=['revoked_at','revoked_by','revoke_reason'])
    return record
