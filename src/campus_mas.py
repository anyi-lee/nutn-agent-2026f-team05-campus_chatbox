"""Week 08 B: a bounded, read-only coordinator and two specialists."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass

from campus_catalog import official_url
from gemini_email import GeminiEmailError, GeminiEmailGenerator

EXAMPLE = "許世昌老師的信箱和電機系辦電話"
ROLES = ("faculty", "office")
TOOLS = {"faculty": "lookup_faculty_email", "office": "lookup_office_phone"}
LABELS = {"faculty": "師資專家", "office": "行政專家", "coordinator": "召集人"}
NAME = r"[\u3400-\u9fff]{2,4}"
UNIT = r"[\u3400-\u9fff]{1,20}"
FACULTY = re.compile(
    rf"(?:(?P<department>{UNIT}系)(?:的)?\s*)?(?P<name>{NAME})(?:老師|教授)(?:的)?\s*(?:公開\s*)?(?:信箱|電子郵件|e-?mail)",
    re.I,
)
OFFICE = re.compile(rf"(?P<unit>{UNIT}系)(?:的)?辦(?:公室)?(?:的)?\s*(?:公開)?(?:聯絡)?電話")
OFFICE_ALTERNATE = re.compile(rf"(?P<unit>{UNIT}系)\s*(?:的)?\s*電話")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"[+()\d\s-]+")


def compound_request(message: str) -> dict[str, dict[str, str]] | None:
    """Recognize exactly two supported public fields; never infer new targets."""
    message = message.strip()
    if len(message) > 160:
        return None
    query = re.sub(r"^(?:請問|請幫我查(?:一下)?|幫我查(?:一下)?|同時查(?:一下)?|查(?:一下)?)\s*", "", message)
    query = query.rstrip("？?。！! ")
    parts = re.split(r"\s*(?:以及|還有|和|與|跟|、|[，,](?:\s*(?:以及|還有))?)\s*", query)
    if len(parts) != 2:
        return None
    targets = {}
    for part in parts:
        part = re.sub(r"^(?:順便|再)?\s*(?:查(?:一下)?|告訴我)\s*", "", part.strip())
        person = FACULTY.fullmatch(part)
        unit = OFFICE.fullmatch(part) or OFFICE_ALTERNATE.fullmatch(part)
        if person and person["name"] not in {"我的", "我們的", "這位", "那位", "導師", "指導"} and "faculty" not in targets:
            targets["faculty"] = {"name": person["name"]}
            if person["department"]:
                targets["faculty"]["department"] = person["department"]
        elif unit and "office" not in targets:
            targets["office"] = {"unit": unit["unit"]}
        else:
            return None
    return targets if set(targets) == set(ROLES) else None


class ModelContractError(ValueError):
    """A model reply did not match the validated role/stage contract."""


@dataclass(frozen=True)
class ModelReply:
    value: dict
    total_tokens: int | None = None


class GeminiCampusRoles:
    """Real model calls; reuse the existing server-side Gemini transport."""

    evidence_mode = "live"
    provider = "gemini"

    def __init__(self, client=None):
        self.client = client if client is not None else GeminiEmailGenerator(timeout_seconds=8)
        self.model = self.client.model

    @property
    def configured(self):
        return self.client.configured

    def step(self, role, stage, context, choices=()):
        if role == "coordinator":
            if stage == "plan":
                instruction = (
                    "你是南大唯讀查詢召集人。依給定的兩個已擷取目標委派兩位專家，"
                    "選擇先查教師或先查系辦；若資訊不足則選 clarify。不得改寫目標或要求寄信。"
                )
            else:
                instruction = (
                    "你是南大唯讀查詢召集人。依兩位專家的實際交付決定整合順序；"
                    "有一方查不到時，將已驗證的結果放前面。兩份結果都會顯示，不得編造值或來源。"
                )
            options = {"generationConfig": {"responseFormat": {"text": {
                "mimeType": "APPLICATION_JSON",
                "schema": {
                    "type": "object", "required": ["action"],
                    "properties": {"action": {"type": "string", "enum": list(choices)}},
                },
            }}}}
        else:
            tool = TOOLS[role]
            field = "教師公開信箱" if role == "faculty" else "系辦公開電話"
            instruction = f"你是南大校務通的{LABELS[role]}。只查{field}；用指定工具，原樣傳入 target 的所有欄位，不能更換對象。"
            options = {
                "tools": [{"functionDeclarations": [{
                    "name": tool, "description": f"依官方快照查{field}，只讀不可寫入",
                    "parameters": {"type": "object", "required": list(context["target"]),
                                   "properties": {key: {"type": "string", "enum": [val]}
                                                  for key, val in context["target"].items()}},
                }]}],
                "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool]}},
            }
        result = self.client._post({
            "systemInstruction": {"parts": [{"text": instruction}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(context, ensure_ascii=False)}]}],
            **options,
        })
        try:
            candidates = result["candidates"]
            if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
                raise ModelContractError("Gemini did not complete a single response")
            parts = candidates[0]["content"]["parts"]
            if role == "coordinator":
                values = [p["text"] for p in parts if "text" in p and not p.get("thought")]
                if len(values) != 1 or any("functionCall" in p for p in parts):
                    raise ModelContractError("Invalid coordinator response")
                value = json.loads(values[0])
            else:
                values = [p["functionCall"] for p in parts if "functionCall" in p]
                if len(values) != 1:
                    raise ModelContractError("Expected one specialist tool call")
                value = {"tool": values[0]["name"], "arguments": values[0]["args"]}
            tokens = result.get("usageMetadata", {}).get("totalTokenCount")
            return ModelReply(value, tokens if type(tokens) is int and tokens >= 0 else None)
        except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise ModelContractError("Malformed Gemini reply") from exc


class CampusMAS:
    """Host owns the ledger/result board; role calls cannot change it or write."""

    def __init__(self, chunks, catalog, provider=None, *, office_supplements=None):
        self.people = tuple(dict(row) for row in chunks if row.get("recipient_kind") != "unit")
        self.units = tuple(dict(row) for row in catalog.units if row.get("category") == "academic")
        self.office_supplements = {name: dict(record) for name, record in (office_supplements or {}).items()}
        self.provider = provider if provider is not None else GeminiCampusRoles()
        encoded = json.dumps([self.people, [{**u, "aliases": sorted(u["aliases"])} for u in self.units],
                              self.office_supplements], ensure_ascii=False, sort_keys=True)
        self.source_version = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _matching_units(self, name):
        return [u for u in self.units if name == u["name"] or name in u["aliases"]]

    @staticmethod
    def _valid(row):
        return row.get("authority") == "official" and row.get("validity") == "active"

    def _faculty(self, target):
        rows = [r for r in self.people if (r.get("person_name") or r["teacher_name"]) == target["name"]]
        if "department" in target:
            departments = {u["name"] for u in self._matching_units(target["department"])}
            rows = [r for r in rows if r.get("department", "資訊工程學系") in departments]
        rows = [r for r in rows if self._valid(r) and official_url(r.get("source_url"))
                and EMAIL.fullmatch(r.get("public_email") or "")]
        if not rows:
            return {"status": "not_found"}
        if len({r["public_email"].lower() for r in rows}) != 1:
            return {"status": "ambiguous"}
        row = rows[0]
        return {"status": "found", "display_name": target["name"], "value": row["public_email"],
                "citation": {"source_id": row["source_id"], "chunk_id": row["chunk_id"],
                             "label": f'{target["name"]}－官方師資', "url": row["source_url"]}}

    def _office(self, target):
        rows = self._matching_units(target["unit"])
        if len(rows) != 1:
            return {"status": "ambiguous" if rows else "not_found"}
        unit = rows[0]
        name = unit["name"]
        url = unit.get("phone_source_url") or (unit.get("url") if unit.get("page_status") == "fetched" else None)
        candidates = [(unit, url, f'{unit["chunk_id"]}-phone')]
        supplement = self.office_supplements.get(name)
        if supplement:
            candidates.append((supplement, supplement.get("source_url"), supplement.get("source_id")))
        for row, url, source_id in candidates:
            phone = row.get("telephone") or ""
            if (not self._valid(row) or not official_url(url) or not source_id or not row.get("chunk_id")
                    or not PHONE.fullmatch(phone) or not any(c.isdigit() for c in phone)):
                continue
            extensions = [ext for ext in row.get("extensions", []) if re.fullmatch(r"[\d\s,、/–-]+", ext)]
            return {"status": "found", "display_name": name + "辦公室",
                    "value": phone + (" 轉 " + "、".join(extensions) if extensions else ""),
                    "citation": {"source_id": source_id, "chunk_id": row["chunk_id"],
                                 "label": f"{name}－官方電話", "url": url}}
        return {"status": "not_found"}

    def run(self, targets):
        started = time.monotonic()
        run_id = f"week08-{uuid.uuid4().hex}"
        version = self.source_version
        live = self.provider.evidence_mode == "live"
        ledger = {r: {"task_id": f"{run_id}:{r}", "owner": r, "status": "pending",
                      "source_version": version} for r in ROLES}
        events, board, tokens = [], {}, []
        calls, model_failure = 0, False

        def event(role, stage, status, **fields):
            events.append({"role": role, "stage": stage, "status": status, **fields})

        def step(role, stage, context, choices=()):
            nonlocal calls
            calls += 1
            event(role, stage, "started")
            reply = self.provider.step(role, stage, context, choices)
            tokens.append(reply.total_tokens)
            return reply.value

        def action(decision, choices):
            if not isinstance(decision, dict) or set(decision) != {"action"} or decision["action"] not in choices:
                raise ModelContractError("Invalid coordinator action")
            return decision["action"]

        def finish(status, answer, reason, *, citations=()):
            for task in ledger.values():
                if task["status"] == "pending":
                    task["status"] = "not_dispatched"
            return {"status": status, "answer": answer, "citations": list(citations),
                    "retrieval": None, "used_context": False, "detected_teacher": None,
                    "suggested_action": None, "error": None if status == "ok" else {"code": reason},
                    "workflow": {"name": "mas", "run_id": run_id, "source_version": version,
                                 "provider": self.provider.provider, "model": self.provider.model,
                                 "status": status, "stop_reason": reason,
                                 "live_status": ("LIVE_FAIL" if model_failure else "LIVE_PASS") if live and calls else "LIVE_NOT_RUN",
                                 "evidence_mode": "live" if live else "test_provider",
                                 "elapsed_ms": round((time.monotonic() - started) * 1000),
                                 "model_calls": calls, "live_calls": calls if live else 0,
                                 "total_tokens": sum(tokens) if live and len(tokens) == calls and calls and None not in tokens else None,
                                 "final_role": "coordinator" if any(
                                     e["stage"] == "integrate" and e["status"] == "accepted" for e in events
                                 ) else "host",
                                 "ledger": list(ledger.values()), "result_board": list(board.values()),
                                 "stages": events, "access": "READ_ONLY", "write_actions": 0}}

        if not self.provider.configured:
            event("coordinator", "configuration", "stopped")
            return finish("provider_failed", "中央分工尚未設定模型，沒有執行查詢。", "MAS_NOT_CONFIGURED")
        try:
            orders = ("faculty_first", "office_first")
            plan_options = (*orders, "clarify")
            plan = action(step("coordinator", "plan", {"targets": targets}, plan_options), plan_options)
            event("coordinator", "plan", "accepted", code=plan)
        except (GeminiEmailError, ModelContractError, TimeoutError):
            model_failure = True
            event("coordinator", "plan", "failed")
            return finish("provider_failed", "召集人無法完成派工，沒有執行查詢。", "MAS_PLAN_FAILED")
        if plan == "clarify":
            return finish("needs_clarification", "請提供教師全名和明確的系所，例如「許世昌老師的信箱和電機系辦電話」。", "MAS_NEEDS_TARGETS")

        for role in ROLES if plan == "faculty_first" else reversed(ROLES):
            task = ledger[role]
            task["status"] = "in_progress"
            event("coordinator", "delegate", "accepted", owner=role, task_id=task["task_id"])
            try:
                target = targets[role]
                proposal = step(role, "tool_proposal", {"target": dict(target), "access": "READ_ONLY"})
                if proposal != {"tool": TOOLS[role], "arguments": target}:
                    raise ModelContractError("Tool or target outside assignment")
                event(role, "tool_gate", "passed", tool=TOOLS[role], task_id=task["task_id"])
                result = self._faculty(target) if role == "faculty" else self._office(target)
                event(role, "tool_result", result["status"], tool=TOOLS[role], task_id=task["task_id"],
                      source_id=result.get("citation", {}).get("source_id"))
            except (GeminiEmailError, ModelContractError, TimeoutError) as exc:
                model_failure = True
                result = {"status": "blocked" if isinstance(exc, ModelContractError) else "provider_failed"}
                event(role, "tool_gate", result["status"], task_id=task["task_id"])
            task["status"] = result["status"]
            board[role] = {**result, "task_id": task["task_id"], "from": role, "to": "coordinator",
                           "source_version": version}
            event(role, "deliver", result["status"], task_id=task["task_id"], to="coordinator")

        found = sum(row["status"] == "found" for row in board.values())
        ordered = ROLES
        if found:
            verified = {role: {key: row[key] for key in ("status", "value", "citation", "source_version")
                               if key in row} for role, row in board.items()}
            try:
                order = action(step("coordinator", "integrate", {"results": verified}, orders), orders)
                ordered = ROLES if order == "faculty_first" else tuple(reversed(ROLES))
                event("coordinator", "integrate", "accepted", code=order)
            except (GeminiEmailError, ModelContractError, TimeoutError):
                model_failure = True
                event("coordinator", "integrate", "failed")
        else:
            event("coordinator", "integrate", "stopped", code="NO_VERIFIED_RESULT")

        captions = {"faculty": f'{targets["faculty"]["name"]}老師信箱', "office": f'{targets["office"]["unit"]}辦電話'}
        messages = {"not_found": "目前官方快照未收錄這筆資料。",
                    "ambiguous": "有多筆相符資料，請補充教師所屬系或確認單位全名。",
                    "blocked": "專家提出的工具或查詢對象未通過驗證。",
                    "provider_failed": "專家未能完成查詢，請稍後再試。"}
        lines, citations = [], []
        for role in ordered:
            row = board[role]
            if row["status"] == "found":
                label = captions[role] if role == "faculty" else f'{row["display_name"]}電話'
                lines.append(f'{label}：{row["value"]}')
                citations.append(row["citation"])
            else:
                lines.append(f'{captions[role]}：{messages[row["status"]]}')
        if found == 2 and not model_failure:
            return finish("ok", "\n".join(lines), "BOTH_VERIFIED", citations=citations)
        if found:
            if events[-1]["stage"] == "integrate" and events[-1]["status"] == "failed":
                lines.append("模型整合失敗；上述查證結果由系統直接列出。")
            return finish("partial", "\n".join(lines), "MAS_PARTIAL", citations=citations)
        return finish("insufficient_evidence", "\n".join(lines), "MAS_NO_VERIFIED_RESULT")
