"""Bounded JSON contracts and evidence validation; no third-party runtime."""

# 保留原有导入路径，供外部调用者使用；提示词本身集中维护。
from .prompts import INSTRUCTIONS as INSTRUCTIONS


class ContractError(ValueError):
    """输出结构、来源引用或阶段语义约束不符合约定。"""

    pass


SCHEMAS = {
    "respond": {"intent": "chat", "reason": "", "reply": ""},
    "route": {"intent": "chat", "reason": ""},
    "academic_query": {"query": ""},
    "chat": {"reply": ""},
    "knowledge": {
        "answer": "",
        "sources": [{"title": "", "source": "", "snippet": ""}],
    },
    "extract": {
        "facts": [{"quote": ""}],
        "feelings": [""],
        "assumptions": [""],
        "unknowns": [""],
        "questions": [""],
    },
    "hypotheses": {
        "items": [
            {
                "id": "",
                "explanation": "",
                "status": "unknown",
                "support": [""],
                "limitations": [""],
                "check": "",
            }
        ]
    },
    "actions": {
        "items": [
            {
                "id": "",
                "name": "",
                "benefit": "",
                "risk": "",
                "condition": "",
                "first_step": "",
            }
        ],
        "recommended_id": "",
        "reason": "",
    },
    "dialogue": {
        "action_id": "",
        "opening": "",
        "branches": [{"imagined_reply": "", "practice_reply": ""}],
        "boundaries": [""],
    },
    "feedback": {
        "updates": [
            {"hypothesis_id": "", "status": "unknown", "reason": "", "quote": ""}
        ],
        "reflection": "",
        "next_steps": [""],
    },
}


def validate(value, example, path="result"):
    """递归检查模板字段、字符串长度和数组长度，拒绝多余字段。"""
    if isinstance(example, dict):
        if not isinstance(value, dict) or set(value) != set(example):
            raise ContractError(f"{path}: expected fields {', '.join(example)}")
        for key, child in example.items():
            validate(value[key], child, f"{path}.{key}")
    elif isinstance(example, list):
        if not isinstance(value, list) or len(value) > 12:
            raise ContractError(f"{path}: expected list of at most 12 items")
        for i, child in enumerate(value):
            validate(child, example[0], f"{path}[{i}]")
    elif not isinstance(value, str) or len(value) > 3000:
        raise ContractError(f"{path}: expected text up to 3000 characters")


def validate_stage(stage, value, payload):
    """在结构校验后检查引用、假设状态和演练关系，成功返回原对象。"""
    import json

    if len(json.dumps(value, ensure_ascii=False)) > 16000:
        raise ContractError("Stage output exceeded 16000 characters")
    validate(value, SCHEMAS[stage])
    if stage in {"route", "respond"}:
        if value["intent"] not in {
            "chat",
            "knowledge",
            "analysis",
            "rehearsal",
            "feedback",
            "evidence",
        }:
            raise ContractError("Unknown intent")
        if value["intent"] == "feedback" and not payload.get("has_report"):
            raise ContractError("Feedback requires an existing analysis")
    if stage == "respond":
        if value["intent"] == "chat" and not value["reply"].strip():
            raise ContractError("Direct conversation requires a reply")
        if value["intent"] != "chat" and value["reply"].strip():
            raise ContractError("Specialized workflows must not pre-generate an answer")
    if stage == "academic_query":
        import re

        query = value["query"]
        if len(query) > 200 or re.search(r"https?://|@|\d{7,}|[\r\n]", query):
            raise ContractError(
                "Academic query must be short and omit contact details and URLs"
            )
    if stage == "chat" and not value["reply"].strip():
        raise ContractError("Chat reply must not be empty")
    if stage == "knowledge":
        if not value["answer"].strip():
            raise ContractError("Knowledge answer must not be empty")
        supplied = payload.get("retrieved", [])
        for citation in value["sources"]:
            if not any(
                citation["title"] == doc["title"]
                and citation["source"] == doc["source"]
                and citation["snippet"].strip()
                and citation["snippet"] in doc["text"]
                for doc in supplied
            ):
                raise ContractError(
                    "Knowledge citations must quote retrieved documents"
                )
        if supplied and not value["sources"]:
            raise ContractError("Retrieved knowledge requires a citation")
    if stage == "extract":
        if len(value["questions"]) > 3:
            raise ContractError("At most three questions")
        for fact in value["facts"]:
            if (
                not fact["quote"].strip()
                or fact["quote"] not in payload["user_message"]
            ):
                raise ContractError("Facts must quote the user's original text exactly")
    if stage in {"hypotheses", "actions"}:
        items = value["items"]
        ids = [x["id"] for x in items]
        if (
            not (0 if stage == "hypotheses" else 2) <= len(items) <= 4
            or len(set(ids)) != len(ids)
            or any(not x for x in ids)
        ):
            raise ContractError(
                "Expected up to 4 unique hypothesis IDs, or 2-4 unique action IDs"
            )
        if stage == "hypotheses":
            sources = [payload.get("user_message", "")] + [
                x.get("text", "") for x in payload.get("observations", [])
            ]
            sources += [
                x.get("content", "")
                for x in payload.get("user_history", [])
                if x.get("role") == "user"
            ]
            previous = {
                x["id"]: x for x in payload.get("previous_judgments_unverified", [])
            }
            for item in items:
                if any(
                    quote and not any(quote in source for source in sources)
                    for quote in item["support"]
                ):
                    raise ContractError(
                        "Hypothesis support must quote a user account exactly"
                    )
                prior = previous.get(item["id"], {})
                retained = (
                    prior.get("explanation") == item["explanation"]
                    and prior.get("status") == item["status"]
                )
                if item["status"] not in {"unknown", "possible"} and not retained:
                    raise ContractError(
                        "Supported judgments must retain a previous evidence-backed revision"
                    )
            for prior in previous.values():
                if prior["status"] in {"supported", "weakened"} and not any(
                    x["id"] == prior["id"]
                    and x["explanation"] == prior["explanation"]
                    and x["status"] == prior["status"]
                    for x in items
                ):
                    raise ContractError(
                        "Do not silently discard prior feedback; use the feedback workflow to revise it"
                    )
        if stage == "actions" and value["recommended_id"] not in ids:
            raise ContractError("Recommended action must exist")
    if stage == "dialogue" and value["action_id"] != payload["selected_action"]["id"]:
        raise ContractError("Rehearsal must match the selected action")
    if stage == "feedback":
        known = {x["id"] for x in payload["hypotheses"]}
        seen = set()
        for item in value["updates"]:
            if item["hypothesis_id"] not in known or item["hypothesis_id"] in seen:
                raise ContractError("Unknown or duplicate hypothesis ID")
            seen.add(item["hypothesis_id"])
            if item["status"] not in {"unknown", "possible", "supported", "weakened"}:
                raise ContractError("Invalid hypothesis status")
            if not item["quote"].strip() or item["quote"] not in payload["feedback"]:
                raise ContractError("Feedback updates need an exact feedback quote")
    return value
