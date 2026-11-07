"""A bounded, checkpointed harness workflow, not an unconstrained autonomous agent.

阅读顺序：execute 载入任务 → stage 复用检查点或调用模型 → 按 kind 分支
组织结果 → store.finish 提交。state 是可持久化的中间状态，result 是最终产物。
普通聊天走 respond，只有明确的复杂需求才继续执行专门阶段。
"""

from __future__ import annotations
import time

from .schemas import validate_stage, SCHEMAS, ContractError
from .protocols import WorkflowStore
from .knowledge import search as search_knowledge
from .academic import AcademicSearch


class RelationshipAgent:
    """根据需求选择有界工作流，并逐阶段校验和持久化结果。"""

    def __init__(self, client, store: WorkflowStore, academic=None):
        """注入模型、存储协议和可选学术搜索，便于离线测试。"""
        self.client, self.store = client, store
        self.academic = academic or AcademicSearch()

    def config(self):
        """返回可持久化的模型身份配置，不包含 API Key。"""
        return {
            "mode": self.client.mode,
            "model": self.client.model,
            "protocol": self.client.protocol,
            "base_url": self.client.base_url,
        }

    def execute(self, run_id):
        """恢复或执行任务，成功提交结果，失败保留可恢复的检查点。

        普通对话合并意图判断和回答；特殊流程按需执行。已经保存的阶段
        不会重复调用模型，但远端请求成功、本地未落盘的窗口仍可能重计费。"""
        run = self.store.run(run_id)
        if run["status"] == "complete":
            return run["result"]
        if run["config"] != self.config():
            raise ValueError(
                "恢复时请使用相同模型配置；Key 可以更换 / Resume needs the same model configuration"
            )
        # state 随检查点持久化；重启后以阶段名称为键找到已经完成的结果。
        state, message = run["state"], run["input"]
        common = {"language": state["language"]}

        def stage(name, payload):
            """复用已保存阶段，否则调用、校验并保存结果后记录耗时。"""
            # 普通聊天的 respond 已填入 chat；这个命中可避免额外一次 HTTP 调用。
            if name in state:
                return state[name]
            self.store.trace(run_id, "stage_started", {"stage": name})
            started = time.monotonic()
            payload = {**common, **payload}
            value = validate_stage(
                name, self.client.complete_json(name, payload, SCHEMAS[name]), payload
            )
            state[name] = value
            self.store.checkpoint(run_id, state)
            self.store.trace(
                run_id,
                "stage_completed",
                {
                    "stage": name,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "usage": getattr(self.client, "last_usage", {}),
                },
            )
            return value

        try:
            if run["kind"] == "auto":
                previous = self.store.latest_report(run["session_id"])
                # Answer routine requests in this same call. Keep a separate routing
                # record for traces/compatibility without paying for a second model call.
                decision = stage(
                    "respond",
                    {
                        "user_message": message,
                        "conversation": state["conversation"],
                        "observations": state["context"],
                        "has_report": bool(previous),
                    },
                )
                routing = {key: decision[key] for key in ("intent", "reason")}
                state["route"] = routing
                if routing["intent"] == "chat":
                    state.setdefault("chat", {"reply": decision["reply"]})
                # 改变的是本次执行分支；持久化中间结果仍通过 checkpoint 保存。
                run["kind"] = routing["intent"]
                if run["kind"] == "feedback":
                    state["hypotheses"] = previous["current_hypotheses"]
                elif run["kind"] == "knowledge":
                    state["context"] = []
                    if "retrieved" not in state:
                        state["retrieved"] = search_knowledge(
                            message, language=state["language"]
                        )
                        # “这个概念呢”这类追问先用当前问题检索；无命中才借用最近用户原话。
                        if not state["retrieved"] and any(
                            x in message.lower()
                            for x in ("它", "这个", "这种", "that", "this", "it ")
                        ):
                            query = " ".join(
                                x["content"]
                                for x in state["conversation"][-4:]
                                if x["role"] == "user"
                            )
                            state["retrieved"] = search_knowledge(
                                query, language=state["language"]
                            )
                        self.store.trace(
                            run_id,
                            "knowledge_retrieved",
                            {"document_ids": [d["id"] for d in state["retrieved"]]},
                        )
                self.store.checkpoint(run_id, state)
            if run["kind"] == "evidence":
                state["context"] = []
                query = stage(
                    "academic_query",
                    {
                        "user_message": message,
                        "conversation": state.get("conversation", []),
                    },
                )["query"]
                if query and "academic_search" not in state:
                    self.store.trace(run_id, "academic_search_started")
                    state["academic_search"] = self.academic.search(
                        query, enabled=self.client.mode == "api"
                    )
                    self.store.checkpoint(run_id, state)
                    self.store.trace(
                        run_id,
                        "academic_search_completed",
                        {
                            "providers": state["academic_search"]["providers"],
                            "cached": state["academic_search"]["cached"],
                        },
                    )
                search = state.get(
                    "academic_search", {"query": "", "documents": [], "providers": []}
                )
                # 只有标题/作者的记录不足以支撑知识回答，不让模型凭元数据编结论。
                evidence = [
                    d
                    for d in search["documents"]
                    if d["text"] and d["evidence_level"] != "metadata_only"
                ]
                if evidence:
                    response = stage(
                        "knowledge",
                        {
                            "user_message": message,
                            "retrieved": evidence,
                            "conversation": [],
                            "search_status": search["providers"],
                            "academic": True,
                        },
                    )
                    # URL、作者等元数据来自检索记录，不使用模型自行编写的链接。
                    citations = []
                    for source in response["sources"]:
                        doc = next(
                            d
                            for d in evidence
                            if d["title"] == source["title"]
                            and d["source"] == source["source"]
                            and source["snippet"] in d["text"]
                        )
                        citations.append(
                            {
                                **source,
                                **{
                                    k: doc[k]
                                    for k in (
                                        "url",
                                        "year",
                                        "authors",
                                        "doi",
                                        "evidence_level",
                                        "found_in",
                                    )
                                },
                            }
                        )
                    answer = response["answer"]
                else:
                    en = state["language"] == "en"
                    answer = (
                        (
                            "Which topic would you like research sources for?"
                            if en
                            else "你希望查哪一个观点或主题的研究来源？"
                        )
                        if not query
                        else (
                            "No usable research text was retrieved. See each provider’s status below; this is not evidence that no research exists."
                            if en
                            else "这次没有取得可用于回答的研究文本。下方显示各平台的检索状态；这不代表相关研究不存在。"
                        )
                    )
                    citations = []
                result = {
                    "kind": "evidence",
                    "answer": answer,
                    "sources": citations,
                    "academic_search": search,
                }
                records = [
                    (
                        "observation",
                        "knowledge_input",
                        {"text": message, "source": "user_report_unverified"},
                    ),
                    (
                        "inference",
                        "academic_answer",
                        {"answer": answer, "sources": citations},
                    ),
                ]
            elif run["kind"] == "knowledge":
                response = stage(
                    "knowledge",
                    {
                        "user_message": message,
                        "retrieved": state["retrieved"],
                        "conversation": state.get("conversation", []),
                    },
                )
                result = {
                    "kind": "knowledge",
                    "answer": response["answer"],
                    "sources": response["sources"],
                    "retrieved": state["retrieved"],
                }
                records = [
                    (
                        "observation",
                        "knowledge_input",
                        {"text": message, "source": "user_report_unverified"},
                    ),
                    ("inference", "knowledge_answer", response),
                ]
            elif run["kind"] == "chat":
                prior_replies = [
                    x["content"]
                    for x in state["conversation"]
                    if x["role"] == "assistant"
                ][-3:]
                chat_payload = {
                    "user_message": message,
                    "conversation": state["conversation"],
                    "observations": state["context"],
                    "previous_assistant_replies": prior_replies,
                    "avoid_exact_replies": prior_replies,
                }
                response = stage("chat", chat_payload)

                # Exact duplication is observable. Empathy quality is evaluated using
                # scenarios, not a keyword test that overwrites valid advice with a script.
                def repeated(value):
                    return value["reply"].strip() in {x.strip() for x in prior_replies}

                # 只修复完全重复的回复，不凭“安慰关键词”覆盖正常答案。
                if repeated(response):
                    state.pop("chat", None)
                    chat_payload["repair"] = (
                        "Your answer exactly repeats an earlier reply. Answer the latest "
                        "request using the new information, with the requested advice or "
                        "draft. Do not replace it with a generic reassurance or question."
                    )
                    response = stage("chat", chat_payload)
                    if repeated(response):
                        raise ContractError(
                            "模型重复了上一条回复，请重试 / Model repeated its previous reply; please retry"
                        )
                result = {"kind": "chat", "reply": response["reply"]}
                records = [
                    (
                        "observation",
                        "chat_input",
                        {"text": message, "source": "user_report_unverified"},
                    ),
                    ("inference", "assistant_reply", response),
                ]
            elif run["kind"] == "feedback":
                update = stage(
                    "feedback",
                    {
                        "feedback": message,
                        "hypotheses": state["hypotheses"],
                        "observations": state["context"],
                    },
                )
                # 复制旧判断再更新，保留原检查点；last_update 记录此次修正的来源。
                hypotheses = [dict(x) for x in state["hypotheses"]]
                for change in update["updates"]:
                    for h in hypotheses:
                        if h["id"] == change["hypothesis_id"]:
                            h.update(
                                status=change["status"],
                                last_update={
                                    "reason": change["reason"],
                                    "quote": change["quote"],
                                    "run_id": run_id,
                                },
                            )
                result = {
                    "kind": "feedback",
                    "update": update,
                    "current_hypotheses": hypotheses,
                }
                records = [
                    (
                        "observation",
                        "feedback",
                        {"text": message, "source": "user_report_unverified"},
                    ),
                    (
                        "inference",
                        "hypothesis_revision",
                        {"hypotheses": hypotheses, "update": update},
                    ),
                ]
            else:
                # Earlier user reports were previously dropped by the report stages.
                # Pass only real user turns, never generated analysis as evidence.
                user_history = [
                    turn
                    for turn in state.get("conversation", [])
                    if turn.get("role") == "user"
                ]
                evidence = stage("extract", {"user_message": message})
                hypotheses = stage(
                    "hypotheses",
                    {
                        "user_message": message,
                        "evidence": evidence,
                        "observations": state["context"],
                        "user_history": user_history,
                        "previous_judgments_unverified": state.get(
                            "previous_judgments", []
                        ),
                    },
                )
                current_hypotheses = [dict(x) for x in hypotheses["items"]]
                for item in current_hypotheses:
                    for prior in state.get("previous_judgments", []):
                        if (
                            item["id"] == prior["id"]
                            and item["explanation"] == prior["explanation"]
                            and "last_update" in prior
                        ):
                            item["last_update"] = prior["last_update"]
                actions = stage(
                    "actions",
                    {
                        "user_message": message,
                        "evidence": evidence,
                        "hypotheses": hypotheses,
                        "user_history": user_history,
                        "observations": state["context"],
                    },
                )
                # Analysis is not consent to simulate another person's response.
                # The semantic router selects rehearsal only on an explicit request.
                # 给一句回复建议仍属于聊天；只有 rehearsal 分支会生成假想对方回复。
                dialogue = None
                if run["kind"] == "rehearsal":
                    selected = next(
                        x
                        for x in actions["items"]
                        if x["id"] == actions["recommended_id"]
                    )
                    dialogue = stage(
                        "dialogue",
                        {
                            "user_message": message,
                            "evidence": evidence,
                            "selected_action": selected,
                            "user_history": user_history,
                        },
                    )
                result = {
                    "kind": "analysis",
                    "evidence": evidence,
                    "hypotheses": hypotheses,
                    "actions": actions,
                    "dialogue_rehearsal": dialogue,
                    "current_hypotheses": current_hypotheses,
                }
                records = [
                    (
                        "observation",
                        "input",
                        {"text": message, "source": "user_report_unverified"},
                    ),
                    (
                        "inference",
                        "analysis",
                        {
                            "evidence": evidence,
                            "hypotheses": hypotheses,
                            "actions": actions,
                        },
                    ),
                ]
                if dialogue is not None:
                    records.append(("simulation", "rehearsal", dialogue))
            # 各分支统一补元数据，前端通过 kind 区分展示；服务层再过滤内部字段。
            result.update(
                run_id=run_id,
                session_id=run["session_id"],
                input=message,
                mode=self.client.mode,
                model=self.client.model,
                language=state["language"],
                memory_used=state["context"],
                is_demo=self.client.mode == "mock",
            )
            if "route" in state:
                result["routing"] = state["route"]
            # 最终回答和分层记忆在一个事务内提交，避免只保存一半的状态。
            self.store.finish(run_id, result, records)
            self.store.trace(run_id, "committed", {"layers": [x[0] for x in records]})
            return result
        except Exception as exc:
            # Do not persist provider error bodies, credentials, or a traceback.
            error = (
                str(exc)
                if type(exc).__name__ in {"ModelError", "ContractError"}
                else "Execution failed; retry the run or inspect local tests"
            )
            self.store.fail(run_id, error)
            self.store.trace(run_id, "failed", {"error": error})
            raise RuntimeError(error) from None
