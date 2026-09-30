"""Lossless archive / live compilation manager. Python 3.12+, standard library only."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from datetime import datetime

ARCHIVES = ['1、国家局信息化管理相关文件', '2、总队（省）信息化管理相关文件',
            '3、支队信息化管理相关文件', '4、市数据局和市财政局信息化相关文件', '5、其他杂项文件']
COLLECTION = '0、网络安全和信息化工作文件汇编'
LIVE = COLLECTION + '/0、文件汇编实时更新版本'
SECTIONS = ['1、消防内部文件', '2、地方相关文件', '3、其他']
MANAGED = ARCHIVES + [LIVE + '/' + p for p in SECTIONS]
LEVELS = ['国家局', '总队', '支队', '国家', '省', '市', '其他']
BUSINESS = ['综合管理及总体指导', '组织职责', '规划立项', '预算经费', '采购审计',
            '建设实施', '评审验收与档案', '运行维护', '绩效评价', '停用退出', '网络安全', '数据管理', '其他']
STATUSES = ['现行', '历史留存', '征求意见稿', '已作废', '已替代', '待核实', '临时材料']
MGMT = '归档管理'
RECORDS = [MGMT + '/文件台账.json', MGMT + '/当前目录.txt', MGMT + '/待核实事项.txt']


def digest(p):
    with open(p, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def save(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name(p.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, p)


def txt(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding='utf-8-sig')


def safe(root, rel):
    if not isinstance(rel, str) or not rel or '\\' in rel or ':' in rel:
        raise ValueError('路径必须为相对路径：' + str(rel))
    parts = PurePosixPath(rel).parts
    if rel.startswith('/') or '..' in parts or '.' in parts:
        raise ValueError('路径越界：' + rel)
    p = root.joinpath(*parts)
    if not p.resolve().is_relative_to(root.resolve()):
        raise ValueError('路径越界：' + rel)
    cursor = root
    for part in parts:
        cursor /= part
        if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
            raise ValueError('拒绝重解析点：' + rel)
    return p


def scan(root, prefixes):
    files = {}
    for prefix in prefixes:
        folder = safe(root, prefix)
        if not folder.exists():
            continue
        for p in sorted(folder.rglob('*')):
            rel = p.relative_to(root).as_posix()
            safe(root, rel)
            if p.is_file():
                files[rel] = digest(p)
    return files


def protected(root):
    paths = []
    collection = safe(root, COLLECTION)
    if collection.exists():
        paths += [p.relative_to(root).as_posix() for p in collection.iterdir()
                  if p.is_dir() and p.name != '0、文件汇编实时更新版本']
    if (root / '9、信息化流程梳理').exists():
        paths.append('9、信息化流程梳理')
    return scan(root, paths)


def name(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '＿', str(s)).strip().rstrip('. ')
    if not s or s in ('.', '..'):
        raise ValueError('文件名为空')
    return s


def stem(d):
    title = d['title']
    suffix = d.get('number') or d.get('date')
    return name(f"[{d['level']}][{d['topic']}] {title}" + (f'（{suffix}）' if suffix else ''))


def sort_key(d):
    date = d.get('date', '')
    return (LEVELS.index(d['level']), BUSINESS.index(d['business']),
            -int(date.replace('-', '')) if date else 0, stem(d), d['id'])


def validate(root, manifest):
    if manifest.get('schema_version') != 1:
        raise ValueError('不支持的台账版本')
    ids = set(); hashes = set()
    for d in manifest['documents']:
        if not d.get('id') or d['id'] in ids:
            raise ValueError('文书 id 缺失或重复')
        ids.add(d['id'])
        if (d['level'] not in LEVELS or d['business'] not in BUSINESS
                or d['status'] not in STATUSES or d['archive_category'] not in range(1, 6)
                or d['section'] not in range(1, 4)):
            raise ValueError('分类或状态无效：' + d['id'])
        category = {'国家局': 1, '总队': 2, '省': 2, '支队': 3, '市': 4, '国家': 5, '其他': 5}
        if category[d['level']] != d['archive_category']:
            raise ValueError('层级与分类不一致：' + d['id'])
        if d.get('date'):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d['date']):
                raise ValueError('日期格式错误：' + d['id'])
            datetime.strptime(d['date'], '%Y-%m-%d')
        if not any(m['role'] == 'main' for m in d['members']):
            raise ValueError('缺少主文：' + d['id'])
        if d['included'] and not any(m['role'] == 'main' and m['in_compilation'] for m in d['members']):
            raise ValueError('汇编缺少主文：' + d['id'])
        if d['included'] and d['status'] in ('已作废', '已替代'):
            raise ValueError('已作废或替代文件不得留在实时汇编：' + d['id'])
        seen_members = set()
        for m in d['members']:
            if m['role'] not in ('main', 'attachment'):
                raise ValueError('成员类型错误')
            p = safe(root, m['source'])
            if not p.is_file() or digest(p) != m['sha256']:
                raise ValueError('输入文件缺失或内容变化：' + m['source'])
            member_key = (m['sha256'], m['role'], m.get('name', '') if m['role'] == 'attachment' else '')
            if member_key in seen_members:
                raise ValueError('同一文书重复成员：' + d['id'])
            seen_members.add(member_key); hashes.add(m['sha256'])
    for d in manifest['documents']:
        if any(i not in ids or i == d['id'] for i in d.get('replaced_by', [])):
            raise ValueError('替代目标错误：' + d['id'])
    for entry in manifest.get('coverage_sources', []):
        safe(root, entry['path'])
        if entry['sha256'] not in hashes:
            raise ValueError('遗漏原始材料：' + entry['path'])
    old_path = root / RECORDS[0]
    if old_path.exists():
        old = load(old_path)
        by_id = {d['id']: d for d in manifest['documents']}
        for d in old['documents']:
            if d['id'] not in ids or not {m['sha256'] for m in d['members']}.issubset(
                    {m['sha256'] for m in by_id[d['id']]['members']}):
                raise ValueError('不得删除既有文书或历史成员：' + d['id'])


def layout(root, manifest):
    result = copy.deepcopy(manifest); files = {}; folded = {}
    numbers = {}
    for sec in range(1, 4):
        docs = sorted((d for d in result['documents'] if d['included'] and d['section'] == sec), key=sort_key)
        for i, d in enumerate(docs, 1):
            numbers[d['id']] = i
    def put(rel, m):
        k = rel.casefold()
        if k in folded:
            raise ValueError('目标路径碰撞：' + rel)
        folded[k] = rel; files[rel] = {'source': m['source'], 'sha256': m['sha256']}
    for d in result['documents']:
        s = stem(d); multi = len(d['members']) > 1
        live_members = [m for m in d['members'] if m['in_compilation']]
        live_multi = len(live_members) > 1
        d['sequence'] = numbers.get(d['id']); d['canonical_name'] = s
        for is_live in (False, True):
            if is_live and not d['included']:
                for m in d['members']: m['compilation_path'] = None
                continue
            selected = live_members if is_live else d['members']
            prefix = f"{numbers[d['id']]}、{s}" if is_live else s
            base = LIVE + '/' + SECTIONS[d['section'] - 1] if is_live else ARCHIVES[d['archive_category'] - 1]
            folder = live_multi if is_live else multi
            desired = {}
            for idx, m in enumerate(selected):
                ext = Path(m['source']).suffix.lower()
                filename = prefix + ext if m['role'] == 'main' else name(m['name'])
                desired.setdefault(filename.casefold(), []).append((idx, filename, m))
            for entries in desired.values():
                for idx, filename, m in entries:
                    if len(entries) > 1:
                        p = Path(filename); filename = p.stem + '（版本-' + m['sha256'][:12] + '）' + p.suffix
                    rel = base + '/' + (prefix + '/' if folder else '') + filename
                    safe(root, rel); put(rel, m)
                    m['compilation_path' if is_live else 'archive_path'] = rel
            if is_live:
                for m in d['members']:
                    if not m['in_compilation']: m['compilation_path'] = None
    return result, files


def make_plan(root, manifest):
    for folder in MANAGED:
        if not safe(root, folder).is_dir():
            raise ValueError('受管目录不存在：' + folder)
    validate(root, manifest)
    out, files = layout(root, manifest)
    before = scan(root, MANAGED); after = {p: v['sha256'] for p, v in files.items()}
    archive_after = {h for p, h in after.items() if p.split('/')[0] in ARCHIVES}
    if not set(before.values()).issubset(archive_after):
        raise ValueError('新布局遗漏旧档案或实时版内容，禁止执行；先将原件纳入分类留档')
    removed = {p: h for p, h in before.items() if after.get(p) != h}
    added = {p: h for p, h in after.items() if before.get(p) != h}
    return out, files, {'before': before, 'after': after, 'removed': removed, 'added': added}


def directory(manifest):
    lines = ['信息化实时汇编当前目录', '排序：机构层级 → 业务类别 → 发文时间（新到旧）', '']
    for sec, label in enumerate(SECTIONS, 1):
        lines += [label]
        for d in sorted((d for d in manifest['documents'] if d['included'] and d['section'] == sec), key=sort_key):
            lines += [f"{d['sequence']}、{d['canonical_name']}"]
            for m in d['members']:
                if m.get('compilation_path'):
                    lines += ['    ' + m['role'].replace('attachment', '附件').replace('main', '主文') + '：' + m['compilation_path']]
        lines += ['']
    return '\n'.join(lines) + '\n'


def report(manifest, plan, version, previous):
    lines = [f'版本：{version}', f'上一版本：{previous or "整理前基线（未追溯虚构历史版本）"}',
             f"文书 {len(manifest['documents'])} 件；入编 {sum(d['included'] for d in manifest['documents'])} 件。",
             '', '一、核实的业务变化'] + (manifest.get('changes') or ['无新增业务说明'])
    lines += ['', '二、新增位置（包括补齐历史留档、移动后的新位置）']
    lines += list(plan['added']) or ['无']
    lines += ['', '三、旧位置移出及对应的新位置（原始内容保存在分类档案）']
    by_hash = {}
    for p, h in plan['after'].items(): by_hash.setdefault(h, []).append(p)
    for p, h in plan['removed'].items():
        lines += [p + '\n  → ' + '\n  → '.join(by_hash.get(h, ['未收录：需核实']))]
    if not plan['removed']: lines += ['无']
    lines += ['', '四、文书状态、替代关系与依据']
    for d in manifest['documents']:
        if d['status'] in ('已作废', '已替代') or d.get('replaced_by'):
            lines += [d['canonical_name'] + '：' + d['status'] + '；后继：' + ','.join(d.get('replaced_by', [])) + '；依据：' + d.get('evidence', '')]
    lines += ['', '五、更新后完整目录（含主文和附件路径）', directory(manifest), '六、待核实事项']
    lines += manifest.get('issues') or ['无']
    return '\n'.join(lines) + '\n'


def record_snapshot(root):
    return {p: digest(root / p) if (root / p).exists() else None for p in RECORDS}


def check(root, manifest, frozen=None):
    validate(root, manifest)
    recalculated, layout_files = layout(root, manifest)
    expected = {}
    for d in manifest['documents']:
        for m in d['members']:
            for k in ('archive_path', 'compilation_path'):
                if m.get(k): expected[m[k]] = m['sha256']
    if expected != {p: v['sha256'] for p, v in layout_files.items()}:
        raise ValueError('台账路径与命名排序规则不一致')
    for old_doc, new_doc in zip(manifest['documents'], recalculated['documents']):
        if old_doc.get('sequence') != new_doc.get('sequence'):
            raise ValueError('台账序号不一致：' + old_doc['id'])
    actual = scan(root, MANAGED)
    if actual != expected:
        diff = [p for p in set(actual) | set(expected) if actual.get(p) != expected.get(p)]
        raise ValueError('目录或内容不一致：' + '; '.join(sorted(diff)[:8]))
    for entry in manifest.get('coverage_sources', []):
        if entry['sha256'] not in {m['sha256'] for d in manifest['documents'] for m in d['members']}:
            raise ValueError('历史原始材料未覆盖：' + entry['path'])
    if frozen is not None and protected(root) != frozen:
        raise ValueError('固定专项或流程目录变化')
    return {'documents': len(manifest['documents']), 'included': sum(d['included'] for d in manifest['documents']),
            'archive_files': sum(p.split('/')[0] in ARCHIVES for p in actual),
            'compilation_files': sum(p.startswith(LIVE + '/') for p in actual), 'verified': True}


def semantic(manifest):
    value = copy.deepcopy(manifest)
    for k in ('version', 'previous_version', 'transaction', 'protected_files'): value.pop(k, None)
    value.pop('changes', None)
    for d in value['documents']:
        for m in d['members']:
            m.pop('source', None); m.pop('sources', None)
    return value


def apply(root, manifest):
    mgmt = safe(root, MGMT); mgmt.mkdir(exist_ok=True)
    lock = mgmt / '.执行锁'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('存在执行锁，请先核对未完成事务')
    os.close(fd)
    try:
        out, files, plan = make_plan(root, manifest)
        old = load(root / RECORDS[0]) if (root / RECORDS[0]).exists() else None
        if not plan['added'] and not plan['removed'] and old and semantic(out) == semantic(old):
            return {'changed': False, **check(root, old, old.get('protected_files'))}
        frozen = protected(root)
        if old:
            old_frozen = old.get('protected_files', {})
            if any(frozen.get(p) != h for p, h in old_frozen.items()):
                raise ValueError('既有固定文件已变化，先核对来源，不覆盖既有基线')
        version = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        transaction = safe(root, MGMT + '/事务/' + version); transaction.mkdir(parents=True)
        record = {'version': version, 'phase': 'preparing', 'before': plan['before'],
                  'after': plan['after'], 'protected_files': frozen, 'records': record_snapshot(root),
                  'version_note': LIVE + '/版本变更说明/' + version + '_更新说明.txt'}
        save(transaction / 'transaction.json', record)
        try:
            stage = transaction / 'stage'; backup = transaction / 'before'
            for prefix in MANAGED: (stage / prefix).mkdir(parents=True, exist_ok=True)
            for rel, info in files.items():
                target = stage / rel; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(safe(root, info['source']), target)
                if digest(target) != info['sha256']: raise ValueError('暂存校验失败：' + rel)
            for prefix in MANAGED:
                shutil.copytree(safe(root, prefix), backup / prefix)
            for rel, sha in record['records'].items():
                if sha:
                    p = backup / rel; p.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(root / rel, p)
            if scan(backup, MANAGED) != plan['before']:
                raise ValueError('备份校验失败')
            if scan(root, MANAGED) != plan['before'] or protected(root) != frozen or record_snapshot(root) != record['records']:
                raise ValueError('执行前文件被其他进程修改')
            record['phase'] = 'applying'; save(transaction / 'transaction.json', record)
            for prefix in MANAGED:
                target = safe(root, prefix); retained = transaction / 'replaced' / prefix
                retained.parent.mkdir(parents=True, exist_ok=True)
                target.rename(retained)
                (stage / prefix).rename(target)
            for d in out['documents']:
                for m in d['members']:
                    m['sources'] = sorted(set(m.get('sources', []) + [m['source']]))
                    m['source'] = m['archive_path']
            out.update(version=version, previous_version=old.get('version') if old else None,
                       transaction=transaction.relative_to(root).as_posix(), protected_files=frozen)
            result = check(root, out, frozen)
            save(root / RECORDS[0], out)
            txt(root / RECORDS[1], directory(out))
            txt(root / RECORDS[2], '\n'.join(out.get('issues') or ['无']) + '\n')
            txt(root / record['version_note'], report(out, plan, version, out['previous_version']))
            save(transaction / '版本台账.json', out)
            record.update(phase='committed', result=result); save(transaction / 'transaction.json', record)
            return {'changed': True, 'version': version, 'report': record['version_note'], **result}
        except Exception as exc:
            record.update(failed_phase=record['phase'], phase='failed', error=str(exc))
            save(transaction / 'transaction.json', record)
            raise RuntimeError('执行失败，保留现场：' + str(transaction) + '；' + str(exc)) from exc
    finally:
        lock.unlink()


def recover(root, rel):
    transaction = safe(root, rel)
    if not transaction.is_relative_to(safe(root, MGMT + '/事务')):
        raise ValueError('不是本业务事务目录')
    record = load(transaction / 'transaction.json')
    if record['phase'] != 'failed' or record.get('failed_phase') != 'applying':
        raise ValueError('仅支持恢复已进入写入阶段的失败事务')
    if protected(root) != record['protected_files']:
        raise ValueError('固定目录已变化，停止恢复并核对')
    if scan(transaction / 'before', MANAGED) != record['before']:
        raise ValueError('备份损坏，停止恢复')
    current = scan(root, MANAGED)
    for p, h in current.items():
        if h not in (record['before'].get(p), record['after'].get(p)):
            raise ValueError('发现事务之外的新改动，停止恢复：' + p)
    evidence = transaction / ('failed-live-' + datetime.now().strftime('%H%M%S_%f'))
    for prefix in MANAGED:
        target = safe(root, prefix)
        if target.exists():
            hold = evidence / prefix; hold.parent.mkdir(parents=True, exist_ok=True); target.rename(hold)
        shutil.copytree(transaction / 'before' / prefix, target)
    for rel, sha in record['records'].items():
        target = safe(root, rel)
        if target.exists():
            hold = evidence / rel; hold.parent.mkdir(parents=True, exist_ok=True); target.rename(hold)
        if sha: shutil.copy2(transaction / 'before' / rel, target)
    note = safe(root, record['version_note'])
    if note.exists():
        (evidence / '说明').mkdir(parents=True, exist_ok=True); note.rename(evidence / '说明' / note.name)
    if scan(root, MANAGED) != record['before'] or record_snapshot(root) != record['records']:
        raise ValueError('恢复后验证失败，保留现场')
    record['phase'] = 'recovered'; save(transaction / 'transaction.json', record)
    return {'recovered': True, 'transaction': transaction.relative_to(root).as_posix()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory'); sub.add_parser('verify')
    for command in ('preview', 'apply'):
        sub.add_parser(command).add_argument('--manifest', required=True)
    sub.add_parser('recover').add_argument('--transaction', required=True)
    args = parser.parse_args(); root = Path(args.root).resolve()
    if not root.is_dir(): raise ValueError('业务根目录不存在')
    if args.command == 'inventory':
        result = {prefix: len(scan(root, [prefix])) for prefix in MANAGED}
    elif args.command == 'verify':
        manifest = load(root / RECORDS[0]); result = check(root, manifest, manifest.get('protected_files'))
    elif args.command == 'recover':
        result = recover(root, args.transaction)
    elif args.command == 'preview':
        out, files, plan = make_plan(root, load(args.manifest))
        result = {'documents': len(out['documents']), 'included': sum(d['included'] for d in out['documents']),
                  'add': list(plan['added']), 'remove_or_rename': list(plan['removed']),
                  'issues': out.get('issues', []), 'directory': directory(out)}
    else:
        result = apply(root, load(args.manifest))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(str(exc), file=sys.stderr); sys.exit(1)
