"""Build independently portable catalog trees from reviewed standard metadata.

This module does not decide product applicability or infer website status.
"""
from pathlib import Path
from datetime import date
from urllib.parse import quote, unquote, urlparse
from collections import Counter, defaultdict
import argparse, hashlib, html, json, os, re, shutil, tempfile

META = Path('.标准维护/registry.json')
VALID_STATES = {'现行', '即将实施', '废止'}

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def safe(root,relative):
    rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts:raise ValueError('路径必须是资料库内的相对路径')
    p=(root/rel).resolve()
    if not p.is_relative_to(root.resolve()):raise ValueError('路径越出资料库')
    # Refuse junctions/symlinks, including junctions that resolve inside root.
    cursor=root/rel
    while cursor!=root and cursor!=cursor.parent:
        if cursor.is_symlink() or (hasattr(cursor,'is_junction') and cursor.is_junction()):raise ValueError('不支持链接或联接目录')
        cursor=cursor.parent
    return p

def clean_name(s):
    s=str(s).replace('/','／').replace('\\','＼')
    s=re.sub(r'[<>:"|?*\x00-\x1f]','_',s).strip(' .')
    return s[:85] or '未命名'

def norm(s):return re.sub(r'[^A-Z0-9]','',s.upper())

def status_folder(s,as_of):
    if not s.get('verified') or not s.get('checked_at') or not s.get('official_url'):
        raise ValueError('未通过官网核对：'+s['standard'])
    status=s.get('status')
    if status not in VALID_STATES:raise ValueError('未知官网状态：'+str(status))
    effective=s.get('implemented','')
    if status=='即将实施' and not effective:raise ValueError('待生效标准缺实施日期：'+s['standard'])
    if effective:
        day=date.fromisoformat(effective);now=date.fromisoformat(as_of)
        if (status=='现行' and day>now) or (status=='即将实施' and day<=now):raise ValueError('官网状态与实施日期冲突：'+s['standard'])
    national=s['standard'].startswith('GB')
    return ('01_现行国标' if national else '03_现行行标') if status=='现行' else (('02_待生效国标' if national else '04_待生效行标') if status=='即将实施' else '历史')

def verify_pdf(path,s):
    import fitz
    p=Path(path)
    with p.open('rb') as f:
        if f.read(5)!=b'%PDF-':raise ValueError('文件不是PDF：'+str(p))
    actual=digest(p)
    if s.get('sha256') and actual!=s['sha256']:raise ValueError('PDF哈希变化：'+str(p))
    with fitz.open(p) as doc:
        if doc.page_count<1 or doc.is_encrypted:raise ValueError('PDF不可完整读取')
        text=''.join(doc[i].get_text() for i in range(min(3,len(doc))))
        match=norm(s['standard']) in norm(text)
        if not match and not (s.get('identity_verified') and s.get('sha256')==actual):raise ValueError('PDF编号尚未核实：'+s['standard'])
        return dict(sha256=actual,pages=len(doc),bytes=p.stat().st_size,identity_verified=True)

def atomic_json(p,data):
    p.parent.mkdir(parents=True,exist_ok=True)
    q=p.with_name(p.name+'.tmp');q.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');q.replace(p)

def copy_checked(src,dst,expected):
    if dst.exists():
        if digest(dst)!=expected:raise ValueError('目标已有不同内容，拒绝覆盖：'+str(dst))
        return
    dst.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.copy-',dir=dst.parent);os.close(fd);tmp=Path(name)
    try:
        shutil.copy2(src,tmp)
        if digest(tmp)!=expected:raise ValueError('复制后哈希不一致')
        tmp.replace(dst)
    finally:
        if tmp.exists():tmp.unlink()

def filename(product,s):
    state='待生效（'+s['implemented']+'实施）' if s['status']=='即将实施' else ('已废止' if s['status']=='废止' else '现行')
    return clean_name(product)+'-'+s['standard'].replace('/','／')+'-'+state+'.pdf'

