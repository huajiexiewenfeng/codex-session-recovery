#!/usr/bin/env python3
"""Reparent Codex threads from one workspace root to another display root."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3


def normalize_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value.replace("\\\\?\\", "")))


def same_project_root(value: str, project_root: str) -> bool:
    return normalize_path(value) == normalize_path(project_root)


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(path: pathlib.Path, label: str, stamp: str) -> str | None:
    if not path.exists():
        return None
    dst = path.with_name(f"{path.name}.{label}-{stamp}.bak")
    shutil.copy2(path, dst)
    return str(dst)


def update_jsonl_cwd(path: pathlib.Path, old_root: str, new_root: str, stamp: str) -> bool:
    changed = False
    output: list[str] = []

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
                if isinstance(cwd, str) and same_project_root(cwd, old_root):
                    payload["cwd"] = new_root
                    changed = True

            output.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    if changed:
        backup(path, "reparent-codex-session-recovery", stamp)
        path.write_text("".join(output), encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", default=str(pathlib.Path.home() / ".codex"))
    parser.add_argument("--old-root", required=True)
    parser.add_argument("--new-root", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    codex_home = pathlib.Path(args.codex_home).expanduser().resolve()
    old_root = str(pathlib.Path(args.old_root).resolve())
    new_root = str(pathlib.Path(args.new_root).resolve())
    stamp = timestamp()

    db_path = codex_home / "state_5.sqlite"
    global_path = codex_home / ".codex-global-state.json"
    session_ids: list[str] = []
    rollout_paths: list[pathlib.Path] = []

    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        rows = list(con.execute("select id,cwd,rollout_path from threads"))
        for row in rows:
            cwd = row["cwd"] or ""
            if same_project_root(cwd, old_root):
                session_ids.append(row["id"])
                rollout = (row["rollout_path"] or "").replace("\\\\?\\", "")
                if rollout:
                    rollout_paths.append(pathlib.Path(rollout))
    finally:
        con.close()

    result = {
        "codex_home": str(codex_home),
        "old_root": old_root,
        "new_root": new_root,
        "matched_threads": len(session_ids),
        "write_requested": args.write,
        "write_performed": False,
        "backups": {},
    }

    if not args.write:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    result["backups"]["sqlite"] = backup(db_path, "reparent-codex-session-recovery", stamp)
    result["backups"]["global_state"] = backup(global_path, "reparent-codex-session-recovery", stamp)

    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        for session_id in session_ids:
            con.execute("update threads set cwd=? where id=?", (new_root, session_id))
        con.commit()
    finally:
        con.close()

    changed_files = []
    for path in sorted(set(rollout_paths)):
        if path.exists() and update_jsonl_cwd(path, old_root, new_root, stamp):
            changed_files.append(str(path))

    state = json.loads(global_path.read_text(encoding="utf-8")) if global_path.exists() else {}
    for key in ("project-order", "electron-saved-workspace-roots", "active-workspace-roots", "pinned-project-ids"):
        values = state.setdefault(key, [])
        if new_root not in values:
            values.append(new_root)

    hints = state.setdefault("thread-workspace-root-hints", {})
    assignments = state.setdefault("thread-project-assignments", {})
    writable_roots = state.setdefault("thread-writable-roots", {})
    sidebar_orders = state.setdefault("sidebar-project-thread-orders", {})
    for session_id in session_ids:
        hints[session_id] = new_root
        assignments[session_id] = new_root
        writable_roots[session_id] = [new_root]
    existing = sidebar_orders.get(new_root, [])
    sidebar_orders[new_root] = list(dict.fromkeys([*session_ids, *existing]))

    projectless = state.get("projectless-thread-ids")
    if isinstance(projectless, list):
        state["projectless-thread-ids"] = [item for item in projectless if item not in session_ids]

    global_path.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    result["changed_jsonl_files"] = changed_files
    result["write_performed"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
