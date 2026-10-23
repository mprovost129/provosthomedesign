"""Original PHD letterhead for the document studio. Historical PDFs keep their renderer."""
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from .document_content import section_rows


class PDFRich(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output=[];self.lists=[]

    def handle_starttag(self,tag,attrs):
        mapped={'strong':'b','b':'b','em':'i','i':'i','u':'u'}.get(tag)
        if mapped:self.output.append(f'<{mapped}>')
        elif tag in {'ul','ol'}:self.lists.append(0 if tag=='ol' else None)
        elif tag=='li':
            if self.lists and self.lists[-1] is not None:
                self.lists[-1]+=1;prefix=str(self.lists[-1])+'. '
            else:prefix='&bull; '
            self.output.append('<br/>'+prefix)
        elif tag=='br':self.output.append('<br/>')

    def handle_endtag(self,tag):
        mapped={'strong':'b','b':'b','em':'i','i':'i','u':'u'}.get(tag)
        if mapped:self.output.append(f'</{mapped}>')
        elif tag in {'ul','ol'}:
            if self.lists:self.lists.pop()
        elif tag in {'p','div','blockquote'}:self.output.append('<br/>')

    def handle_data(self,data):
        self.output.append(escape(data))


def document_pdf(snapshot, revision=None, *, invoice_draft=False):
    compact=invoice_draft or snapshot.get('document_kind')=='estimate'
    ink=colors.HexColor('#203b4a');blue=colors.HexColor('#28556b');muted=colors.HexColor('#627781');line=colors.HexColor('#dce4e8');wash=colors.HexColor('#eef3f5')
    styles={
        'body':ParagraphStyle('DocBody',fontName='Helvetica',fontSize=10,leading=15,textColor=ink,spaceAfter=7),
        'small':ParagraphStyle('DocSmall',fontName='Helvetica',fontSize=8,leading=12,textColor=muted,spaceAfter=4),
        'heading':ParagraphStyle('DocHeading',fontName='Helvetica-Bold',fontSize=10,leading=14,textColor=blue,spaceBefore=15,spaceAfter=7,keepWithNext=True),
        'title':ParagraphStyle('DocTitle',fontName='Times-Roman',fontSize=25,leading=29,textColor=ink,spaceBefore=14,spaceAfter=15,keepWithNext=True),
        'type':ParagraphStyle('DocType',fontName='Times-Roman',fontSize=24,leading=28,textColor=ink,alignment=2),
        'brand':ParagraphStyle('DocBrand',fontName='Helvetica-Bold',fontSize=21,leading=23,textColor=blue),
        'right':ParagraphStyle('DocRight',fontName='Helvetica',fontSize=9,leading=14,textColor=ink,alignment=2),
        'money':ParagraphStyle('DocMoney',fontName='Helvetica-Bold',fontSize=20,leading=25,textColor=ink,alignment=2),
    }
    if compact:
        styles['body'].fontSize=9.5;styles['body'].leading=13;styles['body'].spaceAfter=5
        styles['heading'].spaceBefore=10;styles['heading'].spaceAfter=5
        styles['title'].fontSize=21;styles['title'].leading=25;styles['title'].spaceBefore=10;styles['title'].spaceAfter=10
        styles['small'].leading=10
    def p(value,style='body',rich=False):
        value=str(value or '')
        if rich:
            parser=PDFRich();parser.feed(value);parser.close();value=''.join(parser.output)
            value=re.sub(r'(?:<br\s*/?>\s*){3,}','<br/><br/>',value)
        else:
            value=escape(value).replace('\n','<br/>')
        return Paragraph(value,styles[style])
    kind='Change order' if snapshot.get('change_order') else 'Invoice working draft' if invoice_draft else 'Estimate' if snapshot.get('document_kind')=='estimate' else 'Proposal'
    seller=snapshot.get('issuer') or {'name':'Provost Home Design','website':'www.provosthomedesign.com'}
    logo=Path(__file__).resolve().parent.parent/'static/images/phd-document-logo.png'
    brand=[Image(str(logo),width=108 if compact else 135,height=59.6 if compact else 74.5,hAlign='LEFT'),Spacer(1,6),p('\n'.join(filter(None,[seller.get('address'),seller.get('email'),seller.get('phone')])),'small')]
    head=Table([[brand,[p(kind,'type'),Spacer(1,9),p(snapshot['number'],'right'),p(f'Version {revision.sequence}' if revision else 'DRAFT · NOT SENT','right')]]],colWidths=[310,194])
    head.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),17),('LINEBELOW',(0,0),(-1,0),2,blue)]))
    story=[head]
    if invoice_draft:
        warning=Table([[p('WORKING DRAFT · NOT ISSUED · NOT A REQUEST FOR PAYMENT','small')]],colWidths=[504])
        warning.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f8f0dd')),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story += [Spacer(1,12),warning]
    if snapshot.get('document_reference'):
        story += [Spacer(1,9),p(snapshot['document_reference'],'small')]
    story += [p(snapshot['title'],'title')]
    parties=Table([[[p('PREPARED FOR','heading'),p(snapshot['client_name']),p(snapshot.get('billing_address',''))],
                    [p('PROJECT','heading'),p(snapshot['project_name']),p(snapshot.get('project_address',''))]]],colWidths=[252,252])
    parties.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),15)]))
    expiry=('PROPOSED DUE DATE\n'+snapshot.get('due_date','')) if invoice_draft else ('VALID THROUGH\n'+snapshot.get('valid_until',''))
    dates=Table([[p('PREPARED\n'+(snapshot.get('issue_date') or 'See document history'),'small'),p(expiry,'small'),p('PRICING\n'+('Hourly estimate' if snapshot.get('pricing')=='hourly' else 'Fixed price'),'small')]],colWidths=[168]*3)
    dates.setStyle(TableStyle([('LINEABOVE',(0,0),(-1,0),.6,line),('LINEBELOW',(0,0),(-1,0),.6,line),('TOPPADDING',(0,0),(-1,-1),11),('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),0)]))
    story += [parties,Spacer(1,10),dates]
    if snapshot.get('change_order'):
        change=snapshot['change_order']
        story += [p('AGREEMENT PRICING','heading'),p(f"{change['base_number']} · accepted version {change['base_sequence']}",'small')]
        summary=Table([[p(label),p(f"${Decimal(change[key]):,.2f}",'right')] for label,key in [
            ('Original agreement','original_total'),('Previously approved changes','approved_changes'),('This additional price','change_total'),
            ('Revised '+('estimated ' if change['estimated'] else '')+'agreement total (USD)','revised_total')]],colWidths=[354,150])
        summary.setStyle(TableStyle([('BACKGROUND',(0,-1),(-1,-1),wash),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,0),(-1,-1),.4,line)]))
        schedule='Schedule change: '+('Yes' if snapshot.get('schedule_affected')=='yes' else 'No')
        if snapshot.get('additional_days') is not None:schedule+=f" · {snapshot['additional_days']} additional calendar days, estimated"
        if snapshot.get('proposed_completion_date'):schedule+=' · Proposed completion '+snapshot['proposed_completion_date']
        story += [summary,Spacer(1,7),p(schedule,'small'),p('This adds to the referenced agreement. It does not replace its original services and terms or request payment.','small')]
    sections=section_rows(snapshot)
    def section(row):return [p(row['label'].upper(),'heading'),p(row['html'] or row['text'],rich=bool(row['html']))]
    for row in sections:
        if row['key'] not in {'notes','terms'}:story += section(row)
    story += [Spacer(1,16)]
    rows=[[p('SERVICE','small'),p('QTY / HRS','small'),p('RATE','small'),p('AMOUNT','small')]]
    for service in snapshot['lines']:
        rows.append([p(service['description']),p(service['quantity'],'right'),p(f"${Decimal(service['rate']):,.2f}",'right'),p(f"${Decimal(service['amount']):,.2f}",'right')])
    table=Table(rows,colWidths=[270,64,80,90],repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),wash),('LINEBELOW',(0,0),(-1,-1),.4,line),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    total=Table([[p('THIS ADDITIONAL PRICE (USD)' if snapshot.get('change_order') else 'TOTAL (USD)','right'),p(f"${Decimal(snapshot['total']):,.2f}",'money')]],colWidths=[324,180])
    total.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),wash),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    story += [table,total]
    if snapshot.get('pricing')=='hourly':story += [Spacer(1,10),p('Estimated from the listed hours. Review scope and terms for how additional time is handled.','small')]
    for row in sections:
        if row['key'] in {'notes','terms'}:story += section(row)
    if revision and revision.accepted_at:
        story += [p('ACCEPTANCE RECORD','heading'),p(f"Accepted by {revision.accepted_name}\n{revision.recipient}\n{revision.accepted_at:%Y-%m-%d %H:%M} UTC · Version {revision.sequence}")]
    def footer(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(line);canvas.line(54,48,558,48);canvas.setFillColor(muted);canvas.setFont('Helvetica',8)
        canvas.drawString(54,34,'DRAFT · NOT ISSUED' if invoice_draft else 'Provost Home Design | www.provosthomedesign.com')
        canvas.drawRightString(558,34,f'{snapshot["number"]} | Page {doc.page}');canvas.restoreState()
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=letter,leftMargin=54,rightMargin=54,topMargin=42,bottomMargin=64,
        title=snapshot['number']+' - '+snapshot['title'],author='Provost Home Design').build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
