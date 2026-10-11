from io import BytesIO
from html import escape
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def proposal_pdf(snapshot, revision=None):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Brand', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor('#24526b'), spaceAfter=12))
    styles['BodyText'].leading, styles['BodyText'].spaceAfter = 13, 4
    styles['Heading2'].textColor = colors.HexColor('#24526b')
    styles['Heading2'].spaceBefore = 10
    def text(value, style='BodyText'):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style])
    story = [text('PROVOST HOME DESIGN', 'Brand'), text(snapshot['title'], 'Title'),
             text(f"{snapshot['number']} | Version {revision.sequence if revision else 'DRAFT'} | Valid through {snapshot['valid_until']}"), Spacer(1, 12)]
    table = Table([[text('PREPARED FOR', 'Heading3'), text('PROJECT', 'Heading3')],
        [text(snapshot['client_name']+'\n'+snapshot['billing_address']), text(snapshot['project_name']+'\n'+snapshot['project_address'])]], colWidths=[252, 252])
    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    story += [table]
    for key, label in [('scope','Scope of work'), ('deliverables','Deliverables'), ('revisions','Included revisions'),
                       ('exclusions','Exclusions'), ('timeline','Expected timing')]:
        if snapshot.get(key):
            story += [text(label, 'Heading2'), text(snapshot[key])]
    story += [text('Pricing', 'Heading2'), text('Hourly estimate' if snapshot['pricing']=='hourly' else 'Fixed price')]
    rows = [[text('SERVICE', 'Heading4'), text('QTY / HRS', 'Heading4'), text('RATE', 'Heading4'), text('AMOUNT', 'Heading4')]]
    for line in snapshot['lines']:
        rows.append([text(line['description']), text(line['quantity']), text(f"${Decimal(line['rate']):,.2f}"), text(f"${Decimal(line['amount']):,.2f}")])
    rows.append([text('TOTAL (USD)', 'Heading3'), '', '', text(f"${Decimal(snapshot['total']):,.2f}", 'Heading3')])
    prices = Table(rows, colWidths=[270, 68, 80, 86], repeatRows=1, hAlign='LEFT')
    prices.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf3f7')),
        ('LINEBELOW',(0,-1),(-1,-1),1,colors.HexColor('#24526b')), ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story += [prices, Spacer(1, 12)]
    if snapshot['pricing']=='hourly':
        story += [text('The total is an estimate based on the listed hours. Review the scope and terms for how additional time is handled.')]
    if snapshot.get('terms'):
        story += [text('Payment and other terms', 'Heading2'), text(snapshot['terms'])]
    if revision and revision.accepted_at:
        story += [text('Acceptance record', 'Heading2'), text(f"Accepted by {revision.accepted_name}\nEmail: {revision.recipient}\nAccepted: {revision.accepted_at:%Y-%m-%d %H:%M} UTC\nVersion: {revision.sequence}")]
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#526273'))
        canvas.drawString(54, 34, 'Provost Home Design | provosthomedesign.com')
        canvas.drawRightString(558, 34, f'Page {doc.page}')
        canvas.restoreState()
    SimpleDocTemplate(buffer, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=48, bottomMargin=54,
                      title=snapshot['number']+' - '+snapshot['title'], author='Provost Home Design').build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
