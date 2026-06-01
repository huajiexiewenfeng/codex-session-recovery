import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "codex-session-recovery"
    / "scripts"
    / "surface_codex_sidebar_threads.py"
)

spec = importlib.util.spec_from_file_location("surface_codex_sidebar_threads", SCRIPT)
surface = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(surface)


class SidebarSurfaceSelectionTests(unittest.TestCase):
    def test_selects_round_robin_by_exact_project_root(self):
        rows = [
            {"id": "a1", "cwd": r"C:\repo\a", "source": "vscode", "archived": 0, "updated_at": 30, "created_at": 1},
            {"id": "a2", "cwd": r"C:\repo\a", "source": "vscode", "archived": 0, "updated_at": 20, "created_at": 1},
            {"id": "b1", "cwd": r"C:\repo\b", "source": "vscode", "archived": 0, "updated_at": 10, "created_at": 1},
            {"id": "child", "cwd": r"C:\repo\a\child", "source": "vscode", "archived": 0, "updated_at": 99, "created_at": 1},
        ]

        selected = surface.select_threads_for_sidebar(
            rows,
            [r"C:\repo\a", r"C:\repo\b"],
            per_project=2,
            max_total=4,
        )

        self.assertEqual([item["id"] for item in selected], ["a1", "b1", "child", "a2"])
        self.assertEqual(selected[0]["_project_root"], r"C:\repo\a")
        self.assertEqual(selected[1]["_project_root"], r"C:\repo\b")

    def test_filters_archived_and_subagent_sources(self):
        rows = [
            {"id": "ok", "cwd": r"C:\repo\a", "source": "vscode", "archived": 0, "updated_at": 3, "created_at": 1},
            {"id": "archived", "cwd": r"C:\repo\a", "source": "vscode", "archived": 1, "updated_at": 4, "created_at": 1},
            {"id": "subagent", "cwd": r"C:\repo\a", "source": '{"subagent":{"other":"guardian"}}', "archived": 0, "updated_at": 5, "created_at": 1},
        ]

        selected = surface.select_threads_for_sidebar(
            rows,
            [r"C:\repo\a"],
            per_project=3,
            max_total=10,
        )

        self.assertEqual([item["id"] for item in selected], ["ok"])

    def test_zero_limits_select_all_round_robin(self):
        rows = [
            {"id": "a1", "cwd": r"C:\repo\a", "source": "vscode", "archived": 0, "updated_at": 30, "created_at": 1},
            {"id": "a2", "cwd": r"C:\repo\a", "source": "vscode", "archived": 0, "updated_at": 20, "created_at": 1},
            {"id": "b1", "cwd": r"C:\repo\b", "source": "vscode", "archived": 0, "updated_at": 10, "created_at": 1},
        ]

        selected = surface.select_threads_for_sidebar(
            rows,
            [r"C:\repo\a", r"C:\repo\b"],
            per_project=0,
            max_total=0,
        )

        self.assertEqual([item["id"] for item in selected], ["a1", "b1", "a2"])

    def test_touch_rollout_updates_last_task_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            rows = [
                {"timestamp": "2026-01-01T00:00:00.000Z", "type": "session_meta", "payload": {"id": "t1"}},
                {"timestamp": "2026-01-01T00:01:00.000Z", "type": "event_msg", "payload": {"type": "task_complete", "completed_at": 100}},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = surface.touch_rollout_completion(path, 200, 200000)

            updated = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(result["old_completed_at"], 100)
            self.assertEqual(updated[-1]["payload"]["completed_at"], 200)
            self.assertEqual(updated[-1]["timestamp"], "1970-01-01T00:03:20.000Z")


if __name__ == "__main__":
    unittest.main()
