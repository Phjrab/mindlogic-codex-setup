import http.client
import io
import queue
import json
import os
from pathlib import Path
import tempfile
import threading
import tomllib
import unittest
import sqlite3
from contextlib import closing
from unittest.mock import patch
from types import SimpleNamespace

import menu_install
import menu_router
import setup


class ConfigTests(unittest.TestCase):
    def test_mindlogic_astra_precedes_sol_in_new_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            models = [{"slug": slug, "supported_reasoning_levels": [{"effort": "medium"}]}
                      for slug in ("gpt-6-sol", "gpt-6-luna", "gpt-6-astra")]
            (home / "models_cache.json").write_text(json.dumps({"models": models}), encoding="utf-8")
            with patch.object(menu_install, "HOME", home), \
                    patch.object(menu_install, "read_mindlogic_key", return_value="test-key"), \
                    patch.object(setup, "account_models", return_value={m["slug"] for m in models}), \
                    patch.object(setup, "native_catalog", return_value={"models": models}):
                catalog, _routes = menu_install.catalog_and_routes()
            entries = [item for item in catalog["models"] if item["slug"].startswith("mindlogic--")]
            self.assertEqual([item["slug"] for item in entries], [
                "mindlogic--gpt-6-astra", "mindlogic--gpt-6-sol", "mindlogic--gpt-6-luna",
            ])
            self.assertEqual([item["priority"] for item in entries], [100, 101, 102])

    def test_gpt61_sol_uses_cached_metadata_and_keeps_openai_route(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            legacy = {"slug": "gpt-6-astra", "context_window": 100}
            cached = {"slug": "gpt-6.1-sol", "context_window": 200,
                      "supported_reasoning_levels": [{"effort": "high"}, {"effort": "ultra"}]}
            (home / "models_cache.json").write_text(json.dumps({"models": [cached, legacy]}), encoding="utf-8")
            with patch.object(menu_install, "HOME", home), \
                    patch.object(menu_install, "read_mindlogic_key", return_value="test-key"), \
                    patch.object(setup, "account_models", return_value={"gpt-6.1-sol", "gpt-6-astra"}), \
                    patch.object(setup, "native_catalog", return_value={"models": [legacy]}):
                catalog, manifest = menu_install.catalog_and_routes()
            entries = [m for m in catalog["models"] if m["slug"].startswith("mindlogic--")]
            self.assertEqual(entries[0]["slug"], "mindlogic--gpt-6.1-sol")
            self.assertEqual(entries[0]["display_name"], "Mindlogic · GPT-6.1 Sol")
            self.assertEqual(entries[0]["context_window"], 200)
            self.assertEqual(entries[0]["supported_reasoning_levels"], [{"effort": "high"}])
            self.assertEqual(manifest["routes"]["gpt-6.1-sol"]["provider"], "openai")
            for alias in ("mindlogic--gpt-6.1-sol", "mindlogic/gpt-6.1-sol"):
                self.assertEqual(manifest["routes"][alias], {"provider": "mindlogic", "model": "gpt-6.1-sol"})

    def test_direct_catalog_uses_matching_cached_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            cached = {"slug": "gpt-6.1-sol", "context_window": 200}
            (home / "models_cache.json").write_text(json.dumps({"models": [cached]}), encoding="utf-8")
            with patch.object(setup, "CODEX_HOME", home), \
                    patch.object(setup, "native_catalog", return_value={"models": [{"slug": "gpt-6-astra", "context_window": 100}]}):
                catalog = setup.build_catalog({"gpt-6.1-sol"})
            self.assertEqual(catalog["models"][0]["slug"], "gpt-6.1-sol")
            self.assertEqual(catalog["models"][0]["context_window"], 200)

    def test_migrates_archived_user_chat_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            thread_id = "01a0dce7-de52-7633-8040-e235c98474b5"
            (home / "config.toml").write_text('model_provider = "mindlogic_menu_router"\n', encoding="utf-8")
            (home / "mindlogic-menu-state.json").write_text('{}', encoding="utf-8")
            (home / "mindlogic-menu-routes.json").write_text(json.dumps({"routes": {
                "mindlogic--gpt-6-astra": {"provider": "mindlogic", "model": "gpt-6-astra"},
            }}), encoding="utf-8")
            with closing(sqlite3.connect(home / "state_5.sqlite")) as database, database:
                database.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, model TEXT, "
                                 "archived INTEGER, thread_source TEXT)")
                database.execute("INSERT INTO threads VALUES (?,?,?,?,?)",
                                 (thread_id, "factchat", "gpt-6-astra", 1, "user"))
            state = {"id": thread_id, "status": {"type": "notLoaded"},
                     "modelProvider": "factchat", "model": "gpt-6-astra", "cwd": str(home),
                     "projectId": "project-1", "source": {"kind": "user"},
                     "turns": [{"id": "one"}]}
            calls = []
            class FakeServer:
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def call(self, method, params):
                    calls.append(method)
                    if method == "thread/read": return {"thread": dict(state)}
                    if method == "thread/turns/list":
                        return {"data": [{"id": "one"}], "nextCursor": None}
                    if method == "thread/resume":
                        self.testcase.assertTrue(params["excludeTurns"])
                        state.update(modelProvider=params["modelProvider"], model=params["model"])
                        return {"thread": dict(state), "modelProvider": state["modelProvider"],
                                "model": state["model"], "cwd": state["cwd"]}
                    if method in ("thread/unarchive", "thread/archive"):
                        return {}
                    raise AssertionError(method)
            FakeServer.testcase = self
            with patch.object(setup, "CODEX_HOME", home), \
                    patch.object(setup, "CONFIG", home / "config.toml"), \
                    patch.object(setup, "AppServer", FakeServer):
                setup.switch_thread_to_menu(thread_id)
            self.assertEqual(state["modelProvider"], "mindlogic_menu_router")
            self.assertEqual(state["model"], "mindlogic--gpt-6-astra")
            self.assertEqual(state["turns"], [{"id": "one"}])
            self.assertEqual(calls, ["thread/read", "thread/turns/list", "thread/unarchive",
                                     "thread/resume", "thread/archive", "thread/read",
                                     "thread/turns/list"])

    def test_refresh_inactive_stale_catalog_preserves_selected_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {name: root / name for name in ("CONFIG", "STATE", "CATALOG", "MANIFEST", "ROUTER")}
            original = 'model = "gpt-6-sol"\nmodel_provider = "openai"\n'
            paths["CONFIG"].write_text(original, encoding="utf-8")
            paths["STATE"].write_text('{}', encoding="utf-8")
            paths["CATALOG"].write_text('{"models":[]}', encoding="utf-8")
            paths["MANIFEST"].write_text('{"local_token":"test-token","routes":{}}', encoding="utf-8")
            paths["ROUTER"].write_text('# old router', encoding="utf-8")
            catalog = {"models": [{"slug": "mindlogic--gpt-6-sol"}]}
            manifest = {"routes": {"mindlogic--gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}
            with patch.multiple(menu_install, **paths), \
                    patch.object(menu_install, "catalog_and_routes", return_value=(catalog, manifest)), \
                    patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health"):
                menu_install.refresh()
            self.assertEqual(paths["CONFIG"].read_text(encoding="utf-8-sig"), original)
            self.assertEqual(json.loads(paths["CATALOG"].read_text(encoding="utf-8-sig")), catalog)
            self.assertEqual(json.loads(paths["MANIFEST"].read_text(encoding="utf-8-sig"))["local_token"], "test-token")

    def test_app_server_retains_coalesced_replies_after_notification(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'{"method":"notice"}\n{"id":1,"result":{"ok":1}}\n{"id":2,"result":{"ok":2}}\n')
            os.close(write_fd)
            write_fd = None
            with os.fdopen(read_fd, "rb", buffering=0) as output:
                server = setup.AppServer()
                server.process = SimpleNamespace(stdin=io.BytesIO(), stdout=output)
                server.request_id = 0
                server.replies = queue.Queue()
                reader = threading.Thread(target=server._read_replies)
                reader.start()
                self.assertEqual(server.call("first", {}), {"ok": 1})
                self.assertEqual(server.call("second", {}), {"ok": 2})
                reader.join(timeout=2)
                self.assertFalse(reader.is_alive())
        finally:
            if write_fd is not None:
                os.close(write_fd)

    def test_current_app_cli_precedes_path_cli(self):
        bundled = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
        with patch.object(setup.sys, "platform", "darwin"), \
                patch.object(setup.shutil, "which", return_value="/usr/local/bin/codex"), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(setup.os, "access", return_value=True):
            self.assertEqual(setup.codex_executable(), str(Path(bundled)))

    def test_menu_activation_restores_missing_provider_and_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.toml"
            section = ('[model_providers.mindlogic_menu_router]\n'
                       'name = "Local router"\nbase_url = "http://127.0.0.1:18762"\n'
                       'wire_api = "responses"\nrequires_openai_auth = false\n')
            config.write_text('model = "gpt-6-luna"\nmodel_reasoning_effort = "high"\n\n' + section, encoding="utf-8")
            state = root / "state.json"
            state.write_text(json.dumps({"provider_section": section}), encoding="utf-8")
            manifest = root / "routes.json"
            manifest.write_text(json.dumps({"routes": {
                "gpt-6-luna": {"provider": "openai", "model": "gpt-6-luna"},
                "mindlogic--gpt-6-luna": {"provider": "mindlogic", "model": "gpt-6-luna"}}}), encoding="utf-8")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-luna"},
                                                        {"slug": "mindlogic--gpt-6-luna"}]}), encoding="utf-8")
            with patch.multiple(menu_install, CONFIG=config, STATE=state, MANIFEST=manifest,
                                CATALOG=catalog), patch.object(menu_install, "backup"), \
                    patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health"):
                menu_install.activate()
            parsed = tomllib.loads(config.read_text(encoding="utf-8-sig"))
            self.assertEqual(parsed["model_provider"], "mindlogic_menu_router")
            self.assertEqual(parsed["model"], "mindlogic--gpt-6-luna")
            self.assertEqual(parsed["model_catalog_json"], str(catalog))
            self.assertEqual(parsed["model_reasoning_effort"], "high")

    def test_mindlogic_alias_maps_plain_and_legacy_model_ids(self):
        routes = {"mindlogic--gpt-6-luna": {"provider": "mindlogic", "model": "gpt-6-luna"}}
        self.assertEqual(menu_install.mindlogic_alias_for_model("gpt-6-luna", routes),
                         "mindlogic--gpt-6-luna")
        self.assertEqual(menu_install.mindlogic_alias_for_model("mindlogic/gpt-6-luna", routes),
                         "mindlogic--gpt-6-luna")

    def test_preserves_quoted_tables_keys_and_multiline_heading(self):
        original = '''"model" = "gpt-6-astra" # keep original comment
model_provider = 'openai'
note = """intro
[Example]
outro"""
[model_providers."factchat"] # comment
base_url = 'https://example.test'
[unrelated]
enabled = true
'''
        changed = menu_install.edit_config(original, {
            "model": "mindlogic--gpt-6-luna", "model_provider": "mindlogic_menu_router",
            "model_catalog_json": "/tmp/catalog.json"},
            provider_section='[model_providers.mindlogic_menu_router]\nbase_url = "http://127.0.0.1:18762"\n')
        parsed = tomllib.loads(changed)
        self.assertEqual(parsed["model"], "mindlogic--gpt-6-luna")
        self.assertIn('[Example]', changed)
        self.assertIn('[model_providers."factchat"] # comment', changed)
        self.assertTrue(parsed["unrelated"]["enabled"])
        self.assertEqual(menu_install.top_level_lines(original)["model"], '"model" = "gpt-6-astra" # keep original comment\n')

    def test_single_quote_and_comment_heading(self):
        original = "model = 'gpt-6-luna'\n[model_providers.factchat] # comment\nbase_url = 'https://example.test'\n"
        changed = menu_install.edit_config(original, {"model": "mindlogic--gpt-6-sol"})
        self.assertEqual(tomllib.loads(changed)["model"], "mindlogic--gpt-6-sol")
        self.assertIn('[model_providers.factchat] # comment', changed)

    def test_backup_is_unique(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "config.toml"
            file.write_text('model = "one"\n', encoding="utf-8")
            first = menu_install.backup(file)
            second = menu_install.backup(file)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(encoding="utf-8-sig"), second.read_text(encoding="utf-8-sig"))

    def test_install_repeated_install_refused_and_remove_after_model_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.toml"
            original = "model = 'gpt-6-sol'\nmodel_provider = 'factchat'\n[other]\nvalue = 7\n"
            config.write_text(original, encoding="utf-8")
            paths = {"HOME": root, "PORT": 0, "CONFIG": config, "CATALOG": root / "catalog.json",
                     "MANIFEST": root / "routes.json", "STATE": root / "state.json",
                     "ROUTER": root / "bin" / "router.py", "AGENT": root / "agent.plist"}
            catalog = {"models": [{"slug": "gpt-6-sol"}, {"slug": "mindlogic--gpt-6-sol"}]}
            routes = {"routes": {"gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
                     "mindlogic--gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}
            with patch.multiple(menu_install, **paths), patch.object(menu_install, "catalog_and_routes", return_value=(catalog, routes)), patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health"):
                menu_install.install()
                installed = tomllib.loads(config.read_text(encoding="utf-8-sig"))
                self.assertEqual(installed["model"], "mindlogic--gpt-6-sol")
                self.assertTrue(installed["model_providers"]["mindlogic_menu_router"]["requires_openai_auth"])
                self.assertIn("X-Mindlogic-Router-Token",
                              installed["model_providers"]["mindlogic_menu_router"]["http_headers"])
                config.write_text(config.read_text(encoding="utf-8-sig").replace(
                    'model_provider = "mindlogic_menu_router"', 'model_provider = "factchat"', 1), encoding="utf-8")
                menu_install.isolate_local_auth()
                isolated = tomllib.loads(config.read_text(encoding="utf-8-sig"))
                self.assertEqual(isolated["model_provider"], "factchat")
                self.assertFalse(isolated["model_providers"]["mindlogic_menu_router"]["requires_openai_auth"])
                menu_install.isolate_local_auth()
                before_auth = config.read_bytes()
                with patch.object(menu_install, "await_router_health", side_effect=RuntimeError("unhealthy")):
                    with self.assertRaises(RuntimeError):
                        menu_install.enable_chatgpt_auth()
                self.assertEqual(config.read_bytes(), before_auth)
                menu_install.enable_chatgpt_auth()
                enabled = tomllib.loads(config.read_text(encoding="utf-8-sig"))
                self.assertTrue(enabled["model_providers"]["mindlogic_menu_router"]["requires_openai_auth"])
                self.assertEqual(enabled["model_provider"], "factchat")
                menu_install.enable_chatgpt_auth()
                menu_install.isolate_local_auth()
                config.write_text(config.read_text(encoding="utf-8-sig").replace('model_provider = "factchat"',
                                                             'model_provider = "mindlogic_menu_router"', 1), encoding="utf-8")
                with self.assertRaises(ValueError):
                    menu_install.install()
                config.write_text(config.read_text(encoding="utf-8-sig").replace('model = "mindlogic--gpt-6-sol"', 'model = "gpt-6-sol"'), encoding="utf-8")
                menu_install.remove()
                self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8-sig"))["other"]["value"], 7)
                self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8-sig"))["model_provider"], "factchat")
                self.assertFalse((root / "state.json").exists())

    def test_failed_service_start_restores_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.toml"
            original = "model = 'gpt-6-sol'\nmodel_provider = 'openai'\n[other]\nvalue = 7\n"
            config.write_text(original, encoding="utf-8")
            paths = {"HOME": root, "PORT": 0, "CONFIG": config, "CATALOG": root / "catalog.json",
                     "MANIFEST": root / "routes.json", "STATE": root / "state.json",
                     "ROUTER": root / "bin" / "router.py", "AGENT": root / "agent.plist"}
            catalog = {"models": [{"slug": "gpt-6-sol"}, {"slug": "mindlogic--gpt-6-sol"}]}
            routes = {"routes": {"gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
                     "mindlogic--gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}
            with patch.multiple(menu_install, **paths), patch.object(menu_install, "catalog_and_routes", return_value=(catalog, routes)), patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health", side_effect=RuntimeError("not ready")):
                with self.assertRaises(RuntimeError):
                    menu_install.install()
                self.assertEqual(config.read_text(encoding="utf-8-sig"), original)
                self.assertFalse((root / "state.json").exists())
                self.assertFalse((root / "agent.plist").exists())


class FakeResponse:
    status = 200
    def __init__(self):
        self.chunks = iter([b'event: response.created\ndata: {"response":{"id":"resp_test"}}\n\n',
                            b'event: response.completed\ndata: {"response":{"id":"resp_test","output":[]}}\n\n'])
    def getheader(self, key, default=None):
        return "text/event-stream" if key.lower() == "content-type" else default
    def read1(self, _size):
        return next(self.chunks, b"")


class FakeConnection:
    calls = []
    def __init__(self, host, timeout, context):
        self.host = host
    def request(self, method, path, body, headers):
        self.calls.append((self.host, path, json.loads(body), headers))
    def getresponse(self):
        return FakeResponse()
    def close(self):
        pass


class RouterTests(unittest.TestCase):
    def setUp(self):
        FakeConnection.calls = []
        self.temp = tempfile.TemporaryDirectory()
        self.env = Path(self.temp.name) / ".env"
        self.env.write_text('FACTCHAT_API_KEY=secret-mindlogic\n', encoding="utf-8")
        self.server = menu_router.Router(("127.0.0.1", 0), manifest={"local_token":"local-test", "routes": {
            "gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
            "mindlogic--gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}, env_file=self.env)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patch = patch.object(menu_router, "HTTPSConnection", FakeConnection)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, model, extra=None, *, openai_auth=False, local_token="local-test"):
        body = {"model": model, "stream": True, "input": [
            {"type": "reasoning", "encrypted_content": "opaque"},
            {"type": "function_call", "call_id": "call_1", "name": "echo", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"}]}
        body.update(extra or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        headers = {menu_router.LOCAL_TOKEN_HEADER: local_token, "thread-id": "thread-test",
                   "chatgpt-account-id": "account", "Content-Type": "application/json"}
        if openai_auth:
            headers["Authorization"] = "Bearer secret-openai"
        conn.request("POST", "/responses", json.dumps(body), headers)
        result = conn.getresponse()
        status, data = result.status, result.read()
        conn.close()
        return status, data

    def test_routes_and_separates_credentials_and_tools(self):
        for model in ("gpt-6-sol", "mindlogic--gpt-6-sol", "gpt-6-sol"):
            status, data = self.request(model, openai_auth=model == "gpt-6-sol")
            self.assertEqual(status, 200)
            self.assertIn(b"response.completed", data)
        self.assertEqual([call[0] for call in FakeConnection.calls],
                         ["chatgpt.com", "factchat-cloud.mindlogic.ai", "chatgpt.com"])
        openai = FakeConnection.calls[0]
        mindlogic = FakeConnection.calls[1]
        self.assertEqual(openai[3]["Authorization"], "Bearer secret-openai")
        self.assertEqual(mindlogic[3]["Authorization"], "Bearer secret-mindlogic")
        self.assertNotIn("chatgpt-account-id", mindlogic[3])
        self.assertEqual(mindlogic[2]["model"], "gpt-6-sol")
        self.assertEqual([item["type"] for item in mindlogic[2]["input"]],
                         ["function_call", "function_call_output"])

    def test_unknown_alias_and_server_reference_are_rejected(self):
        self.assertEqual(self.request("unknown")[0], 400)
        self.assertEqual(self.request("mindlogic--gpt-6-sol", {"previous_response_id": "resp_other"})[0], 409)
        self.assertEqual(FakeConnection.calls, [])

    def test_reasoning_is_kept_only_on_stable_route(self):
        self.request("gpt-6-sol", openai_auth=True)
        self.request("gpt-6-sol", openai_auth=True)
        self.request("mindlogic--gpt-6-sol")
        self.assertEqual(len(FakeConnection.calls[0][2]["input"]), 2)
        self.assertEqual(len(FakeConnection.calls[1][2]["input"]), 3)
        self.assertEqual(len(FakeConnection.calls[2][2]["input"]), 2)

    def test_local_auth_is_required(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("POST", "/responses", '{"model":"mindlogic--gpt-6-sol","input":[]}',
                     {"Authorization": "Bearer fake", "Content-Type": "application/json"})
        result = conn.getresponse()
        self.assertEqual(result.status, 401)
        result.read()
        conn.close()
        self.assertEqual(FakeConnection.calls, [])

    def test_mindlogic_needs_no_openai_auth_and_openai_fails_closed(self):
        status, data = self.request("mindlogic--gpt-6-sol")
        self.assertEqual(status, 200)
        self.assertIn(b"response.completed", data)
        self.assertEqual(FakeConnection.calls[0][0], menu_router.MINDLOGIC_HOST)
        self.assertEqual(FakeConnection.calls[0][3]["Authorization"], "Bearer secret-mindlogic")
        self.assertEqual(self.request("gpt-6-sol")[0], 503)
        self.assertEqual(len(FakeConnection.calls), 1)

    def test_wrong_local_token_blocks_both_routes(self):
        for model in ("gpt-6-sol", "mindlogic--gpt-6-sol"):
            self.assertEqual(self.request(model, openai_auth=True, local_token="wrong")[0], 401)
        self.assertEqual(FakeConnection.calls, [])

    def test_mindlogic_upstream_errors_never_fall_back_to_openai(self):
        for upstream_status in (401, 429):
            with patch.object(FakeResponse, "status", upstream_status):
                self.assertEqual(self.request("mindlogic--gpt-6-sol")[0], upstream_status)
        with patch.object(FakeConnection, "getresponse", side_effect=OSError("offline")):
            self.assertEqual(self.request("mindlogic--gpt-6-sol")[0], 502)
        self.assertEqual([call[0] for call in FakeConnection.calls],
                         [menu_router.MINDLOGIC_HOST] * 3)


if __name__ == "__main__":
    unittest.main()
