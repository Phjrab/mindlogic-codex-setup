import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import thread_sync


class CandidateSafetyTests(unittest.TestCase):
    def test_only_supported_user_threads_are_selected_with_archive_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.toml").write_text('model_provider = "factchat"\n')
            (home / "mindlogic-models.json").write_text(json.dumps({"models": [
                {"slug": "gpt-6-sol"}, {"slug": "gpt-5.6-sol"},
            ]}))
            db = home / "state_5.sqlite"
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, model TEXT, "
                                   "archived INTEGER, thread_source TEXT, updated_at INTEGER)")
                connection.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?)", [
                    ("user-openai", "openai", "gpt-6-sol", 0, "user", 4),
                    ("user-router", "mindlogic_menu_router", "mindlogic/gpt-5.6-sol", 0, "user", 3),
                    ("archived", "openai", "gpt-6-sol", 1, "user", 2),
                    ("agent", "openai", "gpt-6-sol", 0, "subagent", 1),
                    ("unsupported", "openai", "gpt-5.3-codex-spark", 0, "user", 0),
                ])
            with patch.multiple(thread_sync, CONFIG=home / "config.toml",
                                CATALOG=home / "mindlogic-models.json", DB=db):
                self.assertEqual(thread_sync.candidates(), [
                    ("user-openai", "gpt-6-sol", False),
                    ("user-router", "gpt-5.6-sol", False),
                    ("archived", "gpt-6-sol", True),
                ])
                (home / "config.toml").write_text('model_provider = "openai"\n')
                self.assertEqual(thread_sync.candidates(), [])

    def test_archived_threads_request_archive_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            switcher = home / "switcher.py"
            switcher.touch()
            with patch.multiple(thread_sync, LOCK=home / "lock", SWITCHER=switcher), \
                    patch.object(thread_sync, "candidates", return_value=[
                        ("active", "gpt-6-sol", False),
                        ("archived", "gpt-6-sol", True),
                    ]), patch.object(thread_sync.subprocess, "run", return_value=Mock(returncode=0)) as run:
                self.assertEqual(thread_sync.run_once(), (2, 0, 0))
            self.assertEqual(run.call_args_list[0].args[0][-2:], ["mindlogic-thread", "active"])
            self.assertEqual(run.call_args_list[1].args[0][-3:],
                             ["mindlogic-thread", "archived", "--preserve-archive"])


if __name__ == "__main__":
    unittest.main()
