import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from unittest import mock

from sourcebound.config import ConfigError, LLMSettings, load_settings
from sourcebound.llm import LLMError, OpenAICompatibleModel, build_model, parse_json_object


class FakeProvider(BaseHTTPRequestHandler):
    script: ClassVar[list] = []
    seen: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        status, payload, headers = FakeProvider.script.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def ok(text):
    return (200, {"choices": [{"message": {"role": "assistant", "content": text}}]}, {})


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.settings = LLMSettings(
            provider="openai_compatible",
            base_url=f"http://127.0.0.1:{cls.server.server_address[1]}/v1",
            model="test-model",
            api_key="sk-secret-value",
            timeout_seconds=5,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeProvider.script = []
        FakeProvider.seen = []
        self.patch = mock.patch("sourcebound.llm.time.sleep")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_successful_completion_sends_expected_request(self):
        FakeProvider.script = [ok("hello")]
        model = OpenAICompatibleModel(self.settings)
        self.assertEqual(model.complete("sys", "user", json_mode=True), "hello")
        sent = FakeProvider.seen[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["auth"], "Bearer sk-secret-value")
        self.assertEqual(sent["body"]["model"], "test-model")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})

    def test_retries_on_rate_limit_then_succeeds(self):
        FakeProvider.script = [(429, {"error": "slow down"}, {"Retry-After": "1"}), (503, {}, {}), ok("done")]
        self.assertEqual(OpenAICompatibleModel(self.settings).complete("s", "u"), "done")
        self.assertEqual(len(FakeProvider.seen), 3)

    def test_gives_up_after_max_attempts(self):
        FakeProvider.script = [(500, {}, {})] * 3
        with self.assertRaisesRegex(LLMError, "could not be reached: HTTP 500"):
            OpenAICompatibleModel(self.settings).complete("s", "u")

    def test_auth_error_does_not_leak_key(self):
        FakeProvider.script = [(401, {"error": "bad key sk-secret-value"}, {})]
        with self.assertRaises(LLMError) as ctx:
            OpenAICompatibleModel(self.settings).complete("s", "u")
        self.assertIn("rejected the API key", str(ctx.exception))
        self.assertNotIn("sk-secret", str(ctx.exception))
        self.assertEqual(len(FakeProvider.seen), 1)  # no retry on auth errors

    def test_malformed_responses(self):
        FakeProvider.script = [(200, b"<html>oops", {})]
        with self.assertRaisesRegex(LLMError, "not valid JSON"):
            OpenAICompatibleModel(self.settings).complete("s", "u")
        FakeProvider.script = [(200, {"unexpected": True}, {})]
        with self.assertRaisesRegex(LLMError, "unexpected response shape"):
            OpenAICompatibleModel(self.settings).complete("s", "u")

    def test_settings_repr_hides_key(self):
        self.assertNotIn("sk-secret", repr(self.settings))


class ParsingTests(unittest.TestCase):
    def test_parse_json_object_tolerates_fences(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(LLMError):
            parse_json_object("no json here")
        with self.assertRaises(LLMError):
            parse_json_object("{broken")


class ConfigTests(unittest.TestCase):
    def test_defaults_to_free_extractive_mode(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = load_settings()
        self.assertFalse(settings.llm.enabled)
        self.assertIsNone(build_model(settings.llm))
        self.assertNotIn("api_key", json.dumps(settings.public_view()))

    def test_invalid_values_are_rejected(self):
        for env in [
            {"SOURCEBOUND_LLM_PROVIDER": "magic"},
            {"SOURCEBOUND_LLM_PROVIDER": "openai_compatible"},
            {"SOURCEBOUND_LLM_BASE_URL": "ftp://x"},
            {"SOURCEBOUND_MAX_UPLOAD_MB": "lots"},
            {"SOURCEBOUND_MAX_UPLOAD_MB": "999"},
            {"SOURCEBOUND_ALLOW_URL_FETCH": "maybe"},
        ]:
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True), self.assertRaises(ConfigError):
                load_settings()

    def test_provider_configuration(self):
        env = {
            "SOURCEBOUND_LLM_PROVIDER": "openai_compatible",
            "SOURCEBOUND_LLM_BASE_URL": "http://localhost:11434/v1/",
            "SOURCEBOUND_LLM_MODEL": "llama3.1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertTrue(settings.llm.enabled)
        self.assertEqual(settings.llm.base_url, "http://localhost:11434/v1")
        self.assertEqual(settings.public_view()["llm_model"], "llama3.1")


if __name__ == "__main__":
    unittest.main()
