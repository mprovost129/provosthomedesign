"""Small review report. Original PDFs stay immutable; no untrusted PDF processing on the web worker."""
from html import escape
from io import BytesIO
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


def review_pdf(review):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles['BodyText'].spaceAfter = 8
    def text(value, style='BodyText'):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style])
    revision = review.document_version
    story = [text('Provost Home Design — Plan review', 'Title'), text(review.document_version.document.project.name, 'Heading2'),
        text(f'{review.document_title} | Version {revision.sequence} | {revision.original_name}'),
        text(f'{review.author_name} | {review.company_name} | {review.author_email}'),
        text(f'Request: {review.work_item.reference}' if review.submitted_at else 'DRAFT — not submitted'),
        text(f'Submitted: {timezone.localtime(review.submitted_at).strftime('%b %d, %Y at %I:%M %p %Z')}' if review.submitted_at else f'Saved: {timezone.localtime(review.updated_at).strftime('%b %d, %Y at %I:%M %p %Z')}'),
        text('This report records comments on the named original plan version. Download that PDF separately to view the drawing. Locations are measured from the top left of its displayed page; the plan itself has not been changed.'),
        Spacer(1, 12), text('Requested changes', 'Heading2'), text(review.summary or 'See the page notes below.')]
    for index, pin in enumerate(review.annotations, 1):
        story += [text(f'Note {index} — Page {pin["page"]}', 'Heading3'),
            text(f'Location: {pin["x"]:.1%} from left, {pin["y"]:.1%} from top'), text(pin['text'] or '(Empty draft note)')]
    def footer(canvas, doc):
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#526273'))
        canvas.drawString(48, 30, 'Provost Home Design | Plan review record')
        canvas.drawRightString(564, 30, str(doc.page))
    SimpleDocTemplate(buffer, pagesize=letter, leftMargin=48, rightMargin=48, topMargin=48, bottomMargin=48,
        title=review.document_title+' — Review', author='Provost Home Design').build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
