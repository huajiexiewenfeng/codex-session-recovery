#!/usr/bin/env python3
"""Surface restored Codex Desktop sessions in the sidebar's recent cache.

Codex Desktop 26.527 builds the project sidebar from a recent-thread cache that
starts with ``thread/list limit: 50``. Older, correctly restored sessions can
remain hidden when they sit beyond that first global page. This script promotes
a bounded, round-robin set of interactive local threads by updating both the
JSONL rollout's final ``task_complete.completed_at`` and the SQLite
``updated_at`` ordering fields, after backing up the changed files.

Updating SQLite alone is not durable: app-server read-repair can rebuild the
state DB from JSONL rollouts and revert the surfaced order on restart.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import time
from typing import Any


INTERACTIVE_SOURCES = {"appServer", "cli", "vscode"}


def normalize_path(value: str) -> str:
    value = value.replace("\\\\?\\", "")
    return os.path.normcase(os.path.normpath(value))


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_from_ms(timestamp_ms: int) -> str:
    return (
        dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def get_codex_home(arg: str | None) -> pathlib.Path:
    if arg:
        return pathlib.Path(arg).expanduser().resolve()
    env = os.environ.get("CODEX_HOME")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env).expanduser().resolve()
    return pathlib.Path.home() / ".codex"


def is_interactive_source(source: str | None) -> bool:
    return source in INTERACTIVE_SOURCES


def read_global_state_roots(codex_home: pathlib.Path) -> list[str]:
    path = codex_home / ".codex-global-state.json"
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    roots: list[str] = []
    for key in (
        "project-order",
        "electron-saved-workspace-roots",
        "active-workspace-roots",
    ):
        value = state.get(key)
        if isinstance(value, list):
            roots.extend(str(item) for item in value if isinstance(item, str) and item.strip())

    sidebar_orders = state.get("sidebar-project-thread-orders")
    if isinstance(sidebar_orders, dict):
        roots.extend(str(key) for key in sidebar_orders if isinstance(key, str) and key.strip())

    seen: set[str] = set()
    ordered: list[str] = []
    for root in roots:
        norm = normalize_path(root)
        if norm in seen:
            continue
        seen.add(norm)
        ordered.append(root)
    return ordered


def read_threads(db_path: pathlib.Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in con.execute(
                """
                select id, rollout_path, cwd, title, source, archived, updated_at, updated_at_ms, created_at
                from threads
                order by updated_at desc, created_at desc
                """
            )
        ]
    finally:
        con.close()


def eligible_thread(row: dict[str, Any]) -> bool:
    if int(row.get("archived") or 0) != 0:
        return False
    if not is_interactive_source(row.get("source")):
        return False
    cwd = row.get("cwd")
    return isinstance(cwd, str) and bool(cwd.strip())


def select_threads_for_sidebar(
    rows: list[dict[str, Any]],
    project_roots: list[str],
    *,
    per_project: int,
    max_total: int,
) -> list[dict[str, Any]]:
    if per_project < 0 or max_total < 0:
        return []

    grouped: dict[str, list[dict[str, Any]]] = {}
    display_by_norm: dict[str, str] = {}
    for row in rows:
        if not eligible_thread(row):
            continue
        cwd = str(row["cwd"])
        norm = normalize_path(cwd)
        display_by_norm.setdefault(norm, cwd)
        grouped.setdefault(norm, []).append(row)

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                int(item.get("updated_at") or 0),
                int(item.get("created_at") or 0),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )

    ordered_norms: list[str] = []
    seen: set[str] = set()
    for root in project_roots:
        norm = normalize_path(root)
        if norm in grouped and norm not in seen:
            ordered_norms.append(norm)
            seen.add(norm)

    remaining = sorted(
        (norm for norm in grouped if norm not in seen),
        key=lambda norm: int(grouped[norm][0].get("updated_at") or 0),
        reverse=True,
    )
    ordered_norms.extend(remaining)

    if not ordered_norms:
        return []

    project_limit = per_project
    if project_limit == 0:
        project_limit = max(len(grouped[norm]) for norm in ordered_norms)

    selected: list[dict[str, Any]] = []
    for index in range(project_limit):
        for norm in ordered_norms:
            items = grouped[norm]
            if index >= len(items):
                continue
            item = dict(items[index])
            item["_project_root"] = display_by_norm.get(norm, str(item["cwd"]))
            selected.append(item)
            if max_total > 0 and len(selected) >= max_total:
                return selected
    return selected


def backup_database(db_path: pathlib.Path) -> pathlib.Path:
    backup_path = db_path.with_name(f"{db_path.name}.backup-sidebar-surface-{timestamp()}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def backup_global_state(global_state_path: pathlib.Path) -> pathlib.Path:
    backup_path = global_state_path.with_name(f"{global_state_path.name}.backup-sidebar-surface-{timestamp()}")
    shutil.copy2(global_state_path, backup_path)
    return backup_path


def backup_rollout(rollout_path: pathlib.Path) -> pathlib.Path:
    backup_path = rollout_path.with_name(f"{rollout_path.name}.backup-sidebar-surface-{timestamp()}")
    shutil.copy2(rollout_path, backup_path)
    return backup_path


def load_jsonl(path: pathlib.Path) -> list[tuple[str, dict[str, Any] | None]]:
    rows: list[tuple[str, dict[str, Any] | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            rows.append((line, None))
            continue
        rows.append((line, json.loads(line)))
    return rows


def write_jsonl(path: pathlib.Path, rows: list[tuple[str, dict[str, Any] | None]]) -> None:
    text = "\n".join(
        original if obj is None else json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        for original, obj in rows
    )
    path.write_text(text + "\n", encoding="utf-8")


def find_last_task_complete(rows: list[tuple[str, dict[str, Any] | None]]) -> int | None:
    found: int | None = None
    for index, (_, obj) in enumerate(rows):
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "task_complete":
            found = index
    return found


def touch_rollout_completion(rollout_path: pathlib.Path, new_updated_at: int, new_updated_at_ms: int) -> dict[str, Any]:
    rows = load_jsonl(rollout_path)
    task_index = find_last_task_complete(rows)
    if task_index is None:
        raise ValueError(f"no task_complete event found in {rollout_path}")

    task_obj = rows[task_index][1]
    assert isinstance(task_obj, dict)
    payload = task_obj.get("payload")
    assert isinstance(payload, dict)
    old_completed_at = payload.get("completed_at")
    payload["completed_at"] = new_updated_at
    task_obj["timestamp"] = iso_from_ms(new_updated_at_ms)
    write_jsonl(rollout_path, rows)
    return {
        "rollout_path": str(rollout_path),
        "old_completed_at": old_completed_at,
        "new_completed_at": new_updated_at,
    }


def update_global_state_for_threads(codex_home: pathlib.Path, selected: list[dict[str, Any]]) -> dict[str, Any]:
    global_state_path = codex_home / ".codex-global-state.json"
    if not global_state_path.exists():
        return {"updated": False, "backup_path": None, "reason": "global state file not found"}

    state = json.loads(global_state_path.read_text(encoding="utf-8"))
    changed = False
    hints = state.setdefault("thread-workspace-root-hints", {})
    assignments = state.setdefault("thread-project-assignments", {})
    writable_roots = state.setdefault("thread-writable-roots", {})
    sidebar_orders = state.setdefault("sidebar-project-thread-orders", {})

    for item in selected:
        thread_id = str(item["id"])
        project_root = str(item.get("_project_root") or item.get("cwd") or "")
        if not project_root:
            continue
        if hints.get(thread_id) != project_root:
            hints[thread_id] = project_root
            changed = True
        if assignments.get(thread_id) != project_root:
            assignments[thread_id] = project_root
            changed = True
        if writable_roots.get(thread_id) != [project_root]:
            writable_roots[thread_id] = [project_root]
            changed = True

        order = sidebar_orders.setdefault(project_root, [])
        if thread_id in order:
            order.remove(thread_id)
        order.insert(0, thread_id)
        changed = True

    if not changed:
        return {"updated": False, "backup_path": None}

    backup_path = backup_global_state(global_state_path)
    global_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"updated": True, "backup_path": str(backup_path)}


def apply_surface(db_path: pathlib.Path, selected: list[dict[str, Any]], report_path: pathlib.Path | None) -> dict[str, Any]:
    if not selected:
        return {"updated": 0, "backup_path": None, "report_path": str(report_path) if report_path else None}

    codex_home = db_path.parent
    backup_path = backup_database(db_path)
    max_existing = max(int(item.get("updated_at") or 0) for item in read_threads(db_path))
    base_seconds = max(int(time.time()), max_existing) + len(selected) + 5
    now_ms = int(time.time() * 1000)

    con = sqlite3.connect(str(db_path), timeout=30)
    rollout_backups: list[dict[str, str]] = []
    rollout_updates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        con.execute("pragma busy_timeout=30000")
        originals: list[dict[str, Any]] = []
        for index, item in enumerate(selected):
            new_updated_at = base_seconds - index
            new_updated_at_ms = new_updated_at * 1000
            rollout_path = pathlib.Path(str(item.get("rollout_path") or ""))
            try:
                if not rollout_path.exists():
                    raise FileNotFoundError(str(rollout_path))
                rollout_backup = backup_rollout(rollout_path)
                rollout_backups.append({"id": str(item["id"]), "backup_path": str(rollout_backup)})
                rollout_updates.append(touch_rollout_completion(rollout_path, new_updated_at, new_updated_at_ms))
            except Exception as exc:
                errors.append({"id": str(item.get("id")), "error": str(exc)})
                continue

            originals.append(
                {
                    "id": item["id"],
                    "cwd": item["cwd"],
                    "title": item.get("title"),
                    "rollout_path": str(rollout_path),
                    "old_updated_at": item.get("updated_at"),
                    "old_updated_at_ms": item.get("updated_at_ms"),
                    "new_updated_at": new_updated_at,
                    "new_updated_at_ms": new_updated_at_ms,
                    "project_root": item.get("_project_root"),
                }
            )
            con.execute(
                "update threads set updated_at=?, updated_at_ms=? where id=?",
                (new_updated_at, new_updated_at_ms, item["id"]),
            )
        con.commit()
    finally:
        con.close()

    global_state = update_global_state_for_threads(codex_home, selected)

    payload = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sqlite_backup_path": str(backup_path),
        "rollout_backups": rollout_backups,
        "global_state": global_state,
        "db_path": str(db_path),
        "updated": len(originals),
        "requested": len(selected),
        "errors": errors,
        "note": "SQLite and JSONL task_complete timestamps were changed so app-server read-repair keeps restored threads in Codex Desktop's recent cache.",
        "threads": originals,
        "rollout_updates": rollout_updates,
        "generated_at_ms": now_ms,
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"updated": len(originals), "backup_path": str(backup_path), "report_path": str(report_path) if report_path else None}


def build_report(
    codex_home: pathlib.Path,
    rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    per_project: int,
    max_total: int,
) -> dict[str, Any]:
    eligible = [row for row in rows if eligible_thread(row)]
    by_project: dict[str, int] = {}
    for row in eligible:
        root = str(row.get("cwd") or "")
        by_project[root] = by_project.get(root, 0) + 1
    return {
        "codex_home": str(codex_home),
        "state_db": str(codex_home / "state_5.sqlite"),
        "eligible_interactive_threads": len(eligible),
        "eligible_projects": len(by_project),
        "per_project": per_project,
        "max_total": max_total,
        "selected_count": len(selected),
        "selected_projects": len({item.get("_project_root") for item in selected}),
        "selected": [
            {
                "id": item["id"],
                "cwd": item["cwd"],
                "title": item.get("title"),
                "old_updated_at": item.get("updated_at"),
                "project_root": item.get("_project_root"),
            }
            for item in selected
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--per-project", type=int, default=2, help="Threads to surface per project root. Use 0 for all.")
    parser.add_argument("--max-total", type=int, default=50, help="Maximum threads to promote. Use 0 for no limit.")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report-path", default=None)
    args = parser.parse_args()

    codex_home = get_codex_home(args.codex_home)
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        print(json.dumps({"error": f"state database not found: {db_path}"}, ensure_ascii=False, indent=2))
        return 2

    rows = read_threads(db_path)
    roots = read_global_state_roots(codex_home)
    selected = select_threads_for_sidebar(
        rows,
        roots,
        per_project=args.per_project,
        max_total=args.max_total,
    )
    report = build_report(codex_home, rows, selected, args.per_project, args.max_total)

    report_path = pathlib.Path(args.report_path) if args.report_path else codex_home / f"sidebar-surface-report-{timestamp()}.json"
    if args.write:
        report["write"] = apply_surface(db_path, selected, report_path)
    else:
        report["write"] = None
        report["dry_run"] = True

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
