"""White-background Word derivatives and immutable, fingerprinted conversion assets."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

PROFILE = 'word-white-no-markings-20261020-v3-direct-legacy'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'
V = 'urn:schemas-microsoft-com:vml'


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def normalize(source, target):
    """Change presentation properties only; leave text, media, tables and sections intact."""
    from lxml import etree as ET
    counts = Counter()
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(target, 'w') as after:
        for info in before.infolist():
            data = before.read(info.filename)
            if info.filename.startswith('word/') and info.filename.endswith('.xml'):
                root = ET.fromstring(data)
                # XML text and object relationships must survive the formatting cleanup exactly.
                content = [(e.tag, e.text) for e in root.iter() if e.text and e.text.strip()]
                changed = False
                for element in list(root.iter()):
                    tag = element.tag
                    if tag == '{'+W+'}highlight':
                        element.set('{'+W+'}val', 'none'); counts['highlight'] += 1; changed = True
                    elif tag == '{'+W+'}shd':
                        element.attrib.clear()
                        element.set('{'+W+'}val', 'clear'); element.set('{'+W+'}fill', 'FFFFFF')
                        element.set('{'+W+'}color', 'auto'); counts['shading'] += 1; changed = True
                    elif tag in {'{'+W+'}'+name for name in ('shadow', 'outline', 'emboss', 'imprint')}:
                        element.set('{'+W+'}val', '0'); counts['text_effect'] += 1; changed = True
                    elif tag in ({'{'+A+'}'+name for name in ('outerShdw', 'innerShdw', 'prstShdw', 'glow', 'reflection')}
                                 | {'{'+W14+'}'+name for name in ('shadow', 'glow', 'reflection', 'textOutline')}):
                        element.getparent().remove(element); counts['drawing_effect'] += 1; changed = True
                    elif tag == '{'+V+'}shadow':
                        element.set('on', 'f'); counts['drawing_effect'] += 1; changed = True
                    if tag.startswith('{'+W+'}') and '{'+W+'}shadow' in element.attrib:
                        element.set('{'+W+'}shadow', '0'); counts['border_shadow'] += 1; changed = True
                if content != [(e.tag, e.text) for e in root.iter() if e.text and e.text.strip()]:
                    raise ValueError('派生Word清理意外改变文字，停止导出')
                if changed:
                    data = ET.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            after.writestr(info, data)
    return dict(counts)


def converter_identity(soffice):
    files = [Path(soffice)]
    binary = Path(soffice).with_name('soffice.bin')
    if binary.is_file(): files.append(binary)
    return {p.name: digest(p) for p in files}


def clean_pdf_backgrounds(source, target):
    """Remove editorial yellow/gray rectangular backgrounds from a Word export only.

    Images, strokes, text colors, geometry and source PDF documents are preserved.
    Legacy Word exports stay in their direct layout instead of a DOCX round trip.
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ContentStream
    reader = PdfReader(source); writer = PdfWriter(); writer.clone_document_from_reader(reader)
    removed = 0
    for page in writer.pages:
        content = ContentStream(page.get_contents(), writer)
        fill, stack, rectangle = (0, 0, 0), [], False
        for index, (args, op) in enumerate(content.operations):
            if op == b'q': stack.append(fill)
            elif op == b'Q': fill = stack.pop() if stack else (0, 0, 0)
            elif op == b'rg': fill = tuple(float(v) for v in args)
            elif op == b'g': fill = (float(args[0]),)*3
            elif op == b'k':
                c,m,y,k = [float(v) for v in args]; fill = tuple(1-min(1,v+k) for v in (c,m,y))
            elif op == b're': rectangle = True
            elif op in (b'm', b'l', b'c', b'v', b'y'): rectangle = False
            elif op in (b'f',b'f*',b'B',b'B*',b'b',b'b*'):
                yellow = fill[0]>.6 and fill[1]>.55 and fill[2]<.65 and min(fill[:2])-fill[2]>.15
                gray = .2<min(fill)<.98 and max(fill)-min(fill)<.06
                if rectangle and (yellow or gray):
                    content.operations[index] = ([], b'n' if op in (b'f',b'f*') else b'S')
                    removed += 1
                rectangle = False
            elif op in (b'n',b'S',b's'): rectangle = False
        page.replace_contents(content)
    writer.write(target)
    check = PdfReader(target)
    if len(check.pages) != len(reader.pages) or any(a.extract_text()!=b.extract_text() for a,b in zip(reader.pages,check.pages)):
        raise ValueError('旧Word派生PDF清理改变分页或文字，停止')
    return removed


