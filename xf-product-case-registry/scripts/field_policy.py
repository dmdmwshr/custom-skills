"""One field-ownership rule for every Feishu write of this skill (2026-11-12 mechanism).

Four kinds of field, one authority each:

- supervision fact   (contract class ``source-sync-whitelist``): the supervision system is the authority;
  verified local records are synced to Feishu; brigade members read only.
- brigade-maintained (``business-label`` for the yes/no labels, ``brigade-maintained`` for free
  brigade data such as enterprise records): Feishu is the authority; every save takes effect at once.
- statistics         (``derived-readonly`` and derived field types): computed, never written.
- internal           (keys, relations, admin routing): maintained by the program or the administrator.

Four write bases and what each may touch:

- ``source-observed``  supervision-fact update. Never a label or a human field.
- ``label-initial``    the rule default "否" for a label nobody has identified yet. Only fills an empty
  cell or replaces the retired placeholder "待核实"; an explicit 是/否 is never touched.
- ``label-evidence``   verified local evidence shows a label is 是 (or 否). Fills an empty cell or the
  placeholder; replaces an explicit value only when that value is the one the assistant itself wrote
  last (the write ledger proves it), otherwise the difference is reported and the brigade's value stays.
- ``user-delegated``   the user told the assistant to record a value for them. Needs the user's own
  words and the exact current value; a different current value is a conflict that is reported, not overwritten.

The "missing never becomes 否/0" rule stays in force for supervision facts (results, counts and the
like); the label default is the only exception and it is confined to ``business-label`` fields.

Standard library only.
"""

from __future__ import annotations

from typing import Any

BASIS_SOURCE = "source-observed"
BASIS_LABEL_INITIAL = "label-initial"
BASIS_LABEL_EVIDENCE = "label-evidence"
BASIS_USER = "user-delegated"
BASIS_KINDS = (BASIS_SOURCE, BASIS_LABEL_INITIAL, BASIS_LABEL_EVIDENCE, BASIS_USER)

CLASS_SOURCE = "source-sync-whitelist"
CLASS_LABEL = "business-label"
CLASS_BRIGADE = "brigade-maintained"
CLASS_HUMAN = "human"

ALLOWED_CLASSES = {
    BASIS_SOURCE: frozenset({CLASS_SOURCE}),
    BASIS_LABEL_INITIAL: frozenset({CLASS_LABEL}),
    BASIS_LABEL_EVIDENCE: frozenset({CLASS_LABEL}),
    BASIS_USER: frozenset({CLASS_LABEL, CLASS_BRIGADE, CLASS_HUMAN}),
}

LABEL_DEFAULT = "否"
LABEL_VALUES = ("是", "否")
# Old values the rule default may replace: an empty cell and the retired placeholder.
LABEL_REPLACEABLE = (None, "待核实")

OWNERSHIP = {
    CLASS_SOURCE: "supervision-fact",
    "source-unconfirmed-readonly": "supervision-fact",
    CLASS_LABEL: "brigade-maintained",
    CLASS_BRIGADE: "brigade-maintained",
    CLASS_HUMAN: "brigade-maintained",
    "derived-readonly": "statistics",
    "admin-only": "internal",
    "structural-relation": "internal",
    "legacy-hidden-protected": "internal",
    "manual-relation-legacy": "internal",
    "history-readonly": "internal",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def ownership(field_class: str) -> str:
    """The owner category of a contract class; an unknown class is internal (nobody writes it)."""
    return OWNERSHIP.get(field_class, "internal")


def basis_problem(basis: Any) -> str | None:
    """Shape of a write basis, whatever the field. None when acceptable."""
    if not isinstance(basis, dict):
        return "每个字段需 basis"
    kind = basis.get("kind")
    if kind not in BASIS_KINDS:
        return f"basis.kind 必须是 {' / '.join(BASIS_KINDS)}"
    if not _text(basis.get("sourceRef")) or not _text(basis.get("observedAt")):
        return "basis 需要 sourceRef 与 observedAt"
    if kind == BASIS_USER and (basis.get("authorizedBy") != "user" or not _text(basis.get("userQuote"))):
        return "user-delegated 需要 authorizedBy=user 与用户原话 userQuote"
    return None


def write_problem(meta: dict[str, Any], basis: dict[str, Any], item: dict[str, Any]) -> str | None:
    """Whether this basis may write this field with this item ({new, expectedOld?}). None when allowed."""
    kind = basis["kind"]
    name, field_class = meta.get("name"), meta.get("class")
    if field_class not in ALLOWED_CLASSES[kind]:
        if kind == BASIS_SOURCE:
            return f"字段不是 {CLASS_SOURCE}（监督事实更新不能写 {ownership(field_class)} 字段）：{name}({field_class})"
        return f"{kind} 不能写 {ownership(field_class)} 字段：{name}({field_class})"
    if kind == BASIS_SOURCE:
        return None
    if "expectedOld" not in item:
        return f"{kind} 必须给出 expectedOld（写前旧值门禁）：{name}"
    if kind == BASIS_LABEL_INITIAL:
        if item.get("new") != LABEL_DEFAULT:
            return f"label-initial 只能写标签初值“{LABEL_DEFAULT}”：{name}"
        if item["expectedOld"] not in LABEL_REPLACEABLE:
            return f"label-initial 只补空白或替换“待核实”，不改已有明确值：{name}"
    if kind == BASIS_LABEL_EVIDENCE:
        if item.get("new") not in LABEL_VALUES:
            return f"label-evidence 只能写 是/否：{name}"
        if not _text(basis.get("evidenceSha256")):
            return f"label-evidence 需要证据摘要 evidenceSha256：{name}"
    return None


def needs_explicit_negative(basis: dict[str, Any]) -> bool:
    """Only supervision facts need a source-stated negative; the label default and the user's own value do not."""
    return basis.get("kind") == BASIS_SOURCE


def label_conflict(current: Any, evidence_value: str) -> dict[str, Any] | None:
    """New evidence against a label that already holds an explicit value: report the difference, never overwrite."""
    if current in LABEL_VALUES and evidence_value in LABEL_VALUES and current != evidence_value:
        return {"current": current, "evidence": evidence_value, "action": "REPORT_ONLY"}
    return None
