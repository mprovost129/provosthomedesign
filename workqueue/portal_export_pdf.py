"""Standalone bounded PDF overlay process. No Django, network, or provider credentials."""
import json
import math
import sys
from io import BytesIO
from html import escape

def enforce_limits():
    try:
        import resource
    except ImportError:  # Windows development; wall-clock timeout still applies in the parent.
        return
    resource.setrlimit(resource.RLIMIT_AS,(192*1024*1024,192*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(20,20))
    resource.setrlimit(resource.RLIMIT_FSIZE,(110*1024*1024,110*1024*1024))

def page_frame(page):
    crop = page.cropbox
    left,bottom,width,height = float(crop.left),float(crop.bottom),float(crop.width),float(crop.height)
    rotation = page.rotation % 360
    if rotation not in {0,90,180,270} or not all(math.isfinite(value) for value in [left,bottom,width,height]) or not 1<=width<=20000 or not 1<=height<=20000:
        raise ValueError('Unsupported page geometry')
    matrices = {0:(1,0,0,1,left,bottom),90:(0,1,-1,0,left+width,bottom),
        180:(-1,0,0,-1,left+width,bottom+height),270:(0,-1,1,0,left,bottom+height)}
    display_width,display_height = (height,width) if rotation in {90,270} else (width,height)
    return display_width,display_height,rotation,matrices[rotation]

def stamp_page(page, marks, geometry):
    from reportlab.pdfgen.canvas import Canvas
    from pypdf import PdfReader, Transformation
    width,height,rotation,matrix = page_frame(page)
    unit = float(page.get('/UserUnit',1))
    if (not math.isfinite(unit) or not .01<=unit<=100 or geometry['rotation'] != rotation
            or abs(geometry['width']-width*unit)>max(.1,width*unit*.001)
            or abs(geometry['height']-height*unit)>max(.1,height*unit*.001)):
        raise ValueError('Review geometry differs from the original drawing')
    buffer = BytesIO();canvas = Canvas(buffer,pagesize=(width,height))
    radius = max(5,min(16,min(width,height)/70))
    canvas.setStrokeColorRGB(.72,.2,.12);canvas.setLineWidth(max(1,radius/5));canvas.setLineCap(1);canvas.setLineJoin(1)
    for number,mark in marks:
        x,y = mark['x']*width,(1-mark['y'])*height
        kind = mark.get('type','pin')
        points = [(point[0]*width,(1-point[1])*height) for point in mark.get('points',[])]
        if kind == 'box':
            a,b=points;canvas.rect(min(a[0],b[0]),min(a[1],b[1]),abs(b[0]-a[0]),abs(b[1]-a[1]))
        elif kind in {'arrow','freehand'}:
            path=canvas.beginPath();path.moveTo(*points[0])
            for point in points[1:]:path.lineTo(*point)
            canvas.drawPath(path)
            if kind=='arrow':
                a,b=points;angle=math.atan2(b[1]-a[1],b[0]-a[0])
                for offset in [-.45,.45]:canvas.line(*b,b[0]-radius*1.5*math.cos(angle+offset),b[1]-radius*1.5*math.sin(angle+offset))
        canvas.setFillColorRGB(.72,.2,.12);canvas.circle(x,y,radius,stroke=0,fill=1)
        canvas.setFillColorRGB(1,1,1);canvas.setFont('Helvetica-Bold',radius)
        canvas.drawCentredString(x,y-radius*.35,str(number))
    canvas.save()
    stamp = PdfReader(buffer).pages[0]
    page.merge_transformed_page(stamp,Transformation(matrix),over=True,expand=False)

def notes_pdf(data):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
    styles=getSampleStyleSheet();buffer=BytesIO()
    def text(value,style='BodyText'):return Paragraph(escape(str(value)).replace('\n','<br/>'),styles[style])
    story=[text('Provost Home Design - Marked review copy','Title'),text(data['project'],'Heading2'),
        text(f"{data['title']} - Version {data['sequence']} - {data['file']}"),
        text(f"Request {data['reference']} | {data['author']} | {data['company']} | {data['email']}"),
        text('Submitted '+data['submitted']),Spacer(1,12),
        text('These are client-requested changes on a copy of the published plan. This is not a newly issued plan, authorization to build, or acceptance of pricing. The original plan and submitted review remain unchanged.'),
        text('General notes','Heading2'),text(data['summary'] or 'See the numbered comments below.')]
    for index,mark in enumerate(data['annotations'],1):
        story += [text(f"Note {index} - Original plan page {mark['page']} - {mark.get('type','pin')}",'Heading3'),text(mark['text'])]
    SimpleDocTemplate(buffer,leftMargin=48,rightMargin=48,topMargin=48,bottomMargin=48).build(story)
    return buffer

def export_pdf(source, data, output):
    from pypdf import PdfReader,PdfWriter
    from pypdf.generic import NameObject,ArrayObject
    reader=PdfReader(source,strict=False)
    if reader.is_encrypted or '/AcroForm' in reader.trailer['/Root']:raise ValueError('Use an unlocked static plan')
    if not 1<=len(reader.pages)<=500:raise ValueError('Too many pages')
    writer=PdfWriter(clone_from=reader)
    # Retain drawing content/layer settings but exclude root scripts, embedded files, and page actions.
    for key in ['/OpenAction','/AA','/Names','/AF']:writer.root_object.pop(key,None)
    geometry={entry['page']:entry for entry in data['geometry']}
    grouped={}
    for index,mark in enumerate(data['annotations'],1):
        if not 1<=mark['page']<=len(writer.pages):raise ValueError('Unknown plan page')
        grouped.setdefault(mark['page'],[]).append((index,mark))
    for number,page in enumerate(writer.pages,1):
        page.pop('/AA',None)
        if '/Annots' in page:
            retained=[]
            for entry in page['/Annots']:
                annotation=entry.get_object()
                if annotation.get('/Subtype') in {'/FileAttachment','/RichMedia','/Movie','/Sound','/Screen','/Widget'}:continue
                for key in ['/A','/AA','/FS','/RichMediaContent','/RichMediaSettings']:annotation.pop(key,None)
                retained.append(entry)
            page[NameObject('/Annots')]=ArrayObject(retained)
        if number in grouped:stamp_page(page,grouped[number],geometry[number])
    report=PdfReader(notes_pdf(data))
    for page in report.pages:writer.add_page(page)
    writer.add_metadata({'/Title':data['reference']+' - Marked client review copy','/Author':'Provost Home Design'})
    writer.write(output)

if __name__=='__main__':
    enforce_limits()
    # runpy leaves the helper path at argv[1]; the following paths are generated by the parent.
    export_pdf(sys.argv[-3],json.loads(open(sys.argv[-2],encoding='utf-8').read()),sys.argv[-1])