def load_cached(folder, identity):
    receipt = folder/'conversion.json'
    if not receipt.is_file():
        raise ValueError('转换缓存缺少回执，保留现场：'+str(folder))
    record = json.loads(receipt.read_text(encoding='utf-8'))
    if record.get('identity') != identity:
        raise ValueError('转换缓存身份不一致：'+str(folder))
    for name, sha in record['files'].items():
        path = folder/name
        if path.parent != folder or path.is_symlink() or not path.is_file() or digest(path) != sha:
            raise ValueError('转换缓存校验失败，保留现场：'+str(path))
    return folder/'body.pdf'


def convert(root, member, workdir, soffice):
    import file_manager as fm
    source = fm.safe(root, member['source'])
    identity = {'source_sha256': member['sha256'], 'profile': PROFILE,
                'converter': converter_identity(soffice)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = fm.safe(root, fm.MGMT+'/PDF制作资料/转换缓存/'+key)
    if cache.exists(): return load_cached(cache, identity)
    folder = workdir/'word'/key
    if folder.exists():
        raise ValueError('转换过程目录已有现场，请核对后使用新的构建目录：'+str(folder))
    folder.mkdir(parents=True)
    original = folder/('original'+source.suffix.lower()); shutil.copy2(source, original)
    if digest(original) != member['sha256']: raise ValueError('Word副本与原件不一致')
    profile = (workdir/'lo-profile').resolve().as_uri()

    def export(input_path, output_dir, format_name, log_name):
        output_dir.mkdir(exist_ok=True)
        command = [str(soffice), '-env:UserInstallation='+profile, '--headless', '--convert-to',
                   format_name, '--outdir', str(output_dir), str(input_path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        except Exception as exc:
            (folder/log_name).write_text(str(exc), encoding='utf-8'); raise
        (folder/log_name).write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        if result.returncode: raise RuntimeError('Word转换失败，保留日志：'+str(folder/log_name))

    artifacts = [original]
    if source.suffix.lower() == '.docx':
        normalized = folder/'body.docx'
        cleanup = normalize(original, normalized); artifacts.append(normalized)
        export(normalized, folder/'pdf', 'pdf:writer_pdf_Export', 'export.log')
        pdf = folder/'pdf/body.pdf'; strategy = 'normalized_docx'
    else:
        export(original, folder/'pdf', 'pdf:writer_pdf_Export', 'export.log')
        pdf = folder/'pdf/original.pdf'; cleanup = {}; strategy = 'direct_legacy_layout'
    if not pdf.is_file(): raise RuntimeError('Word转PDF未生成文件：'+str(folder))
    cleanup['pdf_backgrounds'] = clean_pdf_backgrounds(pdf, folder/'body.pdf')
    artifacts.append(folder/'body.pdf')
    files = {p.name: digest(p) for p in artifacts}
    record = {'identity': identity, 'strategy': strategy, 'cleanup': cleanup, 'files': files,
              'note': '仅清理派生副本高亮、底纹及阴影；原文、图片、表格和分节保留。'}
    (folder/'conversion.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    # Publish immutable assets only after a successful conversion; failed work remains in workdir.
    staging = cache.with_name(key+'.staging')
    if staging.exists(): raise ValueError('发现未完成缓存发布，保留现场：'+str(staging))
    shutil.copytree(folder, staging); staging.rename(cache)
    return load_cached(cache, identity)
