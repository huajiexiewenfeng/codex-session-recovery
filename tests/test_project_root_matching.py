import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "codex-session-recovery"
    / "scripts"
    / "restore_codex_project_sessions.py"
)
RECOVER_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "codex-session-recovery"
    / "scripts"
    / "recover_codex_global_state.py"
)
REPARENT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "codex-session-recovery"
    / "scripts"
    / "reparent_codex_sessions.py"
)


spec = importlib.util.spec_from_file_location("restore_codex_project_sessions", SCRIPT)
restore = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(restore)

recover_spec = importlib.util.spec_from_file_location("recover_codex_global_state", RECOVER_SCRIPT)
recover = importlib.util.module_from_spec(recover_spec)
assert recover_spec.loader is not None
recover_spec.loader.exec_module(recover)

reparent_spec = importlib.util.spec_from_file_location("reparent_codex_sessions", REPARENT_SCRIPT)
reparent = importlib.util.module_from_spec(reparent_spec)
assert reparent_spec.loader is not None
reparent_spec.loader.exec_module(reparent)


def write_session(path: Path, session_id: str, cwd: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": cwd,
                    "timestamp": "2026-06-01T00:00:00Z",
                },
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


class ProjectRootMatchingTests(unittest.TestCase):
    def test_same_project_root_rejects_children_and_sibling_prefixes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project = str(Path(tempdir) / "project")
            child = str(Path(project) / "child")
            sibling = project + "-old"
            extended = "\\\\?\\" + project

            for module in (restore, recover, reparent):
                self.assertTrue(module.same_project_root(project, project))
                self.assertTrue(module.same_project_root(extended, project))
                self.assertFalse(module.same_project_root(child, project))
                self.assertFalse(module.same_project_root(sibling, project))

    def test_discovers_only_exact_project_root_sessions(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tmp_path = Path(tempdir)
            codex_home = tmp_path / ".codex"
            sessions = codex_home / "sessions" / "2026" / "06" / "01"
            project = str(tmp_path / "project")

            write_session(sessions / "exact.jsonl", "exact", project)
            write_session(sessions / "extended.jsonl", "extended", "\\\\?\\" + project)
            write_session(sessions / "child.jsonl", "child", str(Path(project) / "child"))
            write_session(sessions / "sibling-prefix.jsonl", "sibling", project + "-old")

            found = restore.discover_project_sessions(codex_home, project)

            self.assertEqual([item["id"] for item in found], ["exact", "extended"])

    def test_reads_only_exact_project_root_threads_from_sqlite(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tmp_path = Path(tempdir)
            codex_home = tmp_path / ".codex"
            codex_home.mkdir()
            db = codex_home / "state_5.sqlite"
            project = str(tmp_path / "project")

            con = sqlite3.connect(db)
            con.execute(
                "create table threads (id text, cwd text, title text, archived integer, rollout_path text, updated_at integer, updated_at_ms integer)"
            )
            rows = [
                ("exact", project),
                ("extended", "\\\\?\\" + project),
                ("child", str(Path(project) / "child")),
                ("sibling", project + "-old"),
            ]
            for i, (session_id, cwd) in enumerate(rows):
                con.execute(
                    "insert into threads values (?,?,?,?,?,?,?)",
                    (session_id, cwd, session_id, 0, f"{session_id}.jsonl", i, i),
                )
            con.commit()
            con.close()

            found = restore.read_threads_from_sqlite(codex_home, project)

            self.assertEqual([item["id"] for item in found], ["exact", "extended"])


if __name__ == "__main__":
    unittest.main()
