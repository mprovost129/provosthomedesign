"""Original PHD statement layout; no private CRM notes or payment identifiers."""
from html import escape
from io import BytesIO
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from django.utils import timezone


def statement_pdf(statement):
    ink = colors.HexColor('#203b4a'); blue = colors.HexColor('#28556b')
    line = colors.HexColor('#dce4e8'); wash = colors.HexColor('#eef3f5')
    body = ParagraphStyle('StatementBody', fontName='Helvetica', fontSize=8.5, leading=12, textColor=ink)
    title = ParagraphStyle('StatementTitle', parent=body, fontName='Times-Roman', fontSize=25, leading=29, spaceAfter=14)
    heading = ParagraphStyle('StatementHeading', parent=body, fontName='Helvetica-Bold', fontSize=11, leading=16, spaceBefore=12, spaceAfter=6, keepWithNext=True)
    right = ParagraphStyle('StatementMoney', parent=body, alignment=2)
    def p(value, style=body):
        return Paragraph(escape(str(value or '')).replace('\n', '<br/>'), style)
    def money(value):
        return p(('-' if value < 0 else '')+f'${abs(value):,.2f}', right)
    def table(rows, widths, header=True):
        result = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT')
        style = [('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.4,line),
                 ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]
        if header: style.append(('BACKGROUND',(0,0),(-1,0),wash))
        result.setStyle(TableStyle(style))
        return result
    seller = statement['issuer']
    logo = Path(__file__).resolve().parent.parent/'static/images/phd-document-logo.png'
    brand = [Image(str(logo), width=108, height=59.6, hAlign='LEFT'), Spacer(1,8),
             p('\n'.join(filter(None,[seller.get('address'),seller.get('email'),seller.get('phone')])))]
    identity = [p('Customer statement',title), p(statement['client']['name']), p(statement['client']['address']), Spacer(1,10),
             p('Generated '+timezone.localtime(statement['generated_at']).strftime('%b %d, %Y %I:%M %p %Z'))]
    story = [table([[brand,identity]], [244,260], False),
             p('TEST ONLY - NOT A REQUEST FOR PAYMENT',heading),
             Spacer(1,8), p('Current test records only. FreshBooks invoices and balances are excluded. '
                 'This is not a historical statement. Activity uses effective dates and includes backdated records; corrections use their recorded dates.'),
             Spacer(1,12)]
    story.append(table([[p('Invoices less voids'), money(statement['net_invoiced'])],
                        [p('Net recorded payments'), money(statement['paid'])],
                        [p('Net credits'), money(statement['credited'])],
                        [p('CURRENT TEST BALANCE (USD)'), money(statement['due'])]], [350,154], False))
    story += [p('Balance by due date (USD)',heading), table([[p(label) for label,amount in statement['aging']],
                [money(amount) for label,amount in statement['aging']]], [100.8]*5)]
    story.append(p('Open test invoices',heading))
    rows = [[p('INVOICE / PROJECT'),p('DUE'),p('BALANCE (USD)',right)]]
    rows += [[p(row['number']+'\n'+row['project']+'\nOriginally billed to '+row['billed_to']),
              p(row['due_date'].isoformat()),money(row['due'])] for row in statement['open_invoices']]
    story.append(table(rows,[294,90,120]) if len(rows)>1 else p('No open test invoices.'))
    story += [p('All test account activity',heading), p('Opening balance: $0.00. Payments and credits reduce the balance; reversals restore it. '
                  'Voided invoices remain visible. A running balance can temporarily be negative when corrections were recorded later.'),Spacer(1,8)]
    rows = [[p('DATE'),p('ACTIVITY / PROJECT'),p('CHANGE (USD)',right),p('BALANCE (USD)',right)]]
    rows += [[p(row['date'].isoformat()),p(row['kind']+' - '+row['number']+'\n'+row['project']),
              money(row['amount']),money(row['running_balance'])] for row in statement['activity']]
    story.append(table(rows,[72,232,100,100]) if len(rows)>1 else p('No test account activity.'))
    def footer(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(line);canvas.line(54,48,558,48)
        canvas.setFillColor(ink);canvas.setFont('Helvetica',8)
        canvas.drawString(54,34,'Provost Home Design | TEST ONLY - NOT FOR PAYMENT')
        canvas.drawRightString(558,34,f'Page {doc.page}');canvas.restoreState()
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=letter,leftMargin=54,rightMargin=54,topMargin=42,bottomMargin=64,
                      title='Test customer statement',author='Provost Home Design').build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
