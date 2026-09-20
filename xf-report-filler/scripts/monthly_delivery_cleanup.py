"""Normalize copied skeletons without leaving template archives in delivery."""
from pathlib import Path
import hashlib
import shutil


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def clean_delivery(root, year, month, score_year, score_month, known_hashes,
                   root_names, apply=False):
    supplied_root = Path(root)
    if supplied_root.is_symlink() or (hasattr(supplied_root, 'is_junction') and supplied_root.is_junction()):
        return [], [{'message': '根目录是链接，拒绝整理'}]
    root = supplied_root.resolve()
    actions, blockers = [], []
    if not root.exists():
        return actions, blockers
    entries = list(root.rglob('*'))
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           or not p.resolve().is_relative_to(root) for p in entries):
        return [], [{'message': '目录含链接或越界路径，拒绝整理'}]
    planned_targets = {}
    for src in entries:
        if not src.is_file():
            continue
        parts = src.relative_to(root).parts
        archived = '_模板副本归档' in parts
        placeholder = any('YYYY' in p or '（X-1）' in p or p.startswith('X月') for p in parts)
        if not archived and not placeholder:
            continue
        sha = digest(src)
        if sha in known_hashes:
            actions.append({'kind': 'delete_verified_template_copy', 'src': str(src), 'sha256': sha})
            continue
        converted = []
        for part in parts:
            if part == '_模板副本归档':
                continue
            if part in root_names and len(converted) == 0:
                part = root_names[part].format(year=year, month=month)
            else:
                part = part.replace('YYYY', str(score_year)).replace('（X-1）', str(score_month))
                if part.startswith('X月'):
                    part = part.replace('X月', f'{month}月', 1)
            converted.append(part)
        dst = root.joinpath(*converted)
        if dst == src or any('YYYY' in p or '（X-1）' in p for p in dst.parts):
            blockers.append({'message': '无法判断人工文件应迁往的位置', 'src': str(src)})
            continue
        other = planned_targets.get(dst)
        if (dst.exists() and (not dst.is_file() or digest(dst) != sha)) or (other and other != sha):
            blockers.append({'message': '目标内容冲突，保留原文件', 'src': str(src), 'dst': str(dst)})
            continue
        planned_targets[dst] = sha
        actions.append({'kind': 'migrate_placeholder_material', 'src': str(src), 'dst': str(dst), 'sha256': sha})
    if blockers or not apply:
        return actions, blockers
    for action in actions:
        src = Path(action['src'])
        if digest(src) != action['sha256']:
            raise RuntimeError(f'执行前文件发生变化，停止：{src}')
        if 'dst' in action:
            dst = Path(action['dst'])
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            if digest(dst) != action['sha256']:
                raise RuntimeError(f'迁移校验失败，保留原件：{src}')
        src.unlink()
    for path in sorted((p for p in entries if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if path.exists() and not any(path.iterdir()) and any(
            'YYYY' in p or '（X-1）' in p or p == '_模板副本归档' for p in path.relative_to(root).parts
        ):
            path.rmdir()
    return actions, blockers