def build(root,data):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    if data.get('schema_version')!=1:raise ValueError('不支持的数据版本')
    cats={c['id']:c for c in data['catalogs']};standards=data['standards']
    ids=[p['id'] for p in data['products']]
    if len(ids)!=len(set(ids)):raise ValueError('产品ID重复')
    previous=data.get('placements',[])
    existing= root/META
    if existing.exists():previous=json.loads(existing.read_text(encoding='utf-8')).get('placements',[])
    # Prepare the entire plan and validate every source before copying.
    jobs={};links=defaultdict(list);verified={};old_by=defaultdict(list)
    for old in previous:old_by[old['standard']].append(old)
    for p in data['products']:
        cat=cats[p['catalog']];category=p['category']
        for code in list(dict.fromkeys(p.get('standard_ids',[])+p.get('reference_ids',[]))):
            s=standards[code]
            if not s.get('source_file'):continue
            folder=status_folder(s,data['as_of'])
            src=Path(s['source_file']);src=src if src.is_absolute() else safe(root,src)
            if code not in verified:verified[code]=verify_pdf(src,s)
            s.update(verified[code])
            key=(p['catalog'],category,code,code in p.get('reference_ids',[]))
            if key not in jobs:
                display=s.get('display_name') or p['name']
                if s['status']=='废止':relative=Path('99_历史标准')/cat['folder']/category/filename(display,s)
                else:relative=Path(cat['folder'])/category/('参考标准' if key[3] else folder)/filename(display,s)
                dst=safe(root,relative)
                if dst.exists() and digest(dst)!=s['sha256']:raise ValueError('已有文件内容冲突：'+str(relative))
                jobs[key]=dict(standard=code,path=str(relative),sha256=s['sha256'],source=src,target=dst,catalog=p['catalog'],category=category)
            links[p['id']].append(jobs[key]['path'])
    for job in jobs.values():copy_checked(job['source'],job['target'],job['sha256'])
    placements=[{k:v for k,v in j.items() if k not in ['source','target']} for j in jobs.values()]
    destinations={j['path'] for j in placements}
    # Preserve prior managed copies not in the new mapping. Never delete an
    # unmapped current file or reclassify it based on a replacement alone.
    for old in previous:
        if old['path'] in destinations:continue
        path=safe(root,old['path'])
        if not path.exists():continue
        if digest(path)!=old['sha256']:raise ValueError('既有副本发生修改：'+old['path'])
        s=standards.get(old['standard'])
        related=[j for j in placements if j['standard']==old['standard'] and j.get('catalog')==old.get('catalog') and j.get('category')==old.get('category')]
        if s and s.get('verified') and related and s.get('status') in VALID_STATES:
            # A validated replacement destination exists for this same version.
            path.unlink()
        elif s and s.get('verified') and s.get('status')=='废止':
            status_folder(s,data['as_of'])
            rel=Path('99_历史标准')/old.get('catalog','既有资料')/old.get('category','未分类')/filename(s.get('title',s['standard']),s)
            dst=safe(root,rel);copy_checked(path,dst,old['sha256']);path.unlink()
            placements.append(dict(old,path=str(rel)))
        else:placements.append(old)
    for code,s in standards.items():
        copies=[j for j in placements if j['standard']==code]
        if copies:s['source_file']=copies[0]['path']
    data['placements']=placements
    for p in data['products']:p['local_files']=links[p['id']]
    (root/'99_历史标准').mkdir(exist_ok=True)
    atomic_json(root/META,data)
    render(root,data)
    return audit(root,True)

