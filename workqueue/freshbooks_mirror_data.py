"""Allowlisted FreshBooks fields. Provider totals are preserved, never repriced."""
import hashlib
import json
import re
from datetime import date
from decimal import Decimal,InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.html import strip_tags


def signature(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def number(value,optional=False):
    if optional and value in (None,'',0,'0'):return None
    if type(value) is int and 0<value<10**15:return value
    if isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{0,14}',value):return int(value)
    raise ValueError('Invalid source identifier')


def text(value,maximum=500):
    if value is None:return ''
    if not isinstance(value,str) or len(value)>maximum:raise ValueError('Invalid source text')
    return strip_tags(value).strip()


def decimal(value,places=2):
    if isinstance(value,(bool,float)) or not isinstance(value,(str,int,Decimal)):raise ValueError('Invalid source money')
    try:amount=Decimal(value)
    except (InvalidOperation,ValueError):raise ValueError('Invalid source money') from None
    if not amount.is_finite() or abs(amount)>Decimal('999999999999.99') or amount!=amount.quantize(Decimal(1).scaleb(-places)):
        raise ValueError('Invalid source precision')
    return str(amount)


def currency(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Z]{3}',value):raise ValueError('Invalid currency')
    return value


def money(value,code,optional=False):
    if optional and value is None:return None
    if not isinstance(value,dict) or value.get('code')!=code:raise ValueError('Invalid money currency')
    return decimal(value.get('amount'))


def day(value,optional=False):
    if optional and value in (None,''):return ''
    if not isinstance(value,str):raise ValueError('Invalid date')
    return date.fromisoformat(value).isoformat()


def account(raw,account_id):
    if raw.get('accounting_systemid')!=account_id:raise ValueError('Wrong source account')
    if raw.get('accountid') is not None and raw['accountid']!=account_id:raise ValueError('Wrong source account')


def visibility(raw):
    value=raw.get('vis_state')
    if type(value) is not int or value not in (0,1,2):raise ValueError('Invalid visibility')
    return value


def billing_address(raw,client=False):
    names=['p_street','p_street2','p_city','p_province','p_code','p_country'] if client else ['street','street2','city','province','code','country']
    return {key:text(raw.get(name),1000 if key.startswith('street') else 200)
        for key,name in zip(['street','street2','city','state','zip','country'],names)}


def client(raw,account_id):
    account(raw,account_id);identifier=number(raw.get('id'))
    if raw.get('userid') not in (None,0,identifier):raise ValueError('Conflicting client ID')
    org=text(raw.get('organization'));full=' '.join(filter(None,[text(raw.get('fname')),text(raw.get('lname'))]))
    email=text(raw.get('email'),254).lower();invalid_email=False
    if email:
        try:validate_email(email)
        except ValidationError:email='';invalid_email=True
    return {'provider_id':identifier,'customer_id':identifier,'title':org or full or f'FreshBooks client {identifier}',
        'organization':org,'full_name':full,'email':email,'invalid_email':invalid_email,
        'phone':text(raw.get('bus_phone') or raw.get('mob_phone') or raw.get('home_phone'),100),
        'address':billing_address(raw,True),'visibility':visibility(raw),'updated':text(raw.get('updated'),100)}


def project(raw,account_id):
    active=raw.get('active');complete=raw.get('complete')
    if type(active) is not bool or type(complete) is not bool:raise ValueError('Invalid project state')
    pricing=text(raw.get('project_type'),60)
    if pricing not in ('fixed_price','hourly_rate'):raise ValueError('Invalid project pricing')
    return {'provider_id':number(raw.get('id')),'customer_id':number(raw.get('client_id'),True),
        'title':text(raw.get('title')),'description':text(raw.get('description'),50000),
        'active':active,'complete':complete,'pricing':pricing,
        'fixed_price':decimal(raw['fixed_price']) if raw.get('fixed_price') is not None else None,
        'hourly_rate':decimal(raw['rate'],4) if raw.get('rate') is not None else None,
        'due_date':day(raw.get('due_date'),True),'updated':text(raw.get('updated_at'),100)}


def lines(raw,code):
    values=raw.get('lines')
    if not isinstance(values,list) or len(values)>1000:raise ValueError('Incomplete source lines')
    result=[]
    for line in values:
        if not isinstance(line,dict):raise ValueError('Invalid line')
        item={'name':text(line.get('name')),'description':text(line.get('description'),20000),
            'quantity':decimal(line.get('qty'),8),'rate':money_rate(line.get('unit_cost'),code),
            'amount':money(line.get('amount'),code),'project_id':number(line.get('modern_project_id'),True)}
        for index in (1,2):
            item['tax_name'+str(index)]=text(line.get('taxName'+str(index)),200)
            value=line.get('taxAmount'+str(index))
            item['tax_rate'+str(index)]=decimal(value,8) if value not in (None,'') else ''
        result.append(item)
    return result


def money_rate(value,code):
    if not isinstance(value,dict) or value.get('code')!=code:raise ValueError('Invalid unit cost')
    return decimal(value.get('amount'),8)


def document(raw,account_id,kind):
    account(raw,account_id);identifier=number(raw.get('id'))
    if number(raw.get('invoiceid' if kind=='invoices' else 'estimateid'))!=identifier:raise ValueError('Conflicting document ID')
    code=currency(raw.get('currency_code'))
    label=text(raw.get('invoice_number' if kind=='invoices' else 'estimate_number'),100)
    state=text(raw.get('v3_status') or raw.get('display_status') or raw.get('ui_status'),100)
    if not label or not state:raise ValueError('Incomplete document identity')
    data={'provider_id':identifier,'customer_id':number(raw.get('customerid')),'title':label,
        'currency':code,'amount':money(raw.get('amount'),code),'status':state,'visibility':visibility(raw),
        'issue_date':day(raw.get('create_date')),'due_date':day(raw.get('due_date'),True),
        'client_name':text(raw.get('organization') or raw.get('current_organization')),
        'address':billing_address(raw),'lines':lines(raw,code),
        'discount_total':money(raw.get('discount_total'),code,True),
        'discount_value':decimal(raw['discount_value'],8) if raw.get('discount_value') not in (None,'') else '',
        'terms':text(raw.get('terms'),50000),'notes':text(raw.get('notes'),50000),
        'description':text(raw.get('description'),50000),'po_number':text(raw.get('po_number'),500),
        'updated':text(raw.get('updated'),100)}
    if kind=='invoices':
        data.update(paid=money(raw.get('paid'),code),outstanding=money(raw.get('outstanding'),code),
            estimate_id=number(raw.get('estimateid'),True))
    return data


def payment(raw,account_id):
    account(raw,account_id);identifier=number(raw.get('id'))
    if raw.get('logid') is not None and number(raw['logid'])!=identifier:raise ValueError('Conflicting payment ID')
    if type(raw.get('from_credit')) is not bool:raise ValueError('Invalid payment classification')
    code=currency(raw.get('amount',{}).get('code'))
    method=text(raw.get('type'),100)
    return {'provider_id':identifier,'customer_id':number(raw.get('clientid'),True),
        'provider_invoice_id':number(raw.get('invoiceid'),True),'title':f'Payment {identifier}',
        'amount':money(raw.get('amount'),code),'currency':code,'date':day(raw.get('date')),
        'method':method,'from_credit':raw['from_credit'] or bool(raw.get('creditid')) or method.lower()=='credit',
        'gateway':text(raw.get('gateway'),100),'visibility':visibility(raw),'updated':text(raw.get('updated'),100)}


def normalize(raw,account_id,kind):
    if not isinstance(raw,dict):raise ValueError('Invalid record')
    if kind=='clients':return client(raw,account_id)
    if kind=='projects':return project(raw,account_id)
    if kind=='payments':return payment(raw,account_id)
    if kind in ('invoices','estimates'):return document(raw,account_id,kind)
    raise ValueError('Unknown resource')
