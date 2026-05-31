#!/usr/bin/env python3
"""Repair Codex Desktop session metadata for a project root.

The script is intentionally conservative:
- It discovers sessions by exact session_meta cwd prefix.
- It backs up mutable metadata before writing.
- It refuses global-state writes while Codex is running unless explicitly allowed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
from typing import Any


def normalize_path(value: str) -> str:
    value = value.replace("\\\\?\\", "")
    return os.path.normcase(os.path.normpath(value))


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def get_codex_home(arg: str | None) -> pathlib.Path:
    if arg:
        return pathlib.Path(arg).expanduser().resolve()
    env = os.environ.get("CODEX_HOME")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env).expanduser().resolve()
    return pathlib.Path.home() / ".codex"


def running_codex_processes() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Process | Where-Object { $_.ProcessName -match '^(Codex|codex)$' } | "
            "Select-Object Id,ProcessName,Path | ConvertTo-Json -Compress",
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        parsed = json.loads(completed.stdout)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return [
            {
                "id": item.get("Id"),
                "name": item.get("ProcessName"),
                "path": item.get("Path"),
            }
            for item in parsed
        ]
    except Exception:
        return []


def read_jsonl_first(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            line = handle.readline()
        return json.loads(line)
    except Exception:
        return None


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("input_text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return ""


def short_title(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:80] if compact else "Codex session"


def discover_project_sessions(codex_home: pathlib.Path, project_root: str) -> list[dict[str, Any]]:
    sessions_dir = codex_home / "sessions"
    target = normalize_path(project_root)
    rows: list[dict[str, Any]] = []
    if not sessions_dir.exists():
        return rows

    for path in sessions_dir.rglob("*.jsonl"):
        first = read_jsonl_first(path)
        if not first or first.get("type") != "session_meta":
            continue
        payload = first.get("payload") or {}
        session_id = payload.get("id")
        cwd = payload.get("cwd") or ""
        normalized_cwd = normalize_path(cwd)
        if not session_id or not normalized_cwd.startswith(target):
            continue

        title = "Codex session"
        updated_at = payload.get("timestamp") or iso_now()
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if item.get("timestamp"):
                        updated_at = item["timestamp"]
                    item_payload = item.get("payload") or {}
                    if title == "Codex session" and item.get("type") == "response_item" and item_payload.get("role") == "user":
                        text = extract_text(item_payload.get("content"))
                        if text and not text.startswith("<environment_context>"):
                            title = short_title(text)
        except Exception:
            pass

        rows.append(
            {
                "id": session_id,
                "cwd": cwd.replace("\\\\?\\", ""),
                "rollout_path": str(path),
                "title": title,
                "updated_at": updated_at,
            }
        )

    rows.sort(key=lambda item: item["updated_at"])
    return rows


def load_session_index(path: pathlib.Path) -> tuple[list[str], set[str]]:
    lines: list[str] = []
    ids: set[str] = set()
    if not path.exists():
        return lines, ids
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            lines.append(line.rstrip("\n"))
            try:
                ids.add(json.loads(line)["id"])
            except Exception:
                pass
    return lines, ids


def read_threads_from_sqlite(codex_home: pathlib.Path, project_root: str) -> list[dict[str, Any]]:
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        return []
    target = normalize_path(project_root)
    rows: list[dict[str, Any]] = []
    con = sqlite3.connect(str(db_path), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute("select id,cwd,title,archived,rollout_path,updated_at_ms from threads order by updated_at asc"):
            cwd = row["cwd"] or ""
            if normalize_path(cwd).startswith(target):
                rows.append(dict(row))
    finally:
        con.close()
    return rows


def exact_ui_cwd_report(sqlite_threads: list[dict[str, Any]], project_root: str) -> dict[str, Any]:
    exact = [item for item in sqlite_threads if item.get("cwd") == project_root]
    extended = [
        item
        for item in sqlite_threads
        if isinstance(item.get("cwd"), str) and item["cwd"].startswith("\\\\?\\")
    ]
    mismatched = [item for item in sqlite_threads if item.get("cwd") != project_root]
    risk = bool(sqlite_threads and mismatched)
    command = None
    if risk:
        command = (
            "Run same-root cwd normalization: "
            f'python restore_codex_project_sessions.py --project-root "{project_root}" '
            "--write --normalize-windows-extended-paths"
        )
    return {
        "sqlite_exact_ui_cwd_threads": len(exact),
        "sqlite_non_exact_ui_cwd_threads": len(mismatched),
        "sqlite_extended_path_threads": len(extended),
        "cwd_exact_match_risk": risk,
        "cwd_exact_match_risk_ids": [item["id"] for item in mismatched],
        "recommended_cwd_normalization": command,
    }


def update_jsonl_cwd(path: pathlib.Path, project_root: str) -> bool:
    changed = False
    output: list[str] = []
    target = normalize_path(project_root)

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                output.append(line)
                continue
            try:
                item = json.loads(line)
            except Exception:
                output.append(line)
                continue

            payload = item.get("payload")
            if isinstance(payload, dict):
                cwd = payload.get("cwd")
                if isinstance(cwd, str) and normalize_path(cwd) == target and cwd != project_root:
                    payload["cwd"] = project_root
                    changed = True

            output.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    if changed:
        path.write_text("".join(output), encoding="utf-8")
    return changed


def jsonl_needs_cwd_update(path: pathlib.Path, project_root: str) -> bool:
    target = normalize_path(project_root)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and normalize_path(cwd) == target and cwd != project_root:
                return True
    return False


def normalize_sqlite_and_jsonl_cwd(
    codex_home: pathlib.Path,
    project_root: str,
    sqlite_threads: list[dict[str, Any]],
    backups: dict[str, str | None],
) -> dict[str, Any]:
    db_path = codex_home / "state_5.sqlite"
    target = normalize_path(project_root)
    changed_ids = [
        item["id"]
        for item in sqlite_threads
        if item.get("cwd") != project_root and normalize_path(str(item.get("cwd") or "")) == target
    ]
    rollout_paths = [
        pathlib.Path(str(item["rollout_path"]).replace("\\\\?\\", ""))
        for item in sqlite_threads
        if item.get("rollout_path")
    ]

    if changed_ids:
        backups.setdefault("sqlite", backup(db_path, "normalize-cwd-codex-session-recovery"))
        con = sqlite3.connect(str(db_path), timeout=10)
        try:
            for session_id in changed_ids:
                con.execute("update threads set cwd=? where id=?", (project_root, session_id))
            con.commit()
        finally:
            con.close()

    changed_jsonl_files: list[str] = []
    for path in sorted(set(rollout_paths)):
        if not path.exists():
            continue
        if jsonl_needs_cwd_update(path, project_root):
            backup(path, "normalize-cwd-codex-session-recovery")
            update_jsonl_cwd(path, project_root)
            changed_jsonl_files.append(str(path))

    return {
        "normalized_sqlite_cwd_ids": changed_ids,
        "normalized_jsonl_cwd_files": changed_jsonl_files,
    }


def backup(path: pathlib.Path, label: str) -> str | None:
    if not path.exists():
        return None
    dst = path.with_name(f"{path.name}.{label}-{timestamp()}.bak")
    shutil.copy2(path, dst)
    return str(dst)


def update_global_state(global_path: pathlib.Path, project_root: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any]
    if global_path.exists():
        state = json.loads(global_path.read_text(encoding="utf-8"))
    else:
        state = {}

    ids = [item["id"] for item in sessions]
    project_order = state.setdefault("project-order", [])
    if project_root not in project_order:
        project_order.append(project_root)

    workspace_roots = state.setdefault("electron-saved-workspace-roots", [])
    if project_root not in workspace_roots:
        workspace_roots.append(project_root)

    active_roots = state.setdefault("active-workspace-roots", [])
    if project_root not in active_roots:
        active_roots.append(project_root)

    hints = state.setdefault("thread-workspace-root-hints", {})
    assignments = state.setdefault("thread-project-assignments", {})
    writable_roots = state.setdefault("thread-writable-roots", {})
    sidebar_orders = state.setdefault("sidebar-project-thread-orders", {})

    for item in sessions:
        session_id = item["id"]
        hints[session_id] = project_root
        assignments[session_id] = project_root
        writable_roots[session_id] = [project_root]

    existing_order = sidebar_orders.get(project_root, [])
    merged = list(dict.fromkeys([*ids, *existing_order]))
    sidebar_orders[project_root] = merged

    projectless = state.get("projectless-thread-ids")
    if isinstance(projectless, list):
        state["projectless-thread-ids"] = [item for item in projectless if item not in ids]

    global_path.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--codex-home")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--allow-running-codex", action="store_true")
    parser.add_argument(
        "--normalize-windows-extended-paths",
        action="store_true",
        help="When writing, normalize matching SQLite/JSONL cwd values to the exact project root string used by the UI.",
    )
    args = parser.parse_args()

    codex_home = get_codex_home(args.codex_home)
    project_root = str(pathlib.Path(args.project_root).resolve())
    processes = running_codex_processes()

    sessions = discover_project_sessions(codex_home, project_root)
    sqlite_threads = read_threads_from_sqlite(codex_home, project_root)

    index_path = codex_home / "session_index.jsonl"
    global_path = codex_home / ".codex-global-state.json"
    _, index_ids = load_session_index(index_path)

    global_state: dict[str, Any] = {}
    if global_path.exists():
        try:
            global_state = json.loads(global_path.read_text(encoding="utf-8"))
        except Exception:
            global_state = {}

    def missing_metadata() -> tuple[list[str], list[str], list[str], list[str]]:
        _, current_index_ids = load_session_index(index_path)
        current_global_state: dict[str, Any] = {}
        if global_path.exists():
            try:
                current_global_state = json.loads(global_path.read_text(encoding="utf-8"))
            except Exception:
                current_global_state = {}
        current_hints = current_global_state.get("thread-workspace-root-hints") or {}
        current_assignments = current_global_state.get("thread-project-assignments") or {}
        current_orders = current_global_state.get("sidebar-project-thread-orders") or {}
        current_ids = [item["id"] for item in sessions]
        current_order = current_orders.get(project_root) or []
        return (
            [item["id"] for item in sessions if item["id"] not in current_index_ids],
            [item for item in current_ids if current_hints.get(item) != project_root],
            [item for item in current_ids if current_assignments.get(item) != project_root],
            [item for item in current_ids if item not in current_order],
        )

    missing_index, missing_hints, missing_assignments, missing_order = missing_metadata()

    can_write_global = not processes or args.allow_running_codex
    backups: dict[str, str | None] = {}
    write_performed = False
    write_blocked_reason = None
    cwd_normalization: dict[str, Any] = {
        "normalized_sqlite_cwd_ids": [],
        "normalized_jsonl_cwd_files": [],
    }

    if args.write:
        if processes and not args.allow_running_codex:
            write_blocked_reason = "Codex processes are running; close Codex Desktop or pass --allow-running-codex."
        else:
            backups["session_index"] = backup(index_path, "restore-codex-session-recovery")
            backups["global_state"] = backup(global_path, "restore-codex-session-recovery")
            if missing_index:
                with index_path.open("a", encoding="utf-8", newline="") as handle:
                    for item in sessions:
                        if item["id"] in missing_index:
                            record = {
                                "id": item["id"],
                                "thread_name": item["title"],
                                "updated_at": item["updated_at"],
                            }
                            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            update_global_state(global_path, project_root, sessions)
            if args.normalize_windows_extended_paths:
                cwd_normalization = normalize_sqlite_and_jsonl_cwd(codex_home, project_root, sqlite_threads, backups)
                sqlite_threads = read_threads_from_sqlite(codex_home, project_root)
            missing_index, missing_hints, missing_assignments, missing_order = missing_metadata()
            write_performed = True

    cwd_report = exact_ui_cwd_report(sqlite_threads, project_root)

    result = {
        "codex_home": str(codex_home),
        "project_root": project_root,
        "codex_processes_running": processes,
        "can_write_global_state_safely": can_write_global,
        "exact_project_sessions": len(sessions),
        "sqlite_project_threads": len(sqlite_threads),
        **cwd_report,
        "missing_from_session_index": len(missing_index),
        "missing_session_index_ids": missing_index,
        "missing_global_hints": len(missing_hints),
        "missing_global_hint_ids": missing_hints,
        "missing_project_assignments": len(missing_assignments),
        "missing_project_assignment_ids": missing_assignments,
        "missing_sidebar_order": len(missing_order),
        "missing_sidebar_order_ids": missing_order,
        "write_requested": args.write,
        "write_performed": write_performed,
        "write_blocked_reason": write_blocked_reason,
        "cwd_normalization": cwd_normalization,
        "backups": backups,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not write_blocked_reason else 2


if __name__ == "__main__":
    raise SystemExit(main())
