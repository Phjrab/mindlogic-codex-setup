import http.client
import json
from pathlib import Path
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch

import menu_install
import menu_router


class ConfigTests(unittest.TestCase):
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
            "model": "mindlogic/gpt-6-luna", "model_provider": "mindlogic_menu_router",
            "model_catalog_json": "/tmp/catalog.json"},
            provider_section='[model_providers.mindlogic_menu_router]\nbase_url = "http://127.0.0.1:18762"\n')
        parsed = tomllib.loads(changed)
        self.assertEqual(parsed["model"], "mindlogic/gpt-6-luna")
        self.assertIn('[Example]', changed)
        self.assertIn('[model_providers."factchat"] # comment', changed)
        self.assertTrue(parsed["unrelated"]["enabled"])
        self.assertEqual(menu_install.top_level_lines(original)["model"], '"model" = "gpt-6-astra" # keep original comment\n')

    def test_single_quote_and_comment_heading(self):
        original = "model = 'gpt-6-luna'\n[model_providers.factchat] # comment\nbase_url = 'https://example.test'\n"
        changed = menu_install.edit_config(original, {"model": "mindlogic/gpt-6-sol"})
        self.assertEqual(tomllib.loads(changed)["model"], "mindlogic/gpt-6-sol")
        self.assertIn('[model_providers.factchat] # comment', changed)

    def test_backup_is_unique(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "config.toml"
            file.write_text('model = "one"\n')
            first = menu_install.backup(file)
            second = menu_install.backup(file)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(), second.read_text())

    def test_install_repeated_install_refused_and_remove_after_model_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.toml"
            original = "model = 'gpt-6-sol'\nmodel_provider = 'factchat'\n[other]\nvalue = 7\n"
            config.write_text(original)
            paths = {"HOME": root, "CONFIG": config, "CATALOG": root / "catalog.json",
                     "MANIFEST": root / "routes.json", "STATE": root / "state.json",
                     "ROUTER": root / "bin" / "router.py", "AGENT": root / "agent.plist"}
            catalog = {"models": [{"slug": "gpt-6-sol"}, {"slug": "mindlogic/gpt-6-sol"}]}
            routes = {"routes": {"gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
                     "mindlogic/gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}
            with patch.multiple(menu_install, **paths), patch.object(menu_install, "catalog_and_routes", return_value=(catalog, routes)), patch.object(menu_install, "launch"), patch.object(menu_install, "await_router_health"):
                menu_install.install()
                self.assertEqual(tomllib.loads(config.read_text())["model"], "mindlogic/gpt-6-sol")
                with self.assertRaises(ValueError):
                    menu_install.install()
                config.write_text(config.read_text().replace('model = "mindlogic/gpt-6-sol"', 'model = "gpt-6-sol"'))
                menu_install.remove()
                self.assertEqual(tomllib.loads(config.read_text())["other"]["value"], 7)
                self.assertEqual(tomllib.loads(config.read_text())["model_provider"], "factchat")
                self.assertFalse((root / "state.json").exists())


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
        self.env.write_text('FACTCHAT_API_KEY=secret-mindlogic\n')
        self.server = menu_router.Router(("127.0.0.1", 0), manifest={"local_token":"local-test", "routes": {
            "gpt-6-sol": {"provider": "openai", "model": "gpt-6-sol"},
            "mindlogic/gpt-6-sol": {"provider": "mindlogic", "model": "gpt-6-sol"}}}, env_file=self.env)
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

    def request(self, model, extra=None):
        body = {"model": model, "stream": True, "input": [
            {"type": "reasoning", "encrypted_content": "opaque"},
            {"type": "function_call", "call_id": "call_1", "name": "echo", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"}]}
        body.update(extra or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("POST", "/responses", json.dumps(body), {"Authorization": "Bearer secret-openai",
                     menu_router.LOCAL_TOKEN_HEADER: "local-test",
                     "thread-id": "thread-test",
                     "chatgpt-account-id": "account", "Content-Type": "application/json"})
        result = conn.getresponse()
        status, data = result.status, result.read()
        conn.close()
        return status, data

    def test_routes_and_separates_credentials_and_tools(self):
        for model in ("gpt-6-sol", "mindlogic/gpt-6-sol", "gpt-6-sol"):
            status, data = self.request(model)
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
        self.assertEqual(self.request("mindlogic/gpt-6-sol", {"previous_response_id": "resp_other"})[0], 409)
        self.assertEqual(FakeConnection.calls, [])

    def test_reasoning_is_kept_only_on_stable_route(self):
        self.request("gpt-6-sol")
        self.request("gpt-6-sol")
        self.request("mindlogic/gpt-6-sol")
        self.assertEqual(len(FakeConnection.calls[0][2]["input"]), 2)
        self.assertEqual(len(FakeConnection.calls[1][2]["input"]), 3)
        self.assertEqual(len(FakeConnection.calls[2][2]["input"]), 2)

    def test_local_auth_is_required(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("POST", "/responses", '{"model":"mindlogic/gpt-6-sol","input":[]}',
                     {"Authorization": "Bearer fake", "Content-Type": "application/json"})
        result = conn.getresponse()
        self.assertEqual(result.status, 401)
        result.read()
        conn.close()
        self.assertEqual(FakeConnection.calls, [])


if __name__ == "__main__":
    unittest.main()