STYLE='''body{font-family:"Microsoft YaHei",sans-serif;color:#233747;margin:30px auto;padding:0 24px;max-width:1500px;line-height:1.7}h1{font-size:26px}h2{font-size:19px}a{color:#17639b}table{width:100%;border-collapse:collapse;font-size:13px}td,th{border:1px solid #d5dfe7;padding:9px;vertical-align:top;text-align:left}th{background:#e7f0f7;position:sticky;top:0}tr:nth-child(even){background:#f7f9fb}.note{background:#edf4f8;padding:15px;margin:15px 0}.missing{color:#a45413}input,select{padding:9px;margin:12px 8px 15px 0;font-size:14px}.cards{display:flex;gap:18px;flex-wrap:wrap}.card{border:1px solid #d5dfe7;border-radius:8px;padding:20px;flex:1;min-width:250px}small{color:#617080}details{margin:8px 0}@media print{input,select{display:none}th{position:static}tr{break-inside:avoid}}'''
def page(title,body):return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>'+STYLE+'</style><h1>'+html.escape(title)+'</h1>'+body+'</html>'
def anchor(url,label):return '<a href="'+html.escape(url,quote=True)+'">'+html.escape(label)+'</a>'
def render(root,data):
    esc=html.escape;stats=[]
    for cat in data['catalogs']:
        folder=safe(root,cat['folder']);folder.mkdir(parents=True,exist_ok=True)
        products=[p for p in data['products'] if p['catalog']==cat['id']]
        row_html=[]
        for p in products:
            lines=[]
            for code in dict.fromkeys(p.get('standard_ids',[])+p.get('reference_ids',[])):
                s=data['standards'][code];status=s.get('status','待核实')
                if status=='即将实施':status='待生效（官网：即将实施）'
                if not s.get('verified'):status+='（待核实）'
                if code in p.get('reference_ids',[]):status+='；参考资料'
                official=anchor(s['official_url'],'官网') if s.get('official_url') else ''
                matched=[j for j in data['placements'] if j['standard']==code and j.get('catalog')==cat['id'] and j.get('category')==p['category']]
                local=' '.join(anchor(quote(os.path.relpath(root/j['path'],folder).replace('\\','/')),'打开PDF') for j in matched)
                if not local:local='<span class="missing">PDF未取得</span>'
                replacement=s.get('replacement_note','')
                lines.append('<div><b>'+esc(code)+'</b>　'+esc(status)+'　'+local+'　'+official+'<br><small>'+esc(s.get('title',''))+'；实施：'+esc(s.get('implemented') or '待核实')+'；核对：'+esc(s.get('checked_at','')[:10] or '未完成')+('</small><br><small>'+esc(s.get('download_note','')) if not matched else '')+('</small><br><small>'+esc(replacement) if replacement else '')+'</small></div>')
            if not lines:lines=['<span class="missing">'+esc(p.get('review_state','适用标准待核实'))+'</span>']
            note=p.get('note','')
            if p.get('review_state') and p['review_state']!='已匹配':note+=' '+p['review_state']
            if p.get('source_refs'):note+=' 原目录标准：'+p['source_refs']
            scope='<details><summary>适用范围与说明</summary>'+esc(p.get('scope',''))+'<br>'+esc(note)+'</details>' if p.get('scope') or note else ''
            row_html.append('<tr><td>'+esc(p['category'])+'</td><td><b>'+esc(p['name'])+'</b><br><small>'+esc(p.get('source_locator',''))+'</small>'+scope+'</td><td>'+''.join(lines)+'</td></tr>')
        sources=[]
        for src in cat.get('sources',[]):
            if src.get('path'):sources.append(anchor(quote(os.path.relpath(safe(root,src['path']),folder).replace('\\','/')),src['title']))
            elif src.get('url'):sources.append(anchor(src['url'],src['title']))
        count=len({j['standard'] for j in data['placements'] if j.get('catalog')==cat['id']})
        note=f'<p>核对基准日：{esc(data["as_of"])}　产品条目：{len(products)}　已保存不同标准：{count}</p><p>'+anchor('../目录导航.html','返回总目录')+'</p><div class="note">'+esc(cat.get('note',''))+'<br>目录依据：'+'；'.join(sources)+'</div>'
        script='<script>function filter(){const q=document.getElementById("q").value.toLowerCase();document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.innerText.toLowerCase().includes(q))}</script>'
        doc=page(cat['title'],note+'<input id="q" placeholder="搜索产品、标准号、状态、未取得或待核实" oninput="filter()"><table><thead><tr><th width="18%">分类</th><th width="29%">产品</th><th>适用标准与文件</th></tr></thead><tbody>'+''.join(row_html)+'</tbody></table>'+script)
        (folder/'产品与标准目录.html').write_text(doc,encoding='utf-8')
        stats.append((cat,len(products),count))
    cards=''.join('<div class="card"><h2>'+anchor(quote(c['folder']+'/产品与标准目录.html'),c['title'])+'</h2><p>'+str(n)+'个产品条目<br>'+str(k)+'份不同标准已保存</p></div>' for c,n,k in stats)
    registry_ids={s for p in data['products'] for s in p.get('standard_ids',[])}
    present={j['standard'] for j in data['placements']};missing=len(registry_ids-present)
    pending=sum(p.get('review_state') not in ['已匹配','本次检索未查到适用国标或行标'] for p in data['products'])
    body='<p>核对基准日：'+esc(data['as_of'])+'。三种目录分别维护，同一标准在各目录保留完整PDF。</p>'+cards+'<div class="note">已保存不同标准：'+str(len(present))+'；标准副本：'+str(len(data['placements']))+'；已列编号但未取得PDF：'+str(missing)+'；适用关系待核实产品条目：'+str(pending)+'。<br>“未取得PDF”不等于没有标准；未完成官网查询的条目保留待核实标记。仅官网明确废止的版本进入历史目录。<br>查找缺件：进入对应产品目录后搜索“PDF未取得”或“待核实”。</div><p>'+anchor('99_%E5%8E%86%E5%8F%B2%E6%A0%87%E5%87%86/','打开历史标准目录')+'</p>'
    (root/'目录导航.html').write_text(page('消防产品标准资料库',body),encoding='utf-8')

