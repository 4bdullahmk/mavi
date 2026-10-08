#!/usr/bin/env python3
"""Local document import/export. No network or arbitrary command execution."""
import csv, hashlib, io, json, os, sys, tempfile, zipfile
from pathlib import Path
from xml.sax.saxutils import escape
MAX_SIZE = 20_000_000
TEXT = {'txt','md','csv','json','html','css','py','js','ts','swift','yaml','yml','xml','sql','sh','log'}

def read(path):
    path=Path(path)
    if path.stat().st_size>MAX_SIZE: raise ValueError('Files must be under 20 MB.')
    ext=path.suffix.lower()[1:]
    editable=None
    if ext in ('docx','xlsx','pptx'):
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist())>80_000_000: raise ValueError('Expanded document exceeds 80 MB.')
    if ext=='pdf':
        from pypdf import PdfReader
        doc=PdfReader(path)
        text='\n\n'.join((page.extract_text() or '') for page in doc.pages[:80])
        if not text.strip(): raise ValueError('This PDF has no extractable text. Use an image or a text PDF.')
    elif ext=='docx':
        from docx import Document
        doc=Document(path); text='\n'.join(p.text for p in doc.paragraphs)
        for table in doc.tables:
            text+='\n'+'\n'.join('\t'.join(cell.text for cell in row.cells) for row in table.rows)
    elif ext=='xlsx':
        from openpyxl import load_workbook
        book=load_workbook(path,read_only=True,data_only=False)
        chunks=[]
        for sheet in book.worksheets[:10]:
            out=io.StringIO(); writer=csv.writer(out)
            for row in sheet.iter_rows(max_row=min(sheet.max_row,500),max_col=min(sheet.max_column,30),values_only=True): writer.writerow(row)
            if editable is None: editable=out.getvalue()
            chunks.append('SHEET: '+sheet.title+'\n'+out.getvalue())
        book.close(); text='\n'.join(chunks)
    elif ext=='pptx':
        from pptx import Presentation
        deck=Presentation(path)
        text='\n\n'.join('SLIDE '+str(index+1)+'\n'+'\n'.join(shape.text for shape in slide.shapes if shape.has_text_frame) for index,slide in enumerate(deck.slides) if index<50)
    elif ext in TEXT: text=path.read_text(encoding='utf-8-sig')
    else: raise ValueError('Supported: text/code, CSV, PDF, DOCX, XLSX, PNG and JPEG attachments.')
    return {'text':text[:80000],'truncated':len(text)>80000,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'editable_text':editable[:80000] if editable is not None else text[:80000]}

def write(req):
    root=Path(req['root']).resolve(strict=True)
    name=req['name']
    if not isinstance(name,str) or name!=Path(name).name or name in ('.','..') or '/' in name or '\\' in name: raise ValueError('Choose a file name without folders.')
    target=root/name
    if target.is_symlink() or target.resolve().parent!=root: raise ValueError('File path leaves the selected folder.')
    content=req['content']
    if not isinstance(content,str) or len(content)>120000: raise ValueError('File content must be under 120,000 characters.')
    expected=req.get('sha256')
    if target.exists():
        if not target.is_file() or expected is None or hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise ValueError('File already exists or changed since opening. Reopen it before saving.')
    elif expected is not None: raise ValueError('The original file was removed. Save a new copy instead.')
    ext=target.suffix.lower()[1:]
    if ext not in TEXT|{'pdf','docx','xlsx','pptx'}: raise ValueError('Unsupported output format.')
    fd,temp=tempfile.mkstemp(prefix='.mavi-',suffix='.'+ext,dir=root); os.close(fd)
    try:
        if ext=='docx':
            from docx import Document
            doc=Document()
            for line in content.splitlines():
                if line.startswith('# '): doc.add_heading(line[2:],level=1)
                elif line.startswith('## '): doc.add_heading(line[3:],level=2)
                elif line.startswith('- '): doc.add_paragraph(line[2:],style='List Bullet')
                else: doc.add_paragraph(line)
            doc.save(temp)
        elif ext=='pdf':
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            styles=getSampleStyleSheet()
            # A bundled ReportLab font avoids depending on host fonts.
            import reportlab
            font=Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'
            pdfmetrics.registerFont(TTFont('WorkDesk',str(font)))
            for style in styles.byName.values(): style.fontName='WorkDesk'
            parts=[]
            for line in content.splitlines():
                heading=line.startswith('# ')
                parts += [Paragraph(escape(line[2:] if heading else line) or ' ',styles['Heading1' if heading else 'BodyText']),Spacer(1,5)]
            SimpleDocTemplate(temp).build(parts)
        elif ext=='pptx':
            from PresentationTools import create_presentation
            with tempfile.TemporaryDirectory(dir=root) as scratch:
                produced=create_presentation(json.loads(content),scratch)
                os.replace(produced,temp)
        elif ext=='xlsx' and content.lstrip().startswith('{'):
            from SpreadsheetTools import build_workbook
            book=build_workbook(json.loads(content));book.save(temp);book.close()
        elif ext=='xlsx':
            from openpyxl import Workbook
            book=Workbook(); sheet=book.active; sheet.title='Mavi'
            rows=list(csv.reader(io.StringIO(content)))
            if len(rows)>5000 or any(len(row)>100 for row in rows): raise ValueError('Spreadsheet exceeds 5,000 rows or 100 columns.')
            for row in rows:
                sheet.append(row)
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value,str) and cell.value.startswith('='): cell.data_type='s'
            sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
            book.save(temp)
        else: Path(temp).write_text(content,encoding='utf-8')
        # Recheck after conversion so a concurrent edit cannot be overwritten.
        if target.exists():
            if expected is None or hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise ValueError('File changed during export. Your original was preserved.')
        elif expected is not None: raise ValueError('The original file was removed during export.')
        os.replace(temp,target)
        return {'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    finally:
        Path(temp).unlink(missing_ok=True)

def schema(format):
    if format=='xlsx':
        from SpreadsheetTools import SPEC_GUIDE
        return {'guide':SPEC_GUIDE}
    if format=='pptx':
        from PresentationTools import SPEC_GUIDE
        return {'guide':SPEC_GUIDE}
    return {'guide':''}

def validate(req):
    """Validate generated XLSX/PPTX JSON without creating or changing files."""
    format=req.get('format')
    content=req.get('content')
    if format not in ('xlsx','pptx'):
        return {'valid':False,'validation_error':'Only XLSX and PPTX specifications can be validated.'}
    if not isinstance(content,str) or len(content)>120000:
        return {'valid':False,'validation_error':'File content must be text under 120,000 characters.'}
    try:
        spec=json.loads(content)
        if format=='xlsx':
            from SpreadsheetTools import validate_spec
            validate_spec(spec)
        else:
            from PresentationTools import _validate_spec
            _validate_spec(spec)
    except (ValueError,TypeError) as error:
        return {'valid':False,'validation_error':str(error)[:500] or 'Invalid file specification.'}
    return {'valid':True}

if __name__=='__main__':
    try:
        req=json.load(sys.stdin)
        print(json.dumps(read(req['path']) if req['action']=='read' else write(req) if req['action']=='write' else schema(req.get('format')) if req['action']=='schema' else validate(req) if req['action']=='validate' else {'error':'Unknown operation'}))
    except Exception as e:
        print(json.dumps({'error':str(e)})); sys.exit(1)
