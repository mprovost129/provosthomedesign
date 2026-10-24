"""Original PHD layout for immutable, clearly watermarked sandbox receipts."""
from decimal import Decimal
from html import escape
from io import BytesIO
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image


def receipt_pdf(data):
    ink=colors.HexColor('#203b4a');line=colors.HexColor('#dce4e8');wash=colors.HexColor('#eef3f5')
    body=ParagraphStyle('ReceiptBody',fontName='Helvetica',fontSize=10,leading=15,textColor=ink)
    title=ParagraphStyle('ReceiptTitle',parent=body,fontName='Times-Roman',fontSize=28,leading=33,spaceAfter=12)
    heading=ParagraphStyle('ReceiptHeading',parent=body,fontName='Helvetica-Bold',fontSize=12,leading=17,spaceAfter=8)
    def p(value,style=body):return Paragraph(escape(str(value or '')).replace('\n','<br/>'),style)
    logo=Path(__file__).resolve().parent.parent/'static/images/phd-document-logo.png'
    seller=data['issuer']
    brand=[Image(str(logo),width=135,height=74.5,hAlign='LEFT'),Spacer(1,10),p('\n'.join(filter(None,[seller.get('address'),seller.get('email'),seller.get('phone')])))]
    identity=[p('Test payment receipt',title),p(data['number']),p('Invoice '+data['invoice_number']),p(data['payment_date'])]
    header=Table([[brand,identity]],colWidths=[260,244]);header.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),20),('LINEBELOW',(0,0),(-1,-1),.5,line)]))
    rows=[[p('VERIFIED TEST PAYMENT',heading),p(f"${Decimal(data['amount']):,.2f} USD",heading)],
          [p('Payment method'),p(data['method'])],[p('Payment recorded'),p(data['payment_date'])]]
    summary=Table(rows,colWidths=[290,214]);summary.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),wash),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12),('LINEBELOW',(0,0),(-1,-1),.4,line)]))
    story=[header,Spacer(1,24),p('TEST ONLY - NO REAL MONEY RECEIVED',heading),
        p('This is a receipt for a fictional Stripe sandbox payment. It is not a receipt for real funds and does not replace FreshBooks billing.'),Spacer(1,24),
        p('Billed to',heading),p(data['client_name']),p(data['client_address']),Spacer(1,18),
        p('Project',heading),p(data['project_name']),Spacer(1,24),summary,Spacer(1,24),
        p('Stripe verified the original test payment before this receipt was created. This receipt records that payment as it was received. Later refunds, bank returns or work credits do not rewrite this record; refer to the private invoice for its current test balance.'),
        Spacer(1,18),p('Questions? Contact Provost Home Design using the details above.')]
    def footer(canvas,doc):
        canvas.saveState();canvas.setFillColor(ink);canvas.setFont('Helvetica',8)
        canvas.drawString(54,34,'Provost Home Design | TEST ONLY - NO REAL MONEY RECEIVED')
        canvas.drawRightString(558,34,f'Page {doc.page}');canvas.restoreState()
    stream=BytesIO();SimpleDocTemplate(stream,pagesize=letter,leftMargin=54,rightMargin=54,topMargin=42,bottomMargin=60,
        title='Test payment receipt '+data['number'],author='Provost Home Design').build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
