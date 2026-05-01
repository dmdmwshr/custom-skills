#!/usr/bin/env python3
"""Manage cc-switch skill_repos safely."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_DB = Path.home() / ".cc-switch" / "cc-switch.db"


def parse_enabled(value: str) -> int:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return 1
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return 0
    raise argparse.ArgumentTypeError("enabled must be 1/0, true/false, yes/no, or on/off")


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"cc-switch-skill-repos-before-{stamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def require_yes(args: argparse.Namespace) -> None:
    if args.dry_run:
        return
    if not args.yes:
        raise SystemExit("Refusing to mutate without --yes. Use --dry-run first.")


def repo_exists(con: sqlite3.Connection, owner: str, name: str) -> bool:
    row = con.execute(
        "select 1 from skill_repos where owner=? and name=?",
        (owner, name),
    ).fetchone()
    return row is not None


def impacted_skills(con: sqlite3.Connection, owner: str, name: str) -> list[sqlite3.Row]:
    return con.execute(
        """
        select id, name, repo_branch, enabled_claude, enabled_codex, enabled_gemini, enabled_opencode
        from skills
        where repo_owner=? and repo_name=?
        order by name
        """,
        (owner, name),
    ).fetchall()


def print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("(none)")
        return
    headers = rows[0].keys()
    print("\t".join(headers))
    for row in rows:
        print("\t".join(str(row[h]) for h in headers))


def cmd_list(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        rows = con.execute(
            "select owner, name, branch, enabled from skill_repos order by owner, name"
        ).fetchall()
    print_rows(rows)


def cmd_show(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        repo_rows = con.execute(
            "select owner, name, branch, enabled from skill_repos where owner=? and name=?",
            (args.owner, args.name),
        ).fetchall()
        skill_rows = impacted_skills(con, args.owner, args.name)
    print("== skill_repos ==")
    print_rows(repo_rows)
    print("== linked skills ==")
    print_rows(skill_rows)


def cmd_add(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        if repo_exists(con, args.owner, args.name):
            raise SystemExit(f"Repository source already exists: {args.owner}/{args.name}")
        print(
            f"ADD skill_repos owner={args.owner} name={args.name} "
            f"branch={args.branch} enabled={args.enabled}"
        )
        require_yes(args)
        if args.dry_run:
            print("Dry-run only; no changes written.")
            return
        backup = backup_db(args.db)
        con.execute(
            "insert into skill_repos(owner, name, branch, enabled) values (?, ?, ?, ?)",
            (args.owner, args.name, args.branch, args.enabled),
        )
        con.commit()
    print(f"Backup: {backup}")
    print("Added.")


def cmd_enable(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        if not repo_exists(con, args.owner, args.name):
            raise SystemExit(f"Repository source not found: {args.owner}/{args.name}")
        print(f"SET enabled={args.enabled} for {args.owner}/{args.name}")
        require_yes(args)
        if args.dry_run:
            print("Dry-run only; no changes written.")
            return
        backup = backup_db(args.db)
        con.execute(
            "update skill_repos set enabled=? where owner=? and name=?",
            (args.enabled, args.owner, args.name),
        )
        con.commit()
    print(f"Backup: {backup}")
    print("Updated.")


def cmd_set_branch(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        if not repo_exists(con, args.owner, args.name):
            raise SystemExit(f"Repository source not found: {args.owner}/{args.name}")
        print(f"SET branch={args.branch} for {args.owner}/{args.name}")
        require_yes(args)
        if args.dry_run:
            print("Dry-run only; no changes written.")
            return
        backup = backup_db(args.db)
        con.execute(
            "update skill_repos set branch=? where owner=? and name=?",
            (args.branch, args.owner, args.name),
        )
        con.commit()
    print(f"Backup: {backup}")
    print("Updated.")


def cmd_remove(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        if not repo_exists(con, args.owner, args.name):
            raise SystemExit(f"Repository source not found: {args.owner}/{args.name}")
        skill_rows = impacted_skills(con, args.owner, args.name)
        print(f"REMOVE skill_repos {args.owner}/{args.name}")
        print("== linked skills ==")
        print_rows(skill_rows)
        if skill_rows and not args.delete_skills:
            print("Linked skills will be left in the skills table.")
        if skill_rows and args.delete_skills:
            print("Linked skills will also be deleted from the skills table.")
        require_yes(args)
        if args.dry_run:
            print("Dry-run only; no changes written.")
            return
        backup = backup_db(args.db)
        if args.delete_skills:
            con.execute(
                "delete from skills where repo_owner=? and repo_name=?",
                (args.owner, args.name),
            )
        con.execute(
            "delete from skill_repos where owner=? and name=?",
            (args.owner, args.name),
        )
        con.commit()
    print(f"Backup: {backup}")
    print("Removed.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage cc-switch skill_repos")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite DB path, default {DEFAULT_DB}")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List repository sources")
    list_p.set_defaults(func=cmd_list)

    show_p = sub.add_parser("show", help="Show one source and linked skills")
    show_p.add_argument("--owner", required=True)
    show_p.add_argument("--name", required=True)
    show_p.set_defaults(func=cmd_show)

    add_p = sub.add_parser("add", help="Add a repository source")
    add_p.add_argument("--owner", required=True)
    add_p.add_argument("--name", required=True)
    add_p.add_argument("--branch", default="main")
    add_p.add_argument("--enabled", type=parse_enabled, default=1)
    add_p.add_argument("--dry-run", action="store_true")
    add_p.add_argument("--yes", action="store_true")
    add_p.set_defaults(func=cmd_add)

    enable_p = sub.add_parser("enable", help="Enable or disable a repository source")
    enable_p.add_argument("--owner", required=True)
    enable_p.add_argument("--name", required=True)
    enable_p.add_argument("--enabled", type=parse_enabled, required=True)
    enable_p.add_argument("--dry-run", action="store_true")
    enable_p.add_argument("--yes", action="store_true")
    enable_p.set_defaults(func=cmd_enable)

    branch_p = sub.add_parser("set-branch", help="Change a repository source branch")
    branch_p.add_argument("--owner", required=True)
    branch_p.add_argument("--name", required=True)
    branch_p.add_argument("--branch", required=True)
    branch_p.add_argument("--dry-run", action="store_true")
    branch_p.add_argument("--yes", action="store_true")
    branch_p.set_defaults(func=cmd_set_branch)

    remove_p = sub.add_parser("remove", help="Remove a repository source")
    remove_p.add_argument("--owner", required=True)
    remove_p.add_argument("--name", required=True)
    remove_p.add_argument("--delete-skills", action="store_true")
    remove_p.add_argument("--dry-run", action="store_true")
    remove_p.add_argument("--yes", action="store_true")
    remove_p.set_defaults(func=cmd_remove)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