def audit(root,links=False):
    root=Path(root).resolve();data=json.loads((root/META).read_text(encoding='utf-8'));errors=[]
    hashes=defaultdict(set)
    for p in data.get('placements',[]):
        f=safe(root,p['path'])
        if not f.is_file():errors.append('缺文件：'+p['path'])
        elif digest(f)!=p['sha256']:errors.append('内容变化：'+p['path'])
        hashes[p['standard']].add(p['sha256'])
        s=data['standards'].get(p['standard'],{})
        try:
            folder=status_folder(s,data['as_of'])
            expected='99_历史标准' if folder=='历史' else folder
            if expected not in Path(p['path']).parts and not (s['status']=='现行' and '参考标准' in Path(p['path']).parts):errors.append('状态路径不一致：'+p['path'])
        except (ValueError,KeyError) as ex:errors.append(str(ex))
    for code,values in hashes.items():
        if len(values)>1:errors.append('同编号副本内容不一致：'+code)
    if links:
        files=[root/'目录导航.html']+[safe(root,c['folder'])/'产品与标准目录.html' for c in data['catalogs']]
        for f in files:
            if not f.exists():errors.append('缺索引：'+str(f));continue
            for href in re.findall(r'href="([^"]+)"',f.read_text(encoding='utf-8')):
                href=html.unescape(href)
                if urlparse(href).scheme or href.startswith('#'):continue
                p=(f.parent/unquote(href.split('#')[0])).resolve()
                if not p.is_relative_to(root) or not p.exists():errors.append('失效链接：'+href)
    return dict(products=len(data['products']),standards=len(data['standards']),copies=len(data.get('placements',[])),unique_pdfs=len({p['standard'] for p in data.get('placements',[])}),errors=errors)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['audit','validate','build']);parser.add_argument('--root');parser.add_argument('--registry');parser.add_argument('--config')
    a=parser.parse_args()
    if not a.root:
        conf=Path(a.config) if a.config else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.config'))/'FireProductStandards/config.json'
        if not conf.exists():parser.error('请提供--root或配置资料库路径')
        a.root=json.loads(conf.read_text(encoding='utf-8-sig'))['root']
    if a.command=='build':
        if not a.registry:parser.error('build需要--registry')
        result=build(a.root,json.loads(Path(a.registry).read_text(encoding='utf-8')))
    else:result=audit(a.root,a.command=='validate')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['errors']:raise SystemExit(1)
if __name__=='__main__':main()
