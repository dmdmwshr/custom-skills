"""Profile-wide lifecycle inventory. Classification never grants cleanup ownership."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    pending.replace(path)


@contextmanager
def registry_lock(path):
    lock=path.with_suffix('.lock');lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError('Browser registry is being updated') from None
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def browser_epoch(browser, profile):
    """Read the matching Windows main process, never print its command line."""
    if os.name != 'nt':
        return None
    process = 'chrome.exe' if browser == 'chrome' else 'msedge.exe'
    code = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_Process -Filter \"Name='" + process + "'\"; @($p | Where-Object { $_.CommandLine -notmatch '(?:^|\\s)--type=' } | ForEach-Object { $o=Invoke-CimMethod -InputObject $_ -MethodName GetOwner; if($o.User -eq $env:USERNAME){ [PSCustomObject]@{pid=$_.ProcessId;start=$_.CreationDate.ToUniversalTime().ToString('o');command=$_.CommandLine} } }) | ConvertTo-Json -Compress"
    try:
        shell = Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(shell), '-NoProfile', '-Command', code], capture_output=True,
            encoding='utf-8', errors='replace', timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not result.stdout.strip(): return None
        rows = json.loads(result.stdout)
        if isinstance(rows, dict): rows = [rows]
        base = Path(profile).parent
        default = Path(os.environ['LOCALAPPDATA'])/('Google/Chrome/User Data' if browser == 'chrome' else 'Microsoft/Edge/User Data')
        selected = []
        for row in rows:
            command = row.get('command') or ''
            match = re.search(r'--user-data-dir(?:=|\s+)(?:"([^"]+)"|([^\s]+))', command)
            directory = Path(match.group(1) or match.group(2)) if match else default
            if os.path.normcase(str(directory.resolve())) == os.path.normcase(str(base.resolve())):
                selected.append({'pid': row['pid'], 'start': row['start']})
        return selected[0] if len(selected) == 1 else None
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError):
        return None


def registry_path(root, profile_key):
    if not re.fullmatch(r'[0-9a-f]{64}', profile_key): raise ValueError('Invalid profile identity')
    return Path(root)/'registry'/profile_key/'executions.json'


def registry(root, profile_key):
    return read_json(registry_path(root, profile_key)) or {'schema':'BrowserExecutionRegistryV1','executions':{}}


def register(root, receipt, path):
    target = registry_path(root, receipt['profile_key'])
    key = receipt['execution_id']
    fields = ('execution_id','conversation_id','project_name','project_root','profile_key','browser_name',
        'state','created_at','finished_at','completed_at','cli_session','group_id','connection_id',
        'owned_tab_ids','saved_group_ids','browser_epoch','cleanup')
    value = {k:receipt[k] for k in fields if k in receipt}
    value['receipt_path'] = str(Path(path).resolve())
    with registry_lock(target):
        data = registry(root, receipt['profile_key'])
        data['executions'][key] = value
        data['updated_at'] = datetime.now(timezone.utc).isoformat()
        save_json(target, data)


def classify(native, saved, current, records, epoch):
    if native.get('schema') != 'NativeBrowserInventoryV1': raise ValueError('Native inventory unavailable')
    tabs = {t['id']:dict(t) for t in native['tabs']}
    connections = {c['id']:set(c['tab_ids']) for c in native['connections']}
    connected = set().union(*connections.values()) if connections else set()
    own = set(current.get('owned_tab_ids',[]))
    candidates = list(records.get('executions',{}).values())
    groups = [];saved_bindings={}
    for group in native['groups']:
        members = {t['id'] for t in tabs.values() if t['group_id']==group['id']}
        if not members: continue
        active = [cid for cid, ids in connections.items() if members & ids]
        owners = [r for r in candidates if epoch and r.get('browser_epoch')==epoch
            and r.get('profile_key')==current['profile_key'] and r.get('group_id')==group['id']
            and r.get('connection_id') in active and members.issubset(r.get('owned_tab_ids',[]))]
        if group['id']==current.get('group_id') and current.get('connection_id') in active and members.issubset(own):
            category='own_execution'; owner=current.get('project_name')
        elif len(owners)==1:
            category='other_connected_registered'; owner=owners[0].get('project_name')
        elif active:
            category='other_connected_unregistered'; owner=None
        else:
            category='open_group_without_connection'; owner=None
        groups.append({**group,'category':category,'project':owner,'tab_ids':sorted(members),'connection_ids':active})
        record=current if category=='own_execution' else owners[0] if category=='other_connected_registered' else None
        if record:
            for sid in record.get('saved_group_ids',[]):saved_bindings[sid]={'group_id':group['id'],'project':owner}
    historical=[]
    for record in candidates:
        if record.get('execution_id')==current.get('execution_id'): continue
        ids=set(record.get('cleanup',{}).get('tab_ids',[]) if record.get('cleanup') else []) | set(record.get('owned_tab_ids',[]))
        same=bool(epoch and record.get('browser_epoch')==epoch and record.get('profile_key')==current['profile_key'])
        remaining=sorted(ids & tabs.keys()) if same else []
        if not same: category='history_other_or_unverified_browser'
        elif record.get('connection_id') in connections and ids & connected: category='previous_execution_still_connected'
        elif remaining: category='previous_execution_pages_remain'
        elif record.get('group_id') in {g['id'] for g in native['groups']}: category='previous_group_requires_reconciliation'
        elif any(sid in saved for sid in record.get('saved_group_ids',[])): category='previous_saved_record_remains'
        else: category='previous_native_objects_absent'
        historical.append({'execution_id':record['execution_id'],'project':record.get('project_name'),
            'state':record.get('state'),'category':category,'remaining_tab_ids':remaining})
    historical_ids={i for h in historical if h['category']=='previous_execution_pages_remain' for i in h['remaining_tab_ids']}
    pages=[]
    for tab in tabs.values():
        if tab['id'] in own: category='own_execution'
        elif tab['id'] in connected: category='other_connected_page'
        elif tab['id'] in historical_ids: category='previous_execution_page_remains'
        elif tab['kind']=='automation_control': category='unconnected_control_page_needs_reconciliation'
        elif tab['group_id']<0: category='ordinary_ungrouped_page'
        else: category='open_page_without_automation_connection'
        pages.append({**tab,'category':category})
    saved_items=[{'saved_id':sid,'title':g.get('title',''),
        'category':'own_saved_record' if sid in saved_bindings and sid in current.get('saved_group_ids',[]) else
            'other_saved_record_verified_open' if sid in saved_bindings else
            'saved_record_with_local_marker' if g.get('hasLocalGroupId') else 'saved_history_without_native_binding',
        'native_binding':saved_bindings.get(sid),'has_local_marker':bool(g.get('hasLocalGroupId'))} for sid,g in saved.items()]
    return {'schema':'BrowserEndAuditV1','captured_at':native['captured_at'],
        'native_verified':True,'browser_epoch_verified':bool(epoch),'groups':groups,'pages':pages,
        'saved_records':saved_items,'previous_executions':historical,
        'summary':{'native_groups':len(groups),'native_pages':len(pages),
            'groups_by_category':dict(Counter(g['category'] for g in groups)),
            'pages_by_category':dict(Counter(p['category'] for p in pages)),
            'saved_by_category':dict(Counter(s['category'] for s in saved_items)),
            'previous_by_category':dict(Counter(h['category'] for h in historical))},
        'cleanup_authority':'classification_only; cleanup requires the original exact execution proof'}


def complete_verified_own_history(root, current, report, epoch):
    """Only settle known cleanup, never unknown business work or another conversation."""
    if not epoch or not report.get('native_verified') or not report.get('browser_epoch_verified'):
        return []
    records=registry(root,current['profile_key'])['executions'];completed=[]
    for item in report['previous_executions']:
        if item['category']!='previous_native_objects_absent':continue
        record=records.get(item['execution_id'],{})
        cleanup=record.get('cleanup') or {}
        if (record.get('conversation_id')!=current.get('conversation_id')
            or record.get('state')!='cleanup_pending' or record.get('browser_epoch')!=epoch
            or not cleanup.get('scheduled') or cleanup.get('saved_group_status')!='verified_absent'
            or cleanup.get('connection_release') not in {'detached','already_not_attached'}):continue
        path=Path(record.get('receipt_path',''))
        if path.parent.name!='groups' or path.parent.parent.name!='.playwright':continue
        if str(path.parent.parent.parent.resolve())!=record.get('project_root'):continue
        archive=path.parent.parent/'history'/path.stem/(record['execution_id']+'.json')
        with registry_lock(path):
            latest=read_json(path)
            value=latest if latest and latest.get('execution_id')==record['execution_id'] else read_json(archive)
            if (not value or value.get('lifecycle_schema')!='BrowserExecutionV1'
                or value.get('execution_id')!=record['execution_id']
                or value.get('conversation_id')!=current.get('conversation_id')
                or value.get('profile_key')!=current['profile_key'] or value.get('state')!='cleanup_pending'
                or value.get('browser_epoch')!=epoch
                or value.get('group_id')!=record.get('group_id')
                or value.get('cleanup')!=cleanup
                or set(value.get('owned_tab_ids',[]))!=set(record.get('owned_tab_ids',[]))):continue
            value['state']='completed';value['completed_at']=report['captured_at']
            value['cleanup']['native_pages_status']='verified_absent'
            end_audit=value.setdefault('end_audit',{})
            end_audit['post_cleanup_native']={'captured_at':report['captured_at'],
                'source':'profile_native_end_sweep','remaining_tab_ids':[],'group_absent':True}
            end_audit.setdefault('end_duties',{})['last_native_helper']='verified_absent'
            save_json(archive,value)
            if latest and latest.get('execution_id')==record['execution_id']:save_json(path,value)
        register(root,value,path);completed.append(record['execution_id'])
    return completed
