import hashlib
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_PATH = SKILL_DIR / "resources" / "monthly_workflow.json"
SNAPSHOT_DIR = SKILL_DIR / "resources" / "monthly_templates"


def load_config(path=None):
    config_path = Path(path) if path else CONFIG_PATH
    return json.loads(config_path.read_text(encoding="utf-8"))


def path_from_config(value):
    path = Path(value)
    if path.is_absolute():
        return path
    return SKILL_DIR / path


def template_root(config=None, override=None):
    if override:
        return Path(override)
    config = config or load_config()
    return Path(config["template_sources"]["external_root"])


def bulletin_skeleton_dir(config=None, template_dir=None):
    if template_dir:
        return Path(template_dir) / "X月通报"
    config = config or load_config()
    return Path(config["template_sources"]["bulletin_skeleton"])


def score_skeleton_dir(config=None, template_dir=None):
    if template_dir:
        return Path(template_dir) / "X月通报" / "上月巡查"
    config = config or load_config()
    return Path(config["template_sources"]["score_skeleton"])


def skill_snapshot_dir(config=None):
    config = config or load_config()
    return path_from_config(config["template_sources"]["skill_snapshot"])


def templates(config=None):
    config = config or load_config()
    return list(config["templates"])


def templates_by_key(config=None):
    return {item["key"]: item for item in templates(config)}


def template_by_key(key, config=None):
    return templates_by_key(config)[key]


def template_file_names(config=None, include_excluded=True):
    config = config or load_config()
    names = [item["file"] for item in config["templates"]]
    if include_excluded:
        names.extend(item["file"] for item in config.get("excluded_templates", []))
    return names


def legacy_template_names(config=None):
    config = config or load_config()
    return dict(config.get("legacy_template_names", {}))


def bulletin_root_files(config=None):
    config = config or load_config()
    return list(config["bulletin_root_files"])


def bulletin_root_map(config=None):
    config = config or load_config()
    by_key = templates_by_key(config)
    result = []
    for item in bulletin_root_files(config):
        root_item = dict(item)
        fallback_key = root_item.get("fallback_template_key")
        if fallback_key and fallback_key in by_key:
            root_item["library"] = by_key[fallback_key]["file"]
            if by_key[fallback_key].get("numbered_sync_file"):
                root_item["numbered"] = by_key[fallback_key]["numbered_sync_file"]
        root_item["skeleton"] = root_item["skeleton_file"]
        result.append(root_item)
    return result


def grade_outputs(config=None):
    config = config or load_config()
    return list(config["grade_outputs"])


def data_source_path(name, config=None):
    config = config or load_config()
    value = config["data_sources"][name]["path"]
    return path_from_config(value)


def reference_docs(config=None):
    config = config or load_config()
    return dict(config.get("reference_docs", {}))


def reference_doc_path(value):
    return path_from_config(value)


def sha256_file(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def external_template_path(item, config=None, template_dir=None):
    source = item.get("source", "score_skeleton")
    if source == "bulletin_skeleton":
        return bulletin_skeleton_dir(config, template_dir) / item.get("skeleton_file", item["file"])
    return score_skeleton_dir(config, template_dir) / item["file"]


def snapshot_template_path(item, config=None):
    return skill_snapshot_dir(config) / item["file"]


def root_file_name(item, year, month, pending=False, config=None):
    config = config or load_config()
    prefix = config["naming"]["pending_prefix"] if pending else ""
    return prefix + item["target"].format(year=year, month=month)


def score_dir_name(score_month, config=None):
    config = config or load_config()
    return config["directory_model"]["score_dir_name"].format(score_month=score_month)


def status_for_template(item, config=None, template_dir=None, allow_snapshot_fallback=False):
    config = config or load_config()
    external = external_template_path(item, config, template_dir)
    snapshot = snapshot_template_path(item, config)
    external_exists = external.exists()
    snapshot_exists = snapshot.exists()
    external_hash = sha256_file(external) if external_exists else None
    snapshot_hash = sha256_file(snapshot) if snapshot_exists else None
    match = external_exists and snapshot_exists and external_hash == snapshot_hash
    warnings = []
    blockers = []
    adopted = None
    source = None

    if external_exists:
        adopted = external
        source = "external"
        if not snapshot_exists:
            warnings.append("skill 内模板快照缺失，已采用外部模板。")
        elif not match:
            warnings.append("外部模板与 skill 快照哈希不一致，已采用外部模板。")
    elif snapshot_exists and allow_snapshot_fallback:
        adopted = snapshot
        source = "snapshot"
        warnings.append("外部模板缺失，当前仅为审计/dry-run 使用 skill 快照兜底。")
    else:
        blockers.append("外部模板缺失，正式生成不使用 skill 快照兜底。")

    return {
        "id": item["id"],
        "key": item["key"],
        "file": item["file"],
        "description": item.get("description", ""),
        "external_path": str(external),
        "snapshot_path": str(snapshot),
        "external_exists": external_exists,
        "snapshot_exists": snapshot_exists,
        "external_sha256": external_hash,
        "snapshot_sha256": snapshot_hash,
        "match": match,
        "adopted_path": str(adopted) if adopted else None,
        "adopted_source": source,
        "warnings": warnings,
        "blockers": blockers,
        "used_in_monthly_register": bool(item.get("used_in_monthly_register")),
        "reserved": bool(item.get("reserved")),
    }


def template_statuses(config=None, template_dir=None, allow_snapshot_fallback=False):
    config = config or load_config()
    return [
        status_for_template(item, config=config, template_dir=template_dir, allow_snapshot_fallback=allow_snapshot_fallback)
        for item in templates(config)
    ]


def validate_unique_ids(config=None):
    config = config or load_config()
    groups = {
        "templates": [item["id"] for item in config["templates"]],
        "bulletin_root_files": [item["id"] for item in config["bulletin_root_files"]],
        "grade_outputs": [item["id"] for item in config["grade_outputs"]],
    }
    errors = []
    for group, ids in groups.items():
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            errors.append({"group": group, "duplicates": duplicates})
    return errors
