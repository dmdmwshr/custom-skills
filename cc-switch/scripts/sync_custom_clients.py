import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path

(
    mode,
    content_root_text,
    install_root_text,
    codex_root_text,
    database_text,
    backup_root_text,
    owner,
    repository,
    selected_text,
    gemini_root_text,
) = sys.argv[1:]
selected = set(selected_text.split(','))

content_root = Path(content_root_text).resolve()
install_root = Path(install_root_text).resolve()
codex_root = Path(codex_root_text).absolute()
database_path = Path(database_text).resolve()
backup_root = None if backup_root_text == "-" else Path(backup_root_text).resolve()
reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & reparse_flag)


def normalized_resolved(path: Path, *, strict: bool) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve(strict=strict))))


def assert_plain_tree(root: Path) -> None:
    if not root.is_dir() or is_reparse(root):
        raise RuntimeError(f"skill 安装源不是普通目录：{root}")
    for directory, directory_names, file_names in os.walk(root):
        directory_path = Path(directory)
        for name in [*directory_names, *file_names]:
            child = directory_path / name
            if is_reparse(child):
                raise RuntimeError(f"skill 安装源包含重解析点：{child}")


def manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for directory, _, file_names in os.walk(root):
        directory_path = Path(directory)
        for name in sorted(file_names):
            file_path = directory_path / name
            relative = os.path.relpath(file_path, root).replace("\\", "/")
            digest = hashlib.sha256()
            with file_path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result[relative] = digest.hexdigest()
    return result


def classify_destination(destination: Path, source: Path) -> str:
    if not lexists(destination):
        return "absent"
    if is_reparse(destination):
        try:
            actual = normalized_resolved(destination, strict=True)
            expected = normalized_resolved(source, strict=True)
        except OSError as exc:
            raise RuntimeError(f"Codex skill 链接无法解析：{destination}: {exc}") from exc
        if actual != expected:
            raise RuntimeError(
                f"Codex skill 链接指向非预期来源：{destination} -> {actual}，预期 {expected}"
            )
        return "link"
    if not destination.is_dir():
        raise RuntimeError(f"Codex skill 目标不是目录：{destination}")
    if not (destination / "SKILL.md").is_file():
        raise RuntimeError(f"Codex skill 物理目录缺少 SKILL.md：{destination}")
    assert_plain_tree(destination)
    return "physical"


if not content_root.is_dir() or not install_root.is_dir():
    raise SystemExit("Skill source or installation root is missing")
expected_names = sorted(path.name for path in content_root.iterdir()
                        if path.name in selected and path.is_dir() and not path.is_symlink() and (path / "SKILL.md").is_file())
if not expected_names: raise SystemExit("No selected Skill source found")
connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
try:
    rows = [dict(r) for r in connection.execute("select * from skills where repo_owner=? and repo_name=?", (owner, repository))]
finally:
    connection.close()
by_name = {row["name"]: row for row in rows}
settings_path = database_path.parent / "settings.json"
settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.is_file() else {}
if gemini_root_text != "-":
    gemini_root = Path(gemini_root_text).absolute()
elif os.path.normcase(str(database_path)) == os.path.normcase("C:/Users/12070/.cc-switch/cc-switch.db".replace("/", os.sep)):
    config = Path(settings.get("geminiConfigDir") or "C:/Users/12070/.gemini")
    if not config.is_absolute(): raise SystemExit("Gemini override must be an absolute verified path")
    gemini_root = config / "skills"
else:
    gemini_root = None  # Isolated/non-default database never inherits the real Gemini directory.
