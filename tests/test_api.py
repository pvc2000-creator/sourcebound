import json
import unittest
from pathlib import Path

from sourcebound.config import Settings

from .helpers import OTHER_WORKSPACE, WORKSPACE, FakeModel, LiveServer, make_pdf, multipart, writer_from_evidence

QUESTION = "How much will the Riverside Library renovation cost and how long will it take?"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = LiveServer().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.live.__exit__(None, None, None)

    def setUp(self):
        self.live.request("DELETE", "/api/workspace")
        self.live.request("DELETE", "/api/workspace", workspace=OTHER_WORKSPACE)

    def upload(self, name, content, workspace=WORKSPACE):
        body, ctype = multipart(name, content)
        status, _, raw = self.live.request("POST", "/api/sources/upload", data=body, headers={"Content-Type": ctype}, workspace=workspace)
        return status, json.loads(raw)

    def test_index_and_security_headers(self):
        status, headers, body = self.live.request("GET", "/", workspace=None)
        self.assertEqual(status, 200)
        self.assertIn(b"Sourcebound", body)
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")

    def test_config_is_public_and_secret_free(self):
        status, cfg = self.live.json("GET", "/api/config", workspace=None)
        self.assertEqual(status, 200)
        self.assertFalse(cfg["llm_available"])
        self.assertNotIn("api_key", json.dumps(cfg))

    def test_workspace_header_is_required_and_validated(self):
        for ws in [None, "short", "../../etc", "Z" * 32]:
            with self.subTest(ws=ws):
                status, body = self.live.json("GET", "/api/sources", workspace=ws)
                self.assertEqual(status, 400)
                self.assertIn("workspace", body["error"])

    def test_full_research_workflow(self):
        status, body = self.live.json("POST", "/api/sources/sample")
        self.assertEqual(status, 201)
        self.assertEqual(len(body["sources"]), 4)
        status, body = self.live.json("POST", "/api/sources/sample")
        self.assertEqual(status, 409)

        status, body = self.live.json("POST", "/api/runs", json_body={"question": QUESTION})
        self.assertEqual(status, 202)
        run = self.live.wait_for_run(body["id"])
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["report"]["mode"], "extractive")
        self.assertEqual(len(run["report"]["conflicts"]), 2)
        self.assertEqual(run["events"], run["report"]["trace"])

        status, runs = self.live.json("GET", "/api/runs")
        self.assertEqual([r["id"] for r in runs["runs"]], [run["id"]])

        status, headers, md = self.live.request("GET", f"/api/runs/{run['id']}/export.md")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        self.assertIn(b"## Conflicting figures", md)
        status, exported = self.live.json("GET", f"/api/runs/{run['id']}/export.json")
        self.assertEqual(exported["id"], run["id"])
        self.assertEqual(self.live.json("GET", f"/api/runs/{run['id']}/export.pdf")[0], 404)

        self.assertEqual(self.live.request("DELETE", f"/api/runs/{run['id']}")[0], 204)
        self.assertEqual(self.live.json("GET", f"/api/runs/{run['id']}")[0], 404)

    def test_runs_can_be_scoped_to_selected_sources(self):
        _, body = self.live.json("POST", "/api/sources/sample")
        gazette = next(s["id"] for s in body["sources"] if "Gazette" in s["title"])
        _, created = self.live.json("POST", "/api/runs", json_body={"question": QUESTION, "source_ids": [gazette]})
        run = self.live.wait_for_run(created["id"])
        self.assertEqual(run["report"]["stats"]["sources_in_scope"], 1)
        self.assertEqual({c["source_id"] for c in run["report"]["citations"]}, {gazette})

    def test_workspaces_are_isolated(self):
        _, body = self.live.json("POST", "/api/sources/sample")
        source_id = body["sources"][0]["id"]
        _, created = self.live.json("POST", "/api/runs", json_body={"question": QUESTION})
        self.live.wait_for_run(created["id"])
        self.assertEqual(self.live.json("GET", "/api/sources", workspace=OTHER_WORKSPACE)[1]["sources"], [])
        self.assertEqual(self.live.json("GET", f"/api/runs/{created['id']}", workspace=OTHER_WORKSPACE)[0], 404)
        self.assertEqual(self.live.request("DELETE", f"/api/sources/{source_id}", workspace=OTHER_WORKSPACE)[0], 404)
        status, _ = self.live.json(
            "POST", "/api/runs", json_body={"question": QUESTION, "source_ids": [source_id]}, workspace=OTHER_WORKSPACE
        )
        self.assertEqual(status, 400)

    def test_upload_types_and_limits(self):
        status, body = self.upload("report.pdf", make_pdf(["The harbour wall was rebuilt in stone after the storm."]))
        self.assertEqual(status, 201)
        self.assertEqual((body["source"]["kind"], body["source"]["pages"]), ("pdf", 1))
        status, body = self.upload("../../secret/notes.txt", b"These are field notes about the harbour wall repairs.")
        self.assertEqual((status, body["source"]["title"]), (201, "notes.txt"))
        status, body = self.upload("tool.exe", b"MZ" * 100)
        self.assertEqual(status, 422)
        self.assertIn("Unsupported file type", body["error"])
        status, body = self.upload("huge.txt", b"a " * 700_000)  # over the 1 MB test limit
        self.assertEqual(status, 413)

    def test_source_limit(self):
        for i in range(8):
            status, _ = self.live.json(
                "POST", "/api/sources/text", json_body={"title": f"n{i}", "text": f"Note {i} about the harbour wall and its repairs."}
            )
            self.assertEqual(status, 201)
        status, body = self.live.json(
            "POST", "/api/sources/text", json_body={"title": "x", "text": "One more note about the harbour wall."}
        )
        self.assertEqual(status, 409)
        self.assertIn("up to 8 sources", body["error"])

    def test_bad_requests_are_explained(self):
        cases = [
            ("POST", "/api/runs", {"question": "hi"}, 422),
            ("POST", "/api/runs", {"question": QUESTION}, 400),  # no sources yet
            ("POST", "/api/runs", {"question": QUESTION, "source_ids": ["'; DROP TABLE runs; --"]}, 400),
            ("POST", "/api/runs", {"question": QUESTION, "use_llm": True}, 400),  # no model configured
            ("POST", "/api/sources/text", {"title": 5, "text": []}, 400),
            ("POST", "/api/sources/url", {"url": "http://169.254.169.254/latest/meta-data"}, 422),
            ("POST", "/api/sources/url", {"url": "file:///etc/passwd"}, 422),
        ]
        for method, path, payload, expected in cases:
            with self.subTest(path=path, payload=payload):
                status, body = self.live.json(method, path, json_body=payload)
                self.assertEqual(status, expected, body)
                self.assertTrue(body["error"])
        status, _, _ = self.live.request("POST", "/api/runs", data=b"not json", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.live.request("POST", "/api/runs", data=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(self.live.json("GET", "/api/runs/not-a-valid-id")[0], 404)
        self.assertEqual(self.live.json("GET", "/api/nothing-here")[0], 404)

    def test_html_in_sources_is_returned_as_data(self):
        payload = '<img src=x onerror="alert(1)"> The council approved <b>the plan</b> on Monday.'
        status, body = self.live.json("POST", "/api/sources/text", json_body={"title": "<script>alert(1)</script>", "text": payload})
        self.assertEqual(status, 201)
        self.assertEqual(body["source"]["title"], "scriptalert(1)/script")

    def test_delete_workspace_removes_everything(self):
        self.live.json("POST", "/api/sources/sample")
        self.assertEqual(self.live.request("DELETE", "/api/workspace")[0], 204)
        self.assertEqual(self.live.json("GET", "/api/sources")[1]["sources"], [])


class LLMApiTests(unittest.TestCase):
    def test_ai_mode_end_to_end_with_scripted_model(self):
        plan = {"sub_questions": [{"question": "What will the renovation cost?", "search_queries": ["renovation cost million"]}]}
        model = FakeModel([plan, writer_from_evidence()])
        with LiveServer(model=model) as live:
            _, cfg = live.json("GET", "/api/config", workspace=None)
            self.assertTrue(cfg["llm_available"])
            live.json("POST", "/api/sources/sample")
            _, created = live.json("POST", "/api/runs", json_body={"question": "What will the renovation cost?", "use_llm": True})
            run = live.wait_for_run(created["id"])
            self.assertEqual(run["report"]["mode"], "llm")
            self.assertEqual(run["report"]["model"], "fake-model")
            self.assertTrue(run["report"]["sections"][0]["sentences"])


class RateLimitTests(unittest.TestCase):
    def test_runs_per_minute(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(db_path=Path(tmp) / "rl.db", runs_per_minute=2)
            with LiveServer(settings=settings) as live:
                live.json("POST", "/api/sources/sample")
                for _ in range(2):
                    _, created = live.json("POST", "/api/runs", json_body={"question": QUESTION})
                    live.wait_for_run(created["id"])
                status, body = live.json("POST", "/api/runs", json_body={"question": QUESTION})
                self.assertEqual(status, 429)
                self.assertIn("Too many", body["error"])


class RateLimiterUnitTests(unittest.TestCase):
    def test_idle_keys_are_pruned(self):
        from sourcebound.app import RateLimiter

        limiter = RateLimiter()
        limiter.MAX_KEYS = 5
        for i in range(20):
            self.assertTrue(limiter.allow(f"k{i}", 1, window=0.0))
        self.assertLessEqual(len(limiter._events), 7)
        self.assertFalse(limiter.allow("busy", 0))


if __name__ == "__main__":
    unittest.main()
