import json
import hashlib
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(
    prefix="aura-tests-", dir=Path(__file__).resolve().parents[1] / "data"
)
os.environ["AURA_DB_PATH"] = str(Path(_TEMP.name) / "aura.sqlite3")
from aura import server as aura_server


class WorkflowApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), aura_server.AuraHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        req = urllib.request.Request(cls.base + "/api/auth/setup", data=json.dumps({"password":"test-workspace-password"}).encode(), method="POST", headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            cls.cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0]
            cookie_header = response.headers.get("Set-Cookie", "").casefold()
            if "httponly" not in cookie_header or "samesite=strict" not in cookie_header:
                raise AssertionError("Auth cookie must be HttpOnly and SameSite=Strict.")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(timeout=2); _TEMP.cleanup()

    def request(self, path, method="GET", body=None, content_type="application/json"):
        data = body
        if isinstance(body, (dict, list)): data = json.dumps(body).encode()
        headers = {"Content-Type": content_type} if data is not None else {}
        if getattr(self, "cookie", None): headers["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.headers.get("Set-Cookie"):
                type(self).cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0]
            return response.status, json.loads(response.read()) if response.headers.get_content_type() == "application/json" else response.read()

    def assert_http_error(self, request, status):
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, status)
            error.close()
        else:
            self.fail(f"Expected HTTP {status} for {request.full_url}")

    def test_workspace_authentication_and_logout(self):
        setup_req = urllib.request.Request(self.base + "/api/auth/setup", data=json.dumps({"password":"another-password-value"}).encode(), method="POST", headers={"Content-Type":"application/json"})
        self.assert_http_error(setup_req, 409)
        bad_login = urllib.request.Request(self.base + "/api/auth/login", data=json.dumps({"password":"wrong-password"}).encode(), method="POST", headers={"Content-Type":"application/json"})
        self.assert_http_error(bad_login, 401)
        req = urllib.request.Request(self.base + "/api/decisions")
        self.assert_http_error(req, 401)
        self.request("/api/auth/logout", "POST", {})
        self.assert_http_error(req, 401)
        self.request("/api/auth/login", "POST", {"password":"test-workspace-password"})
        self.assertTrue(self.cookie.startswith("AURA_SESSION="))

    def test_cross_origin_mutation_is_rejected_and_metrics_are_aggregate(self):
        request = urllib.request.Request(
            self.base + "/api/auth/logout", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://attacker.example",
                     "Cookie": self.cookie},
        )
        self.assert_http_error(request, 403)
        _, metrics = self.request("/api/metrics")
        self.assertGreater(metrics["requestsTotal"], 0)
        self.assertIn("requestLatencyMs", metrics)
        self.assertNotIn("test-workspace-password", json.dumps(metrics))
        self.assertNotIn("Choose a role", json.dumps(metrics))

    def test_ai_request_budget_enforces_configured_per_session_limit(self):
        token = uuid.uuid4().hex
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        client = SimpleNamespace(_session_token=lambda: token, client_address=("127.0.0.1", 0))
        try:
            with patch.dict(os.environ, {"AURA_AI_REQUESTS_PER_MINUTE": "1"}):
                aura_server.AuraHandler._consume_ai_budget(client)
                with self.assertRaises(aura_server.RateLimitError):
                    aura_server.AuraHandler._consume_ai_budget(client)
        finally:
            aura_server._ai_requests_by_session.pop(key, None)

    def test_create_evaluate_ingest_verify_apply_and_scenario_compare(self):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
        model = {"options": [{"id":a,"name":"Internship"},{"id":b,"name":"Research"}],
                 "criteria": [{"id":c1,"name":"Mentorship","direction":"benefit","importance":4,"valueType":"ordinal"},
                              {"id":c2,"name":"Weekly hours","direction":"cost","importance":2,"valueType":"ordinal","constraint":{"operator":"lte","threshold":4}}],
                 "assessments": {a:{c1:{"value":5},c2:{"value":3}},b:{c1:{"value":3},c2:{"value":2}}}}
        _, decision = self.request("/api/decisions", "POST", {"title":"Choose a role","question":"Which role is a better fit?","model":model})
        did = decision["id"]
        _, baseline = self.request(f"/api/decisions/{did}/evaluations", "POST", {"revisionId":decision["currentRevision"]["id"]})
        self.assertEqual(baseline["result"]["status"], "complete")
        _, robustness = self.request(f"/api/decisions/{did}/robustness", "POST", {
            "revisionId": decision["currentRevision"]["id"],
            "assessmentRanges": {a: {c1: [3, 5]}}, "weightRanges": {c2: [1, 3]},
            "iterations": 100, "seed": 23
        })
        self.assertEqual(robustness["iterations"], 100)
        self.assertEqual(robustness["revisionId"], decision["currentRevision"]["id"])
        _, saved_runs = self.request(f"/api/decisions/{did}/robustness-runs")
        self.assertEqual(len(saved_runs["runs"]), 1)
        _, replay = self.request(f"/api/decisions/{did}/robustness", "POST", saved_runs["runs"][0]["settings"])
        self.assertEqual(replay["seed"], robustness["seed"])
        self.assertEqual(replay["options"], robustness["options"])
        boundary = "----AURA" + uuid.uuid4().hex
        document_text = b"The internship provides a weekly mentor meeting and code review. Weekly workload averages three hours per day."
        payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"role.txt\"\r\nContent-Type: text/plain\r\n\r\n".encode() + document_text + f"\r\n--{boundary}--\r\n".encode())
        self.request(f"/api/decisions/{did}/documents", "POST", payload, f"multipart/form-data; boundary={boundary}")
        # Upload processing is intentionally asynchronous; wait for the worker
        # to publish its chunks before exercising retrieval.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            documents = self.request(f"/api/decisions/{did}/documents")[1]["documents"]
            if documents and documents[0]["parse_status"] in {"ready", "failed"}:
                break
            time.sleep(0.05)
        self.assertTrue(documents, "Upload should be recorded before processing completes.")
        self.assertEqual(documents[0]["parse_status"], "ready", "Uploaded text should finish processing.")
        _, found = self.request(f"/api/decisions/{did}/evidence?q=mentor%20meeting")
        self.assertTrue(found["passages"])
        passage = found["passages"][0]
        self.assertIn("mentor meeting", passage["text"])
        try:
            self.request(f"/api/decisions/{did}/evidence/apply", "POST", {"chunkId":passage["id"],"optionId":a,"criterionId":c1,"value":5})
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 422)
            error.close()
        else:
            self.fail("Unverified evidence must not be applied.")
        self.request(f"/api/evidence/{passage['id']}", "PATCH", {"status":"verified"})
        _, revised = self.request(f"/api/decisions/{did}/evidence/apply", "POST", {"chunkId":passage["id"],"optionId":a,"criterionId":c1,"value":5})
        self.assertEqual(revised["currentRevision"]["revisionNo"], 2)
        _, scenario = self.request(f"/api/decisions/{did}/scenarios", "POST", {"baselineEvaluationId":baseline["id"],"name":"Mentorship becomes decisive","overrides":{"weights":{c1:8}}})
        _, compared = self.request(f"/api/scenarios/{scenario['id']}/evaluate", "POST", {})
        self.assertEqual(compared["comparison"]["baselineEvaluationId"], baseline["id"])
        self.assertTrue(compared["comparison"]["scoreChanges"])
        _, current = self.request(f"/api/decisions/{did}")
        self.assertEqual(current["currentRevision"]["revisionNo"], 2)
        _, export = self.request(f"/api/decisions/{did}/export")
        self.assertEqual(export["decision"]["id"], did)
        self.assertTrue(export["evidencePassages"])
        _, rejected = self.request(f"/api/evidence/{passage['id']}", "PATCH", {"status":"rejected"})
        self.assertEqual(rejected["decisionRevision"], 3)
        _, current = self.request(f"/api/decisions/{did}")
        self.assertNotIn(c1, current["currentRevision"]["model"]["assessments"][a])
        self.assertEqual(self.request(f"/api/evaluations/{baseline['id']}")[1]["revision_id"], decision["currentRevision"]["id"])


if __name__ == "__main__": unittest.main()
