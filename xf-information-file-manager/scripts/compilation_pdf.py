"""Reviewed, source-preserving PDF book; optional companion to file_manager."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

PROFILE = 'it-guidelines-a4-20261020-v1'
TITLE = '网络安全和信息化工作文件汇编'
CST = timezone(timedelta(hours=8))


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def expand(ranges, total):
    pages = []
    for pair in ranges:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(v) is not int for v in pair)
                or not 1 <= pair[0] <= pair[1] <= total):
            raise ValueError('PDF页段无效：' + str(pair))
        pages.extend(range(pair[0], pair[1] + 1))
    if len(set(pages)) != len(pages) or pages != sorted(pages):
        raise ValueError('PDF页段重叠或顺序错误')
    return pages


def selections(manifest):
    """Bind every included document to a reviewed primary and optional core supplements."""
    import file_manager as fm
    result = []
    for doc in sorted((d for d in manifest['documents'] if d['included']),
                      key=lambda d: (d['section'], d['sequence'])):
        selection = doc.get('book_selection', {})
        if selection.get('reviewed') is not True or not selection.get('parts'):
            raise ValueError('实时PDF缺少正文选取核对：' + doc['id'])
        primary = [m['sha256'] for m in doc['members'] if m['role'] == 'main' and m['in_compilation']]
        parts = selection['parts']
        if len(primary) != 1 or parts[0]['member_sha256'] != primary[0]:
            raise ValueError('PDF主文与实时选定版本不一致：' + doc['id'])
        by_hash = {m['sha256']: m for m in doc['members']}
        seen = set()
        for part in parts:
            sha = part.get('member_sha256')
            if sha not in by_hash or sha in seen or not by_hash[sha]['in_compilation']:
                raise ValueError('PDF选取成员无效或未收入实时版：' + doc['id'])
            seen.add(sha)
            total = part.get('page_count')
            if type(total) is not int or total < 1 or not part.get('evidence', '').strip():
                raise ValueError('PDF选取缺少页数或依据：' + doc['id'])
            kept = expand(part.get('keep_ranges', []), total)
            excluded = []
            for item in part.get('excluded_ranges', []):
                if not item.get('reason', '').strip():
                    raise ValueError('排除页段缺少原因：' + doc['id'])
                excluded += expand(item['ranges'], total)
            if not kept or len(set(kept + excluded)) != total or len(kept + excluded) != total:
                raise ValueError('保留和排除页段必须完整覆盖且不重叠：' + doc['id'])
            for page, box in part.get('crops', {}).items():
                if (not str(page).isdigit() or int(page) not in kept or len(box) != 4
                        or not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
                    raise ValueError('派生页裁剪区域无效：' + doc['id'])
        result.append((doc, parts, by_hash))
    if not result:
        raise ValueError('实时PDF至少需要一件文书')
    return result


def fingerprint(manifest):
    import file_manager as fm
    inputs = []
    for doc, parts, _ in selections(manifest):
        inputs.append({'id': doc['id'], 'section': doc['section'], 'sequence': doc['sequence'],
                       'name': fm.stem(doc), 'number': doc.get('number', ''), 'date': doc.get('date', ''),
                       'status': doc['status'], 'parts': [
                           {k: p.get(k, {} if k == 'crops' else [])
                            for k in ('member_sha256', 'page_count', 'keep_ranges', 'crops')}
                           for p in parts]})
    raw = json.dumps({'profile': PROFILE, 'inputs': inputs}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def transition(root, manifest, old=None):
    """Read-only validation of a candidate or of an unchanged registered book."""
    import file_manager as fm
    if old and old.get('compilation_pdf_required') and not manifest.get('compilation_pdf_required'):
        raise ValueError('不得取消已经启用的实时PDF维护')
    if not manifest.get('compilation_pdf_required'):
        return None
    expected = fingerprint(manifest)
    previous = (old or {}).get('compilation_pdf')
    if previous and previous.get('input_fingerprint') == expected:
        path = fm.safe(root, previous['path'])
        if not path.is_file() or digest(path) != previous['sha256']:
            raise ValueError('已登记实时PDF缺失或被修改，先核对恢复点')
        return {'action': 'reuse', 'record': copy.deepcopy(previous), 'source': str(path)}
    candidate = manifest.get('compilation_pdf_candidate')
    if not candidate or candidate['record'].get('input_fingerprint') != expected:
        raise ValueError('正文或目录变化，必须先运行prepare-book生成并核验候选PDF')
    record = candidate['record']
    target = fm.safe(root, record['path'])
    if target.parent != fm.safe(root, fm.LIVE) or not re.fullmatch(
            re.escape(TITLE) + r'（无附件，\d{8}_\d{6}修订）\.pdf', target.name):
        raise ValueError('实时PDF命名或位置不符合规则')
    source = Path(candidate['source'])
    if (not source.is_absolute() or source.is_symlink() or not source.is_file()
            or digest(source) != record['sha256']):
        raise ValueError('候选PDF缺失或内容变化')
    if target.exists():
        raise ValueError('新PDF目标已存在，拒绝覆盖：' + str(target))
    verify_file(source, record)
    return {'action': 'update' if previous else 'create', 'record': copy.deepcopy(record), 'source': str(source)}


def verify_file(path, record):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if len(reader.pages) != record['page_count'] or not reader.outline:
        raise ValueError('PDF页数或书签与构建记录不一致')
    if len(record['documents']) != record['document_count']:
        raise ValueError('PDF文书索引数量不一致')
    total = record['toc_pages'] + sum(d['body_page_count'] for d in record['documents'])
    if total != len(reader.pages):
        raise ValueError('PDF目录与正文页数不一致')
    links = sum(len(p.get('/Annots', [])) for p in reader.pages[:record['toc_pages']])
    if links != record['document_count']:
        raise ValueError('PDF可点击目录数量不一致')
    offset = record['toc_pages'] + 1
    for item in record['documents']:
        if item['first_page'] != offset or item['body_page_count'] < 1:
            raise ValueError('PDF正文起始页不连续')
        offset += item['body_page_count']
    for page in reader.pages:
        if abs(float(page.mediabox.width) - 595.276) > 1 or abs(float(page.mediabox.height) - 841.89) > 1:
            raise ValueError('合成本存在非A4页面')
    metadata = reader.metadata or {}
    if metadata.get('/Subject') != record['input_fingerprint']:
        raise ValueError('PDF输入指纹与台账不一致')


def verify(root, manifest):
    import file_manager as fm
    if manifest.get('compilation_pdf') and not manifest.get('compilation_pdf_required'):
        raise ValueError('已登记实时PDF不得绕过维护核验')
    if not manifest.get('compilation_pdf_required'):
        return {}
    record = manifest.get('compilation_pdf')
    if not record or record.get('input_fingerprint') != fingerprint(manifest):
        raise ValueError('实时PDF未生成或已落后于当前正文和目录')
    path = fm.safe(root, record['path'])
    if path.parent != fm.safe(root, fm.LIVE) or not path.is_file() or digest(path) != record['sha256']:
        raise ValueError('实时PDF位置、内容或登记不一致')
    expected = [(d['id'], d['section'], d['sequence']) for d, _, _ in selections(manifest)]
    actual = [(d['id'], d['section'], d['sequence']) for d in record['documents']]
    if actual != expected:
        raise ValueError('PDF文书顺序与台账不一致')
    verify_file(path, record)
    return {'pdf_verified': True, 'pdf_documents': record['document_count'], 'pdf_pages': record['page_count']}


def fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    folder = Path('C:/Windows/Fonts')
    for name, filename in (('BookSong', 'simsun.ttc'), ('BookHei', 'simhei.ttf')):
        if name not in pdfmetrics.getRegisteredFontNames():
            path = folder / filename
            if not path.is_file():
                raise ValueError('缺少汇编字体：' + str(path))
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))


def render_member(root, member, workdir, soffice):
    """Copy originals; Word export uses its own profile and never opens the source for editing."""
    import file_manager as fm
    source = fm.safe(root, member['source'])
    if digest(source) != member['sha256']:
        raise ValueError('转换前原件内容变化')
    suffix = source.suffix.lower()
    if suffix == '.pdf':
        return source
    if suffix not in ('.doc', '.docx', '.odt', '.rtf'):
        raise ValueError('PDF构建不支持该正文格式：' + suffix)
    inputs = workdir / 'inputs'; outputs = workdir / 'converted'
    inputs.mkdir(parents=True, exist_ok=True); outputs.mkdir(parents=True, exist_ok=True)
    target = inputs / (member['sha256'][:16] + suffix)
    if target.exists() and digest(target) != member['sha256']:
        raise ValueError('转换缓存输入冲突')
    shutil.copy2(source, target)
    pdf = outputs / (target.stem + '.pdf')
    receipt = outputs / (target.stem + '.json')
    if pdf.exists() and receipt.exists():
        cached = json.loads(receipt.read_text(encoding='utf-8'))
        if cached.get('source_sha256') == member['sha256'] and cached.get('pdf_sha256') == digest(pdf):
            return pdf
        raise ValueError('转换缓存校验失败；保留现场，使用新的工作目录核对')
    command = [str(soffice), '-env:UserInstallation=' + (workdir / 'lo-profile').as_uri(),
               '--headless', '--convert-to', 'pdf:writer_pdf_Export', '--outdir', str(outputs), str(target)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    (outputs / (target.stem + '.log')).write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode or not pdf.is_file():
        raise RuntimeError('Word转PDF失败，保留转换日志：' + str(target))
    receipt.write_text(json.dumps({'source_sha256': member['sha256'], 'pdf_sha256': digest(pdf)}, indent=2), encoding='utf-8')
    return pdf


def toc_pdf(documents, revision, expected_pages=None):
    import file_manager as fm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, KeepTogether, Table, TableStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    buffer = io.BytesIO()
    styles = {
        'title': ParagraphStyle('title', fontName='BookHei', fontSize=18, leading=25, alignment=TA_CENTER, spaceAfter=6),
        'revision': ParagraphStyle('revision', fontName='BookSong', fontSize=10, leading=16, alignment=TA_CENTER, spaceAfter=14),
        'section': ParagraphStyle('section', fontName='BookHei', fontSize=15, leading=22, spaceBefore=12, spaceAfter=8),
        'entry': ParagraphStyle('entry', fontName='BookSong', fontSize=12.5, leading=18, wordWrap='CJK'),
        'page': ParagraphStyle('page', fontName='BookSong', fontSize=12.5, leading=18, alignment=TA_RIGHT),
        'meta': ParagraphStyle('meta', fontName='BookSong', fontSize=9.5, leading=14, leftIndent=16, spaceAfter=6),
    }
    story = [Paragraph(TITLE, styles['title']), Paragraph('目录', styles['title']),
             Paragraph('无附件版　' + revision.strftime('%Y年%m月%d日 %H:%M:%S') + '修订', styles['revision'])]
    current_section = None
    for item in documents:
        if item['section'] != current_section:
            current_section = item['section']
            story.append(Paragraph('第' + '一二三'[current_section - 1] + '部分　' + fm.SECTIONS[current_section - 1].split('、', 1)[1], styles['section']))
        entry = f"{item['sequence']}、{escape(item['name'])}"
        metadata = escape(item['metadata'])
        row = Table([[Paragraph(entry, styles['entry']), Paragraph('… '+str(item['body_start']), styles['page'])]],
                    colWidths=[435, 48], hAlign='LEFT')
        row.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'BOTTOM'),('LEFTPADDING',(0,0),(-1,-1),0),
                                 ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),
                                 ('BOTTOMPADDING',(0,0),(-1,-1),0)]))
        story.append(KeepTogether([row, Paragraph(metadata, styles['meta'])]))
    SimpleDocTemplate(buffer, pagesize=(595.276, 841.89), leftMargin=48, rightMargin=48,
                      topMargin=43, bottomMargin=40, title=TITLE).build(story)
    return buffer.getvalue()


def cropped_page(source, page_number, box, width, height):
    """Only mixed body/form pages are raster-cropped, removing hidden excluded form text too."""
    import pdfplumber
    from pypdf import PdfReader
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.utils import ImageReader
    with pdfplumber.open(source) as pdf:
        image = pdf.pages[page_number-1].to_image(resolution=240).original
        pixels = image.crop((round(image.width*box[0]), round(image.height*(1-box[3])),
                             round(image.width*box[2]), round(image.height*(1-box[1]))))
    stream=io.BytesIO();canvas=Canvas(stream,pagesize=(width,height))
    canvas.drawImage(ImageReader(pixels),width*box[0],height*box[1],
                     width=width*(box[2]-box[0]),height=height*(box[3]-box[1]))
    canvas.save();stream.seek(0)
    return PdfReader(stream).pages[0]


def build(root, manifest, workdir, soffice):
    import file_manager as fm
    from pypdf import PdfReader, PdfWriter, PageObject, Transformation
    from pypdf.annotations import Link
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.pagesizes import A4
    workdir = Path(workdir).resolve(); workdir.mkdir(parents=True, exist_ok=True)
    if workdir.is_relative_to(root.resolve()):
        raise ValueError('PDF加工目录须位于业务根目录之外')
    fonts()
    revision = datetime.now(CST)
    name = TITLE + '（无附件，' + revision.strftime('%Y%m%d_%H%M%S') + '修订）.pdf'
    writer = PdfWriter(); documents = []; readers = []; body_pages = []
    for doc, parts, members in selections(manifest):
        item = {'id': doc['id'], 'section': doc['section'], 'sequence': doc['sequence'], 'name': fm.stem(doc),
                'body_start': len(body_pages) + 1, 'parts': [], 'body_page_count': 0,
                'metadata': f"[{doc['level']}] [{doc['topic']}] " + (doc.get('number') or doc.get('date') or '')
                            + (' （参考）' if doc['status'] == '历史留存' else '')}
        for selection in parts:
            member = members[selection['member_sha256']]
            source = render_member(root, member, workdir, soffice)
            reader = PdfReader(source); readers.append(reader)
            if len(reader.pages) != selection['page_count']:
                raise ValueError('转换页数与人工核对页段不一致：' + doc['id'])
            part_record = copy.deepcopy(selection)
            part_record.update(source=member.get('archive_path', member['source']), rendered_sha256=digest(source))
            item['parts'].append(part_record)
            for page_number in expand(selection['keep_ranges'], len(reader.pages)):
                page = copy.copy(reader.pages[page_number - 1])
                if page.get('/Rotate', 0):
                    page.transfer_rotation_to_content()
                box = selection.get('crops', {}).get(str(page_number), [0, 0, 1, 1])
                original_box = page.cropbox
                full_x, full_y = float(original_box.left), float(original_box.bottom)
                full_width, full_height = float(original_box.width), float(original_box.height)
                if str(page_number) in selection.get('crops', {}):
                    page = cropped_page(source, page_number, box, full_width, full_height)
                    full_x, full_y = 0, 0
                    box = [0, 0, 1, 1]
                x0 = full_x + full_width * box[0]
                y0 = full_y + full_height * box[1]
                x1 = full_x + full_width * box[2]
                y1 = full_y + full_height * box[3]
                page.cropbox.lower_left = (x0, y0); page.cropbox.upper_right = (x1, y1)
                # Keep the entire selected source region within a new A4 page; add running matter outside it.
                width, height = full_width, full_height
                scale = min((A4[0]-24)/width, (A4[1]-65)/height)
                tx = (A4[0]-width*scale)/2 - full_x*scale
                ty = 30 + (A4[1]-65-height*scale)/2 - full_y*scale
                output = PageObject.create_blank_page(width=A4[0], height=A4[1])
                output.merge_transformed_page(page, Transformation().scale(scale).translate(tx, ty))
                output.pop('/Annots', None)
                body_pages.append((output, doc['section']))
                item['body_page_count'] += 1
        documents.append(item)
    toc = PdfReader(io.BytesIO(toc_pdf(documents, revision)))
    toc_count = len(toc.pages)
    for page in toc.pages: writer.add_page(page)
    for number, (page, section) in enumerate(body_pages, 1):
        overlay = io.BytesIO(); canvas = Canvas(overlay, pagesize=A4)
        canvas.setFont('BookSong', 10)
        canvas.drawRightString(A4[0]-30, A4[1]-23, '第'+'一二三'[section-1]+'部分　'+fm.SECTIONS[section-1].split('、',1)[1])
        canvas.drawCentredString(A4[0]/2, 14, f'-{number}-')
        canvas.save(); overlay.seek(0); layer=PdfReader(overlay); page.merge_page(layer.pages[0]); writer.add_page(page)
    section_bookmarks = {}
    for item in documents:
        first = toc_count + item['body_start'] - 1
        item['first_page'] = first + 1
        section = item['section']
        if section not in section_bookmarks:
            section_bookmarks[section] = writer.add_outline_item('第'+'一二三'[section-1]+'部分 '+fm.SECTIONS[section-1].split('、',1)[1], first)
        writer.add_outline_item(str(item['sequence'])+'、'+item['name'], first, parent=section_bookmarks[section])
    # Reuse ReportLab paragraph geometry for precise clickable TOC areas.
    # Each entry is searched as a wrapped paragraph, so links follow the actual multi-line layout.
    import pdfplumber
    with pdfplumber.open(io.BytesIO(toc_pdf(documents, revision))) as pdf:
        for item in documents:
            needle=f"{item['sequence']}、{item['name']}"
            for page_index, page in enumerate(pdf.pages):
                plain=re.sub(r'\s+', '', page.extract_text() or '')
                if re.sub(r'\s+', '', needle) not in plain:
                    continue
                chars=page.chars
                joined=''.join(c['text'] for c in chars)
                start=joined.find(needle)
                if start < 0:
                    continue
                selected=chars[start:start+len(needle)]
                rect=[min(c['x0'] for c in selected), page.height-max(c['bottom'] for c in selected),
                      max(c['x1'] for c in selected), page.height-min(c['top'] for c in selected)]
                writer.add_annotation(page_index, Link(rect=rect, target_page_index=item['first_page']-1))
                break
            else:
                raise ValueError('目录条目无法建立跳转：'+item['id'])
    input_fingerprint=fingerprint(manifest)
    writer.add_metadata({'/Title':TITLE+'（无附件）','/Author':'','/Subject':input_fingerprint,
                         '/CreationDate':revision.strftime('D:%Y%m%d%H%M%S')+"+08'00'"})
    writer.page_mode='/UseOutlines'
    output=workdir/name
    if output.exists(): raise ValueError('候选PDF名称已存在，拒绝覆盖')
    writer.write(output)
    record={'schema_version':1,'profile':PROFILE,'revision':revision.isoformat(timespec='seconds'),
            'path':fm.LIVE+'/'+name,'sha256':digest(output),'input_fingerprint':input_fingerprint,
            'document_count':len(documents),'page_count':len(writer.pages),'toc_pages':toc_count,'documents':documents}
    verify_file(output,record)
    (workdir/'pdf-build.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'source':str(output),'record':record}


def prepare(root, manifest_path, workdir, soffice):
    import file_manager as fm
    manifest=fm.load(manifest_path)
    out, _, _=fm.make_plan(root,manifest)
    if not out.get('compilation_pdf_required'):
        raise ValueError('候选台账尚未启用实时PDF')
    old = fm.load(root/fm.RECORDS[0]) if (root/fm.RECORDS[0]).exists() else None
    if (old or {}).get('compilation_pdf', {}).get('input_fingerprint') == fingerprint(out):
        current = transition(root, out, old)
        return {'prepared': True, 'reused': True, 'pdf': current['source']}
    try:
        candidate=build(root,out,workdir,soffice)
        manifest['compilation_pdf_candidate']=candidate
        fm.save(manifest_path,manifest)
        return {'prepared':True,'pdf':candidate['source'],'documents':candidate['record']['document_count'],
                'pages':candidate['record']['page_count']}
    except Exception as exc:
        folder=Path(workdir);folder.mkdir(parents=True,exist_ok=True)
        fm.save(folder/'build-failure.json',{'error':str(exc),'manifest':str(manifest_path),'time':datetime.now(CST).isoformat()})
        raise