client_roots = {"codex": codex_root, "gemini": gemini_root}
entries = []
for name in expected_names:
    row = by_name.get(name)
    enabled = {k[8:]: bool(v) for k, v in row.items() if k.startswith("enabled_")} if row else {"codex": True}
    unsupported = sorted(client for client, flag in enabled.items() if flag and client not in client_roots)
    if unsupported: raise SystemExit(f"Selected Skill has unmapped enabled clients: {name}: {','.join(unsupported)}; no publication")
    for client, root in client_roots.items():
        if not enabled.get(client): continue
        if root is None: raise SystemExit("Enabled Gemini requires an explicit -GeminiSkillsRoot for this non-production database")
        if not root.is_dir() or is_reparse(root): raise SystemExit(f"Client root missing or reparse point: {client}: {root}")
        root_norm = normalized_resolved(root, strict=True)
        install_norm = normalized_resolved(install_root, strict=True)
        try: common = os.path.commonpath([root_norm, install_norm])
        except ValueError: common = None
        if common in (root_norm, install_norm):
            raise SystemExit(f"Client root overlaps SSOT: {client}")
        kind = classify_destination(root / name, install_root / name)
        entries.append({"client": client, "root": root, "name": name, "kind": kind})
sync_method = str(settings.get("skillSyncMethod", "auto")).lower()
if sync_method not in {"auto", "symlink", "copy"}: raise SystemExit("Unsupported sync method")
if mode == "validate":
    print("clients_validated=" + str(len(entries)) + " " + " ".join(f"{client}={sum(e['client']==client for e in entries)}" for client in client_roots))
    raise SystemExit(0)
if mode != "sync" or backup_root is None or not backup_root.is_dir(): raise SystemExit("Invalid client sync request")
previous_root, staging_root, failed_root = [backup_root / name for name in ("codex-previous-live", "codex-staging", "codex-failed-new")]
for root in (previous_root, staging_root, failed_root):
    if not root.is_dir() or any(root.iterdir()): raise SystemExit("Client backup/staging directory is not empty")
for entry in entries:
    name, client = entry["name"], entry["client"]
    source = install_root / name
    assert_plain_tree(source)
    entry["manifest"] = manifest(source)
    stage = staging_root / client / name
    stage.parent.mkdir(parents=True, exist_ok=True)
    prefer_link = sync_method == "symlink" or (sync_method == "auto" and entry["kind"] in {"link", "absent"})
    entry["published_mode"] = "copy"
    if prefer_link:
        try:
            os.symlink(str(source), str(stage), target_is_directory=True)
            entry["published_mode"] = "link"
        except OSError:
            if sync_method != "auto": raise
            shutil.copytree(source, stage)
    else:
        shutil.copytree(source, stage)
    if manifest(stage) != entry["manifest"]: raise RuntimeError("Staged client differs from SSOT")
states = []
try:
    for entry in entries:
        name, client = entry["name"], entry["client"]
        destination = entry["root"] / name
        previous, stage = previous_root / client / name, staging_root / client / name
        previous.parent.mkdir(parents=True, exist_ok=True)
        state = {**entry, "had_existing": lexists(destination), "old_moved": False, "attempted": False}
        states.append(state)
        if state["had_existing"]:
            shutil.move(str(destination), str(previous)); state["old_moved"] = True
        state["attempted"] = True
        shutil.move(str(stage), str(destination))
    for entry in entries:
        destination, source = entry["root"] / entry["name"], install_root / entry["name"]
        if entry["published_mode"] == "link":
            if not is_reparse(destination) or normalized_resolved(destination, strict=True) != normalized_resolved(source, strict=True):
                raise RuntimeError("Published client link target mismatch")
        elif is_reparse(destination): raise RuntimeError("Expected physical client copy")
        if manifest(destination) != entry["manifest"]: raise RuntimeError("Published client hash mismatch")
except Exception as error:
    rollback_errors = []
    for state in reversed(states):
        name, client = state["name"], state["client"]
        destination, previous, failed = state["root"] / name, previous_root / client / name, failed_root / client / name
        failed.parent.mkdir(parents=True, exist_ok=True)
        try:
            if state["attempted"] and lexists(destination): shutil.move(str(destination), str(failed))
            if state["old_moved"] and lexists(previous): shutil.move(str(previous), str(destination))
            if lexists(destination) != state["had_existing"]: raise RuntimeError("Original existence state not restored")
        except Exception as rollback_error: rollback_errors.append(f"{client}/{name}: {rollback_error}")
    raise SystemExit(f"Client sync failed; rollback_errors={rollback_errors}: {error}") from error
print(" ".join(f"{client}_synced={sum(e['client']==client for e in entries)}" for client in client_roots))
