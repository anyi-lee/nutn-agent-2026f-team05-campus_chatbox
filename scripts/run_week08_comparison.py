#!/usr/bin/env python3
"""Reproduce the offline comparison or serve a clearly labeled UI fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT)]
from campus_catalog import CATALOG_PATH
from campus_mas import EXAMPLE
from campus_service import CampusService, FACULTY_DATA_PATH, OFFICE_DATA_PATH
from gemini_email import GeminiEmailGenerator
from session_host import SessionHost
from week08_fixture import TestRoles

BASELINE = "4764e4c2a0733cbaead59b28494376b26adc8cff"
SOURCES = (FACULTY_DATA_PATH, OFFICE_DATA_PATH, CATALOG_PATH)
IMPLEMENTATION = (
    "app.py", "src/campus_mas.py", "src/campus_service.py", "src/campus_catalog.py",
    "src/session_host.py", "src/email_tools.py", "src/gemini_email.py",
    "ui/index.html", "ui/app.js", "ui/styles.css", "ui/email_directory.json",
    "scripts/run_with_gemini.sh", "scripts/run_week08_comparison.py",
    "tests/test_week08_mas.py", "tests/week08_fixture.py",
)


def build(backend, path):
    return SessionHost(
        service_factory=lambda: CampusService(campus_enabled=True, workflow_backend=backend,
                                              mas_provider=TestRoles(), email_generator=GeminiEmailGenerator(api_key=""),
                                              outbox_path=path / backend / "outbox.sqlite3"),
        source_paths=SOURCES,
    )


def ask(host, message):
    owner, session = host.bootstrap(None)
    http_status, result = host.handle(owner, session["csrf_token"], "/api/chat",
                                      {"session_id": session["session_id"], "generation": session["generation"],
                                       "message": message, "retriever": "bm25"})
    # Keep public facts and run events; omit session identifiers and CSRF.
    return {"http_status": http_status, **{key: result.get(key) for key in
           ("status", "answer", "citations", "workflow", "error")}}


def comparison(path):
    baseline, mas = build("baseline", path), build("mas", path)
    assert baseline.source_version == mas.source_version
    cases = {}
    for name, message in (("normal", EXAMPLE),
                          ("csie_colloquial", "陳榮銘教授的信箱跟資工系辦電話"),
                          ("unknown_teacher", "不存在老師的信箱和電機系辦電話"),
                          ("missing_office_phone", "許世昌老師的信箱和應數系辦電話")):
        cases[name] = {"input": message, "baseline": ask(baseline, message),
                       "candidate": ask(mas, message)}
    body = cases["normal"]["candidate"]
    assert body["status"] == "ok" and len(body["citations"]) == 2
    assert "scshei@mail.nutn.edu.tw" in body["answer"] and "(06)2602315" in body["answer"]
    colloquial = cases["csie_colloquial"]["candidate"]
    assert colloquial["workflow"]["stop_reason"] == "BOTH_VERIFIED" and len(colloquial["citations"]) == 2
    assert "rmchen@mail.nutn.edu.tw" in colloquial["answer"] and "7701、7702" in colloquial["answer"]
    assert cases["unknown_teacher"]["candidate"]["status"] == "partial"
    assert cases["missing_office_phone"]["candidate"]["status"] == "partial"
    assert all(case["candidate"]["workflow"]["write_actions"] == 0 for case in cases.values())
    return {"recorded_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
            "evidence_mode": "TEST_PROVIDER", "live_status": "LIVE_NOT_RUN", "live_calls": 0,
            "baseline_sha": BASELINE, "checkout_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "version_note": "checkout_head does not identify uncommitted candidate changes",
            "comparison_note": "baseline uses the original route with the same current sources; it is not a separate checkout",
            "input_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in SOURCES},
            "worktree_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in IMPLEMENTATION},
            "same_source_snapshot": baseline.source_version, "cases": cases}


def serve(path, port):
    # Import app only for the real handler. Explicit test-only host replaces the
    # runtime instance; all fixture operations use the temporary directory.
    from app import CampusRequestHandler
    host = build("mas", path)

    class Handler(CampusRequestHandler):
        session_host = host

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"TEST_PROVIDER (0 live calls): http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="run offline browser demo on localhost")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--output", type=Path, help="write the offline comparison JSON")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="campus-week08-") as tmp:
        if args.serve:
            serve(Path(tmp), args.port)
        else:
            report = json.dumps(comparison(Path(tmp)), ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.write_text(report, encoding="utf-8")
            else:
                print(report, end="")
