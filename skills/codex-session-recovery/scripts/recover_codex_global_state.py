#!/usr/bin/env python3
"""Restore Codex project/sidebar state for one workspace without reading SQLite."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil


def normalize_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value.replace("\\\\?\\", "")))


def same_project_root(value: str, project_root: str) -> bool:
    return normalize_path(value) == normalize_path(project_root)


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def read_first_json(path: pathlib.Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.loads(handle.readline())
    except Exception:
        return None


def extract_text(content: object) -> str:
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


def discover_sessions(codex_home: pathlib.Path, project_root: str) -> list[dict[str, str]]:
    sessions_dir = codex_home / "sessions"
    sessions: list[dict[str, str]] = []

    for path in sessions_dir.rglob("*.jsonl"):
        first = read_first_json(path)
        if not first or first.get("type") != "session_meta":
            continue
        payload = first.get("payload") or {}
        session_id = payload.get("id")
        cwd = payload.get("cwd") or ""
        if session_id and same_project_root(cwd, project_root):
            title = "Codex session"
            updated_at = payload.get("timestamp") or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if item.get("timestamp"):
                            updated_at = item["timestamp"]
                        item_payload = item.get("payload") or {}
                        if title == "Codex session" and item.get("type") == "response_item" and item_payload.get("role") == "user":
                            text = extract_text(item_payload.get("content"))
                            if text and not text.startswith("<environment_context>"):
                                title = " ".join(text.split())[:80]
            except Exception:
                pass
            sessions.append({"id": session_id, "title": title, "updated_at": updated_at})

    unique = {item["id"]: item for item in sessions}
    return sorted(unique.values(), key=lambda item: item["updated_at"])


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


def backup(path: pathlib.Path, label: str) -> str | None:
    if not path.exists():
        return None
    destination = path.with_name(f"{path.name}.{label}-{timestamp()}.bak")
    shutil.copy2(path, destination)
    return str(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", default=str(pathlib.Path.home() / ".codex"))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--project-display-root")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    codex_home = pathlib.Path(args.codex_home).expanduser().resolve()
    project_root = str(pathlib.Path(args.project_root).resolve())
    project_display_root = str(pathlib.Path(args.project_display_root).resolve()) if args.project_display_root else project_root
    global_path = codex_home / ".codex-global-state.json"
    index_path = codex_home / "session_index.jsonl"
    sessions = discover_sessions(codex_home, project_root)
    session_ids = [item["id"] for item in sessions]
    _, index_ids = load_session_index(index_path)

    state = json.loads(global_path.read_text(encoding="utf-8")) if global_path.exists() else {}
    hints = state.get("thread-workspace-root-hints") or {}
    assignments = state.get("thread-project-assignments") or {}
    orders = state.get("sidebar-project-thread-orders") or {}
    order = orders.get(project_root) or []

    result = {
        "codex_home": str(codex_home),
        "project_root": project_root,
        "project_display_root": project_display_root,
        "exact_project_sessions": len(session_ids),
        "missing_global_hints": [item for item in session_ids if hints.get(item) != project_root],
        "missing_project_assignments": [item for item in session_ids if assignments.get(item) != project_root],
        "missing_sidebar_order": [item for item in session_ids if item not in order],
        "missing_from_session_index": [item for item in session_ids if item not in index_ids],
        "write_requested": args.write,
        "write_performed": False,
        "backups": {},
    }

    if args.write:
        result["backups"]["global_state"] = backup(global_path, "recover-codex-global-state")
        result["backups"]["session_index"] = backup(index_path, "recover-codex-session-index")

        missing_index = set(result["missing_from_session_index"])
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

        state.setdefault("project-order", [])
        if project_root not in state["project-order"]:
            state["project-order"].append(project_root)
        if project_display_root not in state["project-order"]:
            state["project-order"].append(project_display_root)

        for key in ("electron-saved-workspace-roots", "active-workspace-roots"):
            state.setdefault(key, [])
            if project_root not in state[key]:
                state[key].append(project_root)
            if project_display_root not in state[key]:
                state[key].append(project_display_root)

        state.setdefault("pinned-project-ids", [])
        if project_root not in state["pinned-project-ids"]:
            state["pinned-project-ids"].append(project_root)
        if project_display_root not in state["pinned-project-ids"]:
            state["pinned-project-ids"].append(project_display_root)

        hints = state.setdefault("thread-workspace-root-hints", {})
        assignments = state.setdefault("thread-project-assignments", {})
        writable_roots = state.setdefault("thread-writable-roots", {})
        sidebar_orders = state.setdefault("sidebar-project-thread-orders", {})

        for session_id in session_ids:
            hints[session_id] = project_root
            assignments[session_id] = project_root
            writable_roots[session_id] = [project_root]

        existing_order = sidebar_orders.get(project_root, [])
        sidebar_orders[project_root] = list(dict.fromkeys([*session_ids, *existing_order]))
        existing_display_order = sidebar_orders.get(project_display_root, [])
        sidebar_orders[project_display_root] = list(dict.fromkeys([*session_ids, *existing_display_order]))

        projectless = state.get("projectless-thread-ids")
        if isinstance(projectless, list):
            state["projectless-thread-ids"] = [item for item in projectless if item not in session_ids]

        global_path.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        result["write_performed"] = True

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
