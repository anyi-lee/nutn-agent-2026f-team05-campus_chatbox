"""Route B acceptance, role boundaries, and the original HTTP entry point."""
from __future__ import annotations

import http.cookiejar
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campus_catalog import CATALOG_PATH
from campus_mas import EXAMPLE, GeminiCampusRoles, compound_request
from campus_service import CampusService, FACULTY_DATA_PATH, OFFICE_DATA_PATH
from gemini_email import GeminiEmailGenerator
from session_host import SessionHost
from week08_fixture import TestRoles


class Week08Tests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.provider = TestRoles()
        self.service = self.build()

    def build(self, backend="mas"):
        return CampusService(campus_enabled=True, workflow_backend=backend, mas_provider=self.provider,
                             email_generator=GeminiEmailGenerator(api_key=""), outbox_path=self.tmp / "outbox.db")

    def host(self):
        return SessionHost(service_factory=lambda: self.service,
                           source_paths=(FACULTY_DATA_PATH, OFFICE_DATA_PATH, CATALOG_PATH))

    def test_normal_roles_and_two_verified_facts(self):
        result = self.service.chat(EXAMPLE)
        self.assertEqual(result["status"], "ok")
        self.assertIn("scshei@mail.nutn.edu.tw", result["answer"])
        self.assertIn("(06)2602315", result["answer"])
        trace = result["workflow"]
        self.assertEqual([(r, s) for r, s, _ in self.provider.calls],
                         [("coordinator", "plan"), ("faculty", "tool_proposal"),
                          ("office", "tool_proposal"), ("coordinator", "integrate")])
        self.assertEqual(self.provider.calls[1][2], {"target": {"name": "許世昌"}, "access": "READ_ONLY"})
        self.assertEqual(self.provider.calls[2][2], {"target": {"unit": "電機系"}, "access": "READ_ONLY"})
        self.assertEqual(len(self.provider.calls[3][2]["results"]), 2)
        self.assertEqual(len({c["source_id"] for c in result["citations"]}), 2)
        self.assertEqual({row["owner"] for row in trace["ledger"]}, {"faculty", "office"})
        for item in trace["result_board"]:
            self.assertEqual(item["source_version"], trace["source_version"])
            self.assertEqual(item["to"], "coordinator")
            self.assertTrue(item["task_id"].startswith(trace["run_id"]))
        self.assertEqual((trace["model_calls"], trace["live_calls"], trace["write_actions"]), (4, 0, 0))
        self.assertEqual(trace["live_status"], "LIVE_NOT_RUN")
        self.assertIsNone(trace["total_tokens"])
        self.assertFalse(self.service.pending_drafts)
        self.assertIsNone(result["suggested_action"])

    def test_reverse_order_and_exact_scoped_grammar(self):
        self.assertEqual(compound_request("查電機系辦公室電話，以及電機系的許世昌老師的公開 Email？"),
                         {"office": {"unit": "電機系"}, "faculty": {"name": "許世昌", "department": "電機系"}})
        for msg in [EXAMPLE + "並幫我寄信", "他老師的信箱和系辦電話", "我的老師的信箱和電機系辦電話",
                    EXAMPLE + "和國文系辦電話", "老師電話和系辦信箱",
                    "陳榮銘教授的信箱跟研究方向", "陳榮銘教授的信箱跟資工系辦電話跟地址"]:
            self.assertIsNone(compound_request(msg), msg)
        self.provider.override = lambda r, s, _: {"action": "office_first"} if r == "coordinator" else None
        result = self.service.chat(EXAMPLE)
        self.assertEqual(self.provider.calls[1][0], "office")
        self.assertTrue(result["answer"].startswith("電機工程學系辦公室電話"))

    def test_csie_phone_uses_official_office_snapshot_in_compound_requests(self):
        for message in ("陳榮銘教授的信箱跟資工系辦電話", "陳榮銘教授的信箱和資工系辦電話",
                        "資訊工程學系辦公室電話，以及陳榮銘教授的信箱"):
            with self.subTest(message=message):
                result = self.service.chat(message)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["workflow"]["stop_reason"], "BOTH_VERIFIED")
                self.assertIn("rmchen@mail.nutn.edu.tw", result["answer"])
                self.assertIn("(06) 2606123 轉 7701、7702", result["answer"])
                self.assertNotIn("csie@mail.nutn.edu.tw", result["answer"])
                self.assertEqual(len(result["citations"]), 2)
                cite = next(c for c in result["citations"] if c["source_id"] == "nutn-csie-homepage-contact")
                self.assertEqual(cite["chunk_id"], "office-csie-main")
                self.assertEqual(cite["url"], "https://csie.nutn.edu.tw/")

    def test_csie_phone_supplement_still_requires_official_active_source(self):
        record = self.service.mas.office_supplements["資訊工程學系"]
        for fields in ({"source_url": "https://example.invalid/"}, {"validity": "inactive"}):
            with self.subTest(fields=fields), patch.dict(record, fields):
                result = self.service.chat("陳榮銘教授的信箱跟資工系辦電話")
                self.assertEqual(result["workflow"]["stop_reason"], "MAS_PARTIAL")
                self.assertNotIn("2606123", result["answer"])
                self.assertIn("rmchen@mail.nutn.edu.tw", result["answer"])

    def test_missing_teacher_keeps_only_office_fact(self):
        result = self.service.chat("不存在老師的信箱和電機系辦電話")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["citations"]), 1)
        self.assertIn("未收錄", result["answer"])
        self.assertIn("(06)2602315", result["answer"])
        self.assertNotIn("@", result["answer"])

    def test_known_unit_without_phone_is_not_substituted(self):
        result = self.service.chat("許世昌老師的信箱和應數系辦電話")
        self.assertEqual(result["status"], "partial")
        self.assertEqual([r["status"] for r in result["workflow"]["result_board"]], ["found", "not_found"])
        self.assertNotIn("2602315", result["answer"])

    def test_distinct_same_name_requires_department_but_same_address_deduplicates(self):
        self.assertEqual(self.service.chat(EXAMPLE)["status"], "ok")
        mas = self.service.mas
        existing = next(r for r in mas.people if r.get("person_name") == "許世昌")
        mas.people += ({**existing, "department": "國語文學系", "public_email": "alternate@example.invalid"},)
        result = self.service.chat(EXAMPLE)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["workflow"]["result_board"][0]["status"], "ambiguous")
        self.assertNotIn("@", result["answer"])
        self.assertEqual(self.service.chat("電機系的" + EXAMPLE)["status"], "ok")

    def test_outside_tool_or_target_is_blocked(self):
        for proposal in ({"tool": "send_email", "arguments": {"name": "許世昌"}},
                         {"tool": "lookup_office_phone", "arguments": {"name": "許世昌"}},
                         {"tool": "lookup_faculty_email", "arguments": {"name": "其他人"}}):
            with self.subTest(proposal=proposal), patch.object(self.service.mas, "_faculty") as faculty:
                self.provider.override = lambda r, s, c: proposal if r == "faculty" else None
                result = self.service.chat(EXAMPLE)
                faculty.assert_not_called()
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["workflow"]["result_board"][0]["status"], "blocked")
                self.assertEqual(result["workflow"]["write_actions"], 0)

    def test_timeout_keeps_independent_result_without_retry(self):
        def timeout(role, stage, context):
            if role == "faculty":
                raise TimeoutError()
        self.provider.override = timeout
        result = self.service.chat(EXAMPLE)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["workflow"]["result_board"][0]["status"], "provider_failed")
        self.assertEqual(len([c for c in self.provider.calls if c[0] == "faculty"]), 1)

    def test_no_model_and_invalid_plan_stop_before_delegation(self):
        self.provider.configured = False
        result = self.service.chat(EXAMPLE)
        self.assertEqual(result["error"]["code"], "MAS_NOT_CONFIGURED")
        self.assertFalse(self.provider.calls)
        self.provider.configured = True
        self.provider.override = lambda *args: {"action": "send_email"}
        result = self.service.chat(EXAMPLE)
        self.assertEqual(result["error"]["code"], "MAS_PLAN_FAILED")
        self.assertFalse(result["workflow"]["result_board"])

    def test_integration_failure_reports_host_fallback_not_success(self):
        self.provider.override = lambda role, stage, _: {"action": "fabricate"} if stage == "integrate" else None
        result = self.service.chat(EXAMPLE)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["workflow"]["final_role"], "host")
        self.assertEqual(len(result["citations"]), 2)

    def test_zero_verified_results_stop_without_integration(self):
        result = self.service.chat("不存在老師的信箱和不存在系辦電話")
        self.assertEqual(result["error"]["code"], "MAS_NO_VERIFIED_RESULT")
        self.assertEqual(result["workflow"]["model_calls"], 3)
        self.assertFalse(result["citations"])

    def test_existing_single_queries_use_baseline(self):
        baseline = self.build("baseline")
        self.assertIsNone(baseline.chat(EXAMPLE).get("workflow"))
        for msg in ["許世昌老師的信箱", "電機系辦電話", "幫我寫信給許世昌老師"]:
            self.assertEqual(self.service.chat(msg), baseline.chat(msg))
        self.assertFalse(self.provider.calls)

    def test_signed_in_mas_does_not_even_fetch_memory(self):
        host = self.host()
        owner, session = host.bootstrap(None)
        # Use a synthetic session identity; any attempted memory access fails.
        host._users[owner].account_id = "test-owner"
        host.memory_planner = Mock()
        host.accounts.list_memories = Mock(side_effect=AssertionError("MAS read private memory"))
        _, result = host.handle(owner, session["csrf_token"], "/api/chat", {
            "session_id": session["session_id"], "generation": session["generation"], "message": " " * 160 + EXAMPLE,
        })
        self.assertEqual(result["status"], "ok")
        host.accounts.list_memories.assert_not_called()
        host.memory_planner.plan.assert_not_called()
        self.assertEqual(result["personal_memory_used"], [])

    def test_reset_discards_late_mas_response(self):
        entered, released = threading.Event(), threading.Event()
        def block(role, stage, _):
            if role == "faculty":
                entered.set()
                if not released.wait(timeout=3):
                    raise TimeoutError()
        self.provider.override = block
        host = self.host()
        owner, session = host.bootstrap(None)
        fields = {"session_id": session["session_id"], "generation": session["generation"]}
        results = []
        worker = threading.Thread(target=lambda: results.append(host.handle(owner, session["csrf_token"], "/api/chat",
                                  {**fields, "message": EXAMPLE})), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=3))
            self.assertEqual(host.handle(owner, session["csrf_token"], "/api/session/reset", fields)[0], 200)
        finally:
            released.set()
            worker.join(timeout=5)
        self.assertEqual(results[0][0], 409)
        self.assertEqual(results[0][1]["error"]["code"], "STALE_SESSION")
        self.assertNotIn("workflow", results[0][1])
        self.assertFalse(self.service.pending_drafts)

    def test_real_gemini_transport_contract_without_network(self):
        client = GeminiEmailGenerator(api_key="fake-test-only")
        provider = GeminiCampusRoles(client)
        cases = [
            ({"functionCall": {"name": "lookup_faculty_email", "args": {"name": "許世昌"}}}, "faculty", "tool_proposal"),
            ({"functionCall": {"name": "lookup_office_phone", "args": {"unit": "電機系"}}}, "office", "tool_proposal"),
            ({"text": '{"action":"faculty_first"}'}, "coordinator", "plan"),
        ]
        for part, role, stage in cases:
            args = part.get("functionCall", {}).get("args", {})
            with patch.object(client, "_post", return_value={"candidates": [
                {"finishReason": "STOP", "content": {"parts": [part]}}],
                "usageMetadata": {"totalTokenCount": 12}}) as post:
                self.assertEqual(provider.step(role, stage, {"target": args}, ("faculty_first",)).total_tokens, 12)
                payload = post.call_args.args[0]
                if "functionCall" in part:
                    self.assertEqual([d["name"] for d in payload["tools"][0]["functionDeclarations"]],
                                     [part["functionCall"]["name"]])
                else:
                    self.assertNotIn("tools", payload)
                self.assertNotIn("fake-test-only", json.dumps(payload))

    def test_original_http_entry_and_csrf(self):
        from app import CampusRequestHandler
        host = self.host()

        class Handler(CampusRequestHandler):
            session_host = host
            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                client = request.build_opener(request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                with client.open(base + "/api/session") as response:
                    session = json.load(response)["session"]
                body = json.dumps({"session_id": session["session_id"], "generation": session["generation"],
                                   "message": "陳榮銘教授的信箱跟資工系辦電話", "retriever": "bm25"}).encode()
                headers = {"Content-Type": "application/json", "X-CSRF-Token": session["csrf_token"]}
                with client.open(request.Request(base + "/api/chat", body, headers)) as response:
                    result = json.load(response)
                self.assertEqual(result["workflow"]["status"], "ok")
                self.assertEqual(result["workflow"]["final_role"], "coordinator")
                self.assertEqual(result["workflow"]["stop_reason"], "BOTH_VERIFIED")
                with self.assertRaises(request.HTTPError) as rejected:
                    client.open(request.Request(base + "/api/chat", body, {"Content-Type": "application/json"}))
                with rejected.exception as response:
                    self.assertEqual(response.code, 403)
            finally:
                server.shutdown()
                thread.join()
