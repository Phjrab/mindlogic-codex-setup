import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import menu_install
import menu_router
import platform_support
import setup


class PreserveDefaultTests(unittest.TestCase):
    def test_direct_install_preserves_openai_and_writes_windows_launchers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            original = '# 사용자 설정\r\nmodel = "gpt-6-sol"\r\nmodel_reasoning_effort = "high"\r\n'.encode("utf-8")
            config.write_bytes(original)
            catalog = {"models": [{"slug": "gpt-6-sol"}]}
            with patch.multiple(setup, CODEX_HOME=root, CONFIG=config, CATALOG=root / "catalog.json",
                                PROFILE=root / "profile-state.json", ENV_FILE=root / ".env"), \
                    patch.object(sys, "platform", "win32"), patch.object(setup, "read_env_key", return_value="test-key"), \
                    patch.object(setup, "account_models", return_value={"gpt-6-sol"}), \
                    patch.object(setup, "build_catalog", return_value=catalog):
                setup.install(preserve_default=True)
            self.assertEqual(config.read_bytes(), original)
            profile = tomllib.loads((root / "mindlogic.config.toml").read_text(encoding="utf-8"))
            self.assertEqual(profile["model_provider"], "factchat")
            self.assertNotIn("test-key", (root / "mindlogic.config.toml").read_text(encoding="utf-8"))
            self.assertTrue((root / "bin" / "codex-profile.cmd").exists())
            self.assertTrue((root / "bin" / "mindlogic.cmd").exists())

    def test_separate_profile_install_refresh_auth_remove_preserves_openai_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            original = '# 사용자 설정\r\nmodel = "gpt-6-sol"\r\nmodel_reasoning_effort = "high"\r\n'.encode("utf-8")
            config.write_bytes(original)
            paths = {"HOME": root, "PORT": 0, "CONFIG": config, "CATALOG": root / "catalog.json",
                     "MANIFEST": root / "routes.json", "STATE": root / "state.json",
                     "ROUTER": root / "bin" / "router.py", "AGENT": root / "service.json"}
            catalog = {"models": [{"slug": "gpt-6-sol"}, {"slug": "mindlogic--gpt-6-sol"}]}
            routes = {"routes": {"gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
                      "mindlogic--gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}
            with patch.multiple(menu_install, **paths), \
                    patch.object(menu_install, "catalog_and_routes", return_value=(catalog, routes)), \
                    patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health"):
                menu_install.install(preserve_default=True)
                profile = root / "mindlogic-menu.config.toml"
                self.assertEqual(config.read_bytes(), original)
                self.assertEqual(tomllib.loads(profile.read_text(encoding="utf-8"))["model_provider"], menu_install.PROVIDER)
                menu_install.isolate_local_auth()
                self.assertEqual(config.read_bytes(), original)
                self.assertFalse(tomllib.loads(profile.read_text(encoding="utf-8"))["model_providers"][menu_install.PROVIDER]["requires_openai_auth"])
                menu_install.enable_chatgpt_auth()
                menu_install.refresh()
                self.assertEqual(config.read_bytes(), original)
                with patch.object(menu_install, "await_router_health", side_effect=RuntimeError("not ready")):
                    with self.assertRaises(RuntimeError):
                        menu_install.activate()
                self.assertEqual(config.read_bytes(), original)
                menu_install.remove()
                self.assertEqual(config.read_bytes(), original)
                self.assertFalse(profile.exists())

    def test_windows_main_defaults_to_preserved_profile(self):
        with patch.object(sys, "argv", ["setup.py", "menu-install"]), \
                patch.object(sys, "platform", "win32"), patch.object(menu_install, "install") as install:
            setup.main()
            install.assert_called_once_with(preserve_default=True)
        with patch.object(sys, "argv", ["setup.py", "menu-install", "--activate"]), \
                patch.object(sys, "platform", "win32"), patch.object(menu_install, "install") as install:
            setup.main()
            install.assert_called_once_with(preserve_default=False)

    def test_explicit_activation_keeps_the_selected_openai_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('model = "gpt-6-sol"\n', encoding="utf-8")
            section = '[model_providers.mindlogic_menu_router]\nbase_url = "http://127.0.0.1:18762"\n'
            state = root / "state.json"
            state.write_text(json.dumps({"preserve_default": True, "provider_section": section}), encoding="utf-8")
            manifest = root / "routes.json"
            manifest.write_text(json.dumps({"routes": {"gpt-6-sol": {"provider": "openai"},
                                                        "mindlogic--gpt-6-sol": {"provider": "mindlogic"}}}), encoding="utf-8")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-sol"}, {"slug": "mindlogic--gpt-6-sol"}]}), encoding="utf-8")
            with patch.multiple(menu_install, CONFIG=config, STATE=state, MANIFEST=manifest, CATALOG=catalog), \
                    patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health"):
                menu_install.activate()
            self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8"))["model"], "gpt-6-sol")

    def test_busy_port_install_does_not_write_files(self):
        import socket
        with tempfile.TemporaryDirectory() as directory, socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            root = Path(directory)
            with patch.object(menu_install, "PORT", busy.getsockname()[1]), \
                    patch.object(menu_install, "STATE", root / "state.json"):
                with self.assertRaisesRegex(ValueError, "in use"):
                    menu_install.install(preserve_default=True)
                self.assertEqual(list(root.iterdir()), [])


class PortableRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows socket restart")
    def test_windows_router_rebinds_after_a_closed_http_response(self):
        import http.client
        import threading
        server = menu_router.Router(("127.0.0.1", 0), manifest={"local_token": "test-token", "routes": {}}, env_file=Path("unused"))
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(OSError):
                menu_router.Router(("127.0.0.1", port), manifest={}, env_file=Path("unused"))
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("POST", "/responses", body="{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        restarted = menu_router.Router(("127.0.0.1", port), manifest={}, env_file=Path("unused"))
        restarted.server_close()

    def test_utf8_private_write_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "설정.toml"
            setup.write_private(file, '# 한글\nmodel = "gpt-6-sol"\n')
            self.assertIn("한글", file.read_text(encoding="utf-8"))

    def test_windows_cli_uses_resolved_path(self):
        with patch.object(sys, "platform", "win32"), patch.object(setup.shutil, "which", return_value="C:\\Codex\\codex.exe"):
            self.assertEqual(setup.codex_executable(), "C:\\Codex\\codex.exe")

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific lock")
    def test_windows_lock_excludes_another_process_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "lock"
            code = "import pathlib,platform_support,sys; f=pathlib.Path(sys.argv[1]).open('a+'); print(platform_support.try_lock(f)); f.close()"
            with file.open("a+") as stream:
                self.assertTrue(platform_support.try_lock(stream))
                locked = subprocess.check_output([sys.executable, "-c", code, str(file)], text=True).strip()
                self.assertEqual(locked, "False")
            released = subprocess.check_output([sys.executable, "-c", code, str(file)], text=True).strip()
            self.assertEqual(released, "True")

    def test_windows_service_is_per_home_and_mac_definition_is_preserved(self):
        with patch.object(sys, "platform", "win32"):
            home = Path("한글 경로")
            spec = json.loads(platform_support.service_definition(home, "router", home / "router.py", ["--port", "18762"], log_prefix="router"))
            self.assertEqual(spec["arguments"], ["--port", "18762"])
            self.assertNotEqual(platform_support.task_name("router", home / "a.json"),
                                platform_support.task_name("router", home / "b.json"))
        import plistlib
        with patch.object(sys, "platform", "darwin"):
            spec = plistlib.loads(platform_support.service_definition(home, "sync", home / "sync.py", [], interval=60, log_prefix="sync"))
            self.assertEqual(spec["StartInterval"], 60)
            self.assertEqual(spec["EnvironmentVariables"]["CODEX_HOME"], str(home))


if __name__ == "__main__":
    unittest.main()
