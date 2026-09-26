import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import thread_sync


class CandidateSafetyTests(unittest.TestCase):
    def test_router_mode_selects_only_supported_user_chats(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.toml").write_text('model_provider = "mindlogic_menu_router"\n')
            (home / "mindlogic-menu-routes.json").write_text(json.dumps({"routes": {
                "gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
                "mindlogic--gpt-6-astra": {"provider": "mindlogic", "model": "gpt-6-astra"},
            }}))
            db = home / "state_5.sqlite"
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, model TEXT, "
                                   "archived INTEGER, thread_source TEXT, updated_at INTEGER)")
                connection.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?)", [
                    ("direct", "factchat", "gpt-6-astra", 1, "user", 4),
                    ("openai", "openai", "gpt-6-sol", 0, "user", 3),
                    ("review", "openai", "gpt-6-sol", 0, "guardian_review", 2),
                    ("unknown", "factchat", "gpt-5.5", 0, "user", 1),
                ])
            with patch.multiple(thread_sync, CONFIG=home / "config.toml",
                                MENU_MANIFEST=home / "mindlogic-menu-routes.json", DB=db):
                self.assertEqual(thread_sync.candidates(), [
                    ("direct", "mindlogic--gpt-6-astra", True),
                    ("openai", "gpt-6-sol", False),
                ])

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
                    ("user-router-new", "mindlogic_menu_router", "mindlogic--gpt-6-sol", 0, "user", 2),
                    ("archived", "openai", "gpt-6-sol", 1, "user", 1),
                    ("agent", "openai", "gpt-6-sol", 0, "subagent", 1),
                    ("unsupported", "openai", "gpt-5.3-codex-spark", 0, "user", 0),
                ])
            with patch.multiple(thread_sync, CONFIG=home / "config.toml",
                                CATALOG=home / "mindlogic-models.json", DB=db):
                self.assertEqual(thread_sync.candidates(), [
                    ("user-openai", "gpt-6-sol", False),
                    ("user-router", "gpt-5.6-sol", False),
                    ("user-router-new", "gpt-6-sol", False),
                    ("archived", "gpt-6-sol", True),
                ])
                (home / "config.toml").write_text('model_provider = "openai"\n')
                self.assertEqual(thread_sync.candidates(), [])

    def test_archived_threads_request_archive_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            switcher = home / "switcher.py"
            switcher.touch()
            (home / "config.toml").write_text('model_provider = "factchat"\n')
            with patch.multiple(thread_sync, LOCK=home / "lock", SWITCHER=switcher,
                                CONFIG=home / "config.toml"), \
                    patch.object(thread_sync, "candidates", return_value=[
                        ("active", "gpt-6-sol", False),
                        ("archived", "gpt-6-sol", True),
                    ]), patch.object(thread_sync.subprocess, "run", return_value=Mock(returncode=0)) as run:
                self.assertEqual(thread_sync.run_once(), (2, 0, 0))
            self.assertEqual(run.call_args_list[0].args[0][-2:], ["mindlogic-thread", "active"])
            self.assertEqual(run.call_args_list[1].args[0][-3:],
                             ["mindlogic-thread", "archived", "--preserve-archive"])

    def test_router_mode_retries_without_requiring_archive_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            switcher = home / "switcher.py"
            switcher.touch()
            config = home / "config.toml"
            config.write_text('model_provider = "mindlogic_menu_router"\n')
            with patch.multiple(thread_sync, LOCK=home / "lock", SWITCHER=switcher,
                                CONFIG=config), \
                    patch.object(thread_sync, "candidates", return_value=[
                        ("archived", "mindlogic--gpt-6-astra", True),
                    ]), patch.object(thread_sync.subprocess, "run", return_value=Mock(returncode=0)) as run:
                self.assertEqual(thread_sync.run_once(), (1, 0, 0))
            self.assertEqual(run.call_args.args[0][-2:], ["menu-thread", "archived"])

    def test_one_time_run_uses_checkout_before_helper_install(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config.toml"
            config.write_text('model_provider = "mindlogic_menu_router"\n')
            with patch.multiple(thread_sync, LOCK=home / "lock", SWITCHER=home / "not-installed.py",
                                CONFIG=config), \
                    patch.object(thread_sync, "candidates", return_value=[
                        ("thread", "gpt-6-sol", False),
                    ]), patch.object(thread_sync.subprocess, "run", return_value=Mock(returncode=0)) as run:
                self.assertEqual(thread_sync.run_once(), (1, 0, 0))
            self.assertEqual(Path(run.call_args.args[0][1]).name, "setup.py")


if __name__ == "__main__":
    unittest.main()
