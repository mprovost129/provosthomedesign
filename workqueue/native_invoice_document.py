"""One client-safe presentation for private test-invoice screens and PDFs."""
import copy


def test_invoice_snapshot(invoice):
    snapshot=copy.deepcopy(invoice.snapshot)
    snapshot['notes']='\n'.join(filter(None,[
        'TEST ONLY. Not a request for payment. No money has been charged.',
        f"{invoice.get_stage_display()} billing against {snapshot['document_number']} version {snapshot['draft_version']}.",
        f"Full plan: ${snapshot['plan_total']} (service subtotal ${snapshot['plan_subtotal']}, discount ${snapshot['plan_discount']}, tax {snapshot['tax_percent']}% = ${snapshot['plan_tax']}).",
        'This installment includes its share of the plan discount and tax. See the full plan for the service breakdown.',
        'TEST INVOICE VOIDED.' if invoice.voided_at else '',snapshot.get('notes','')]))
    snapshot['rich_sections']={key:value for key,value in snapshot.get('rich_sections',{}).items() if key!='notes'}
    return snapshot
