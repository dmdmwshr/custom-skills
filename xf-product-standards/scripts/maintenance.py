"""Read-only maintenance planning and exact, manifest-based scratch cleanup."""
from pathlib import Path
from datetime import date, datetime, timedelta
import argparse, json, os, shutil
import library as lib

DEFAULT_RESOURCES={'min_free_bytes':5*1024**3,'max_work_bytes':2*1024**3,'max_download_bytes':256*1024**2,'cleanup_after_days':7}

def resources(work_dir,limits=None):
    limits=dict(DEFAULT_RESOURCES,**(limits or {}));work=Path(work_dir).resolve()
    probe=work
    while not probe.exists():
        if probe==probe.parent:return dict(work_bytes=0,free_bytes=0,limits=limits,downloads_allowed=False,errors=['工作盘不可用：'+str(probe)])
        probe=probe.parent
    used=sum(p.stat().st_size for p in work.rglob('*') if p.is_file() and not p.is_symlink()) if work.exists() else 0
    free=shutil.disk_usage(probe).free
    return dict(work_bytes=used,free_bytes=free,limits=limits,downloads_allowed=free>=limits['min_free_bytes'] and used<limits['max_work_bytes'])

def plan(root,work_dir,on_date=None,limits=None):
    root=Path(root).resolve();data=json.loads((root/lib.META).read_text('utf-8'));today=date.fromisoformat(on_date) if on_date else date.today()
    local=lib.audit(root,True);due=[];stale=[]
    for code,s in data['standards'].items():
        try:
            checked=date.fromisoformat(s['checked_at'][:10])
            if (today-checked).days>=90:stale.append(code)
            effective=date.fromisoformat(s['implemented']) if s.get('implemented') else None
            if s.get('status')=='即将实施' and effective and effective<=today+timedelta(days=30):due.append(dict(standard=code,implemented=s['implemented'],official_url=s['official_url'],overdue=effective<=today))
        except (ValueError,KeyError):pass # local audit carries the diagnostic
    present=set()
    for j in data.get('placements',[]):
        try:
            f=lib.safe(root,j['path'])
            if f.is_file() and lib.digest(f)==j['sha256']:present.add(j['standard'])
        except ValueError:pass # audit already reports the exact bad path
    resource_report=resources(work_dir,limits)
    return dict(on_date=today.isoformat(),local_audit=local,due_status_review=sorted(due,key=lambda s:(s['implemented'],s['standard'])),missing_pdfs=sorted(set(data['standards'])-present),stale_status_review=sorted(stale),catalog_sources=[dict(catalog=c['id'],sources=c.get('sources',[])) for c in data['catalogs']],resources=resource_report,online_verified=False,errors=local['errors']+resource_report.get('errors',[]))

def cleanup(root,work_dir,manifest,apply=False,on_date=None,limits=None):
    """Delete only a specifically registered, archived duplicate, never a tree."""
    root=Path(root).resolve();work_raw=Path(work_dir)
    if work_raw.is_symlink() or (hasattr(work_raw,'is_junction') and work_raw.is_junction()):raise ValueError('临时目录不能是链接')
    work=work_raw.resolve()
    if root==work or root.is_relative_to(work) or work.is_relative_to(root):raise ValueError('临时目录须在正式资料库外')
    skill_container=Path(__file__).resolve().parents[2]
    if work==skill_container or work.is_relative_to(skill_container):raise ValueError('临时目录须在Skill源码及安装目录外')
    mf=Path(manifest).resolve()
    if not mf.is_relative_to(work):raise ValueError('清单须在专属临时目录内')
    data=json.loads((root/lib.META).read_text('utf-8'));entries=json.loads(mf.read_text('utf-8'))
    limits=dict(DEFAULT_RESOURCES,**(limits or {}));today=date.fromisoformat(on_date) if on_date else date.today()
    result={'apply':apply,'candidates':[],'deleted':[],'preserved':[],'reclaimed_bytes':0,'errors':[]}
    if not isinstance(entries,list):raise ValueError('清理清单必须为数组')
    for item in entries:
        if not isinstance(item,dict):
            result['errors'].append('清单条目不是对象');continue
        reason=None;p=None
        try:
            p=lib.safe(work,item['path'])
            if not p.exists():continue
            if p==mf:raise ValueError('清单本身不得清理')
            if not p.is_file():raise ValueError('只清理精确文件')
            if item.get('state')!='archived_verified' or item.get('active') is not False or item.get('recovery_critical') is not False or item.get('resumable') is not False:raise ValueError('活动、断点或恢复依赖未排除')
            if (today-date.fromisoformat(item['archived_at'][:10])).days<limits['cleanup_after_days'] or (today-date.fromtimestamp(p.stat().st_mtime)).days<limits['cleanup_after_days']:raise ValueError('尚在保留期')
            code=item['standard'];sha=item['sha256'];s=data['standards'].get(code,{})
            if s.get('sha256')!=sha or lib.digest(p)!=sha:raise ValueError('正式记录或临时文件哈希不符')
            valid=False
            for j in data.get('placements',[]):
                if j['standard']==code and j['sha256']==sha:
                    copy=lib.safe(root,j['path'])
                    if copy.is_file() and lib.digest(copy)==sha:valid=True;break
            if not valid:raise ValueError('未找到已验证的正式副本')
            size=p.stat().st_size
            result['candidates'].append(dict(path=item['path'],bytes=size,sha256=sha))
            if apply:
                # Re-read immediately before unlink; no recursive removal.
                if lib.digest(p)!=sha:raise ValueError('清理前文件发生变化')
                p.unlink();result['deleted'].append(item['path']);result['reclaimed_bytes']+=size
        except (ValueError,KeyError,OSError,TypeError,AttributeError) as ex:reason=str(ex)
        if reason:result['preserved'].append(dict(path=item.get('path'),reason=reason))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['plan','resources','cleanup']);p.add_argument('--config');p.add_argument('--root');p.add_argument('--work-dir');p.add_argument('--on-date');p.add_argument('--manifest');p.add_argument('--apply',action='store_true')
    a=p.parse_args();conf=Path(a.config) if a.config else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.config'))/'FireProductStandards/config.json'
    config=json.loads(conf.read_text('utf-8-sig')) if conf.exists() else {}
    root=a.root or config.get('root');work=a.work_dir or config.get('work_dir');limits=config.get('resources',{})
    if not work or (a.command!='resources' and not root):p.error('需配置 root 和 work_dir')
    if a.command=='plan':result=plan(root,work,a.on_date,limits)
    elif a.command=='resources':result=resources(work,limits)
    else:
        if not a.manifest:p.error('cleanup 必须提供精确 manifest')
        result=cleanup(root,work,a.manifest,a.apply,a.on_date,limits)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result.get('errors'):raise SystemExit(1)

if __name__=='__main__':main()
