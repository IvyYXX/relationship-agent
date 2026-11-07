"""Durable job claims, bounded model spending, and person-aware workflow execution.

submit 冻结上下文并入队；后台 start → claim → execute；失败由 resume
显式恢复。Service 不保存 API Key 到任务，恢复时可换 Key，但模型身份和
提示词版本必须一致。安全与人物澄清可以直接回应而不调用模型。
"""

import os
import re
import threading
import logging
from datetime import datetime, timezone
from sqlalchemy import select, insert, update
from .database import runs, quota, attempts, feedback, ident, stamp
from .store import ScopedStore, Busy, Missing
from ..engine import RelationshipAgent
from ..providers import build_model_client, ModelError
from ..demo import MockModelClient
from ..academic import AcademicSearch

# 恢复资格的一部分：改回答语义时升级版本，纯注释/格式变动无需升级。
PROMPT_VERSION = "persona-4-conversation-first"


class PrivateSearch(AcademicSearch):
    """在学术查询外发前剔除已登记人物名及明显联系方式。"""

    def __init__(self, store):
        """收集当前账号昵称与别名，供确定性的第二道去标识化使用。"""
        super().__init__()
        self.names = [
            name.strip()
            for p in store.people()
            for name in [
                p["profile"]["nickname"],
                *re.split(r"[,，、\n]", p["profile"].get("aliases", "")),
            ]
            if name.strip()
        ]

    def search(self, query, enabled=True):
        # A second, deterministic boundary after the model's de-identification instruction.
        """清理查询后调用有界搜索；清理后为空则不发起网络请求。"""
        for name in sorted(self.names, key=len, reverse=True):
            pattern = re.escape(name)
            if name.isascii():
                pattern = r"(?<!\w)" + pattern + r"(?!\w)"
            query = re.sub(pattern, "", query, flags=re.I)
        query = re.sub(r"https?://\S+|\S+@\S+|\d{5,}", "", query)
        query = " ".join(query.split())[:200]
        return (
            super().search(query, enabled=enabled)
            if query
            else {"query": "", "documents": [], "providers": [], "cached": False}
        )


def safety_response(message, language):
    """对有限的危机或骚扰表达直接回应；不是全面语义安全分类器。"""
    m = message.lower()
    danger = any(
        x in m
        for x in (
            "想自杀",
            "准备自杀",
            "想死",
            "不想活了",
            "伤害自己",
            "威胁要打",
            "威胁打我",
            "正在打我",
            "kill myself",
            "end my life",
            "hurt myself",
            "threatened to hit",
            "threatens to hurt",
        )
    )
    if danger:
        return (
            "I hear how painful or frightening this feels. Are you in immediate danger right now? If you may hurt yourself or someone is threatening you, move to a safer place if you can, contact local emergency services, and reach a trusted person who can stay with you. We can focus on getting you through this moment."
            if language == "en"
            else "听起来你现在承受着很大的痛苦或恐惧。你此刻是否安全、有没有马上受伤的危险？如果你可能伤害自己，或有人正在威胁你，请尽可能去安全的地方，联系当地紧急救援，并找一个信任的人陪着你。我们可以先一起把眼前这一刻撑过去。"
        )
    harassment = any(
        x in m
        for x in (
            "绕过拉黑",
            "换号骚扰",
            "偷偷定位",
            "跟踪他",
            "跟踪她",
            "bypass their block",
            "secretly track",
        )
    )
    if harassment:
        return (
            "It sounds difficult to sit with this uncertainty. I cannot help bypass someone’s boundaries or secretly track them. We can work on a message you do not send, or a plan for managing the urge to contact them."
            if language == "en"
            else "这种不确定感可能很难熬。我不能帮助绕过拉黑、秘密定位或跟踪别人。我们可以先整理一段不发送的话，或一起想想如何度过特别想联系对方的时刻。"
        )
    return None


class PersonaDemo(MockModelClient):
    """面向网页的离线记忆示例，不代表真实模型的回复质量。"""

    def complete_json(self, stage, payload, schema=None):
        """复用工作流样例，并让普通回复展示已确认人物记忆。"""
        if stage == "respond":
            decision = super().complete_json(stage, payload, schema)
            if decision["intent"] == "chat":
                decision["reply"] = PersonaDemo.complete_json(self, "chat", payload)[
                    "reply"
                ]
            return decision
        if stage == "chat":
            en = payload.get("language") == "en"
            records = [
                r for r in payload.get("observations", []) if r.get("kind") == "account"
            ]
            message = payload.get("user_message", "")
            if records:
                fact = records[0]["text"][:180]
                return {
                    "reply": (
                        f"You previously recorded: “{fact}”. That is your account, and it does not tell us their motives. What feels most important about today’s situation?"
                        if en
                        else f"你之前确认记录过：“{fact}”。这能帮助我们理解背景，但还不能说明对方的动机。关于这次的情况，你最想让我听见的是什么？"
                    )
                }
            return {
                "reply": (
                    f"I hear that “{message[:100]}” matters to you. We can take it slowly. What would you most like to talk through?"
                    if en
                    else f"听到你说“{message[:100]}”，我想先了解这件事对你的影响。可以慢慢说，你现在最想聊的是哪一部分？"
                )
            }
        return super().complete_json(stage, payload, schema)


class Service:
    """协调持久任务队列、每请求预算与有权限的人物上下文。"""

    def __init__(self, db, settings, client_factory=None):
        """保存依赖并准备线程状态；创建对象时不启动后台任务。"""
        self.db, self.settings = db, settings
        self.client_factory = client_factory
        self.stop_event = threading.Event()
        self.threads = []

    def client(self):
        """创建当前模式的客户端；API 失败不会自动降级成 mock。"""
        if self.client_factory:
            return self.client_factory()
        if self.settings.mode == "mock":
            return PersonaDemo()
        return build_model_client(
            "api", api_key=os.getenv("PERSONA_API_KEY") or os.getenv("GLM_API_KEY")
        )

    def submit(self, user_id, sid, message, person_id=None, idempotency_key=None):
        """解析人物与安全预检，冻结上下文后幂等入队，返回 run_id。"""
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 6000:
            raise ValueError("消息需1–6000字 / Invalid message length")
        store = ScopedStore(self.db, user_id, idempotency_key=idempotency_key)
        session = store.session(sid)
        if idempotency_key:
            with self.db.read() as c:
                existing = (
                    c.execute(
                        select(runs).where(
                            runs.c.user_id == user_id,
                            runs.c.idempotency_key == idempotency_key,
                        )
                    )
                    .mappings()
                    .first()
                )
            if existing:
                # A retry must refer to the same message AND selected person.
                # Older saved jobs have no selected_person_id field; preserve their replay behavior.
                if (
                    existing["input"] != message.strip()
                    or existing["session_id"] != sid
                    or existing["state"].get("selected_person_id", person_id)
                    != person_id
                ):
                    raise ValueError("Idempotency key conflict")
                return existing["id"]
        with self.db.read() as c:
            context_epoch = store.epoch(c)
        scope, choices = store.resolve(message, person_id)
        named_scope, all_choices = store.resolve(message, None)
        mentioned_ids = named_scope or [p["id"] for p in all_choices]
        # Never use hidden session defaults after a user clears the visible person tag.
        store.person_ids = scope
        language = session["language"]
        client = self.client()
        agent = RelationshipAgent(client, store)
        # 这是入队时冻结的快照；worker 不重新读取不断变化的人物资料来执行同一任务。
        state = {
            "language": language,
            "context": store.context(sid, query=message),
            "conversation": store.conversation(sid),
            "prompt_version": PROMPT_VERSION,
            "multi_person": len(all_choices) > 1,
            "context_epoch": context_epoch,
            # mentioned_person_ids 用于后续删除依赖；selected_person_id 用于重试一致性校验。
            "mentioned_person_ids": mentioned_ids,
            "selected_person_id": person_id,
        }
        # 危机预检优先于人物澄清，避免迫切求助被“你说的是谁”挡住。
        safe = safety_response(message, language)
        if safe:
            state["direct_reply"] = safe
            state["direct_kind"] = "safety"
            state["context"] = []
            state["conversation"] = []
        elif choices:
            names = " / ".join(p["profile"]["nickname"] for p in choices)
            state["direct_reply"] = (
                f"为了准确理解你说的是谁，这次想先聊哪一位（{names}）？请在输入框上方选择人物，我再继续。"
                if language == "zh"
                else f"To make sure I understand who you mean, who should we focus on ({names})? Choose a person above the message box."
            )
            state["direct_kind"] = "clarification"
            state["choices"] = [
                {"id": p["id"], "nickname": p["profile"]["nickname"]} for p in choices
            ]
            state["context"] = []
            state["conversation"] = []
        previous = store.latest_report(sid)
        if previous:
            state["previous_judgments"] = previous["current_hypotheses"]
        return store.create_run(sid, "auto", message.strip(), agent.config(), state)

    def claim(self):
        """原子领取最早排队任务；过期租约变为需手动恢复的失败任务。"""
        with self.db.tx() as c:
            # Expired leases become explicitly resumable failures, not automatically billed retries.
            c.execute(
                update(runs)
                .where(runs.c.status == "running", runs.c.lease_until < stamp())
                .values(
                    status="failed",
                    error="执行中断，可恢复 / Interrupted; resume available",
                    worker=None,
                    finished=stamp(),
                )
            )
            row = (
                c.execute(
                    select(runs)
                    .where(runs.c.status == "queued")
                    .order_by(runs.c.created)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            # 每次领取使用新的 worker token；旧线程即使晚返回，也不能覆盖新任务结果。
            worker = ident()
            c.execute(
                update(runs)
                .where(runs.c.id == row["id"], runs.c.status == "queued")
                .values(status="running", worker=worker, lease_until=stamp() + 300)
            )
            return {**dict(row), "worker": worker}

    def reserve(self, store, rid, stage):
        """在每次实际 HTTP 请求前原子预留用户和全站调用/费用预算。

        失败请求也保留预约额；此限制不等价于模型服务商的账单上限。"""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.db.tx() as c:
            store.valid_job(c, rid)
            c.execute(
                update(runs).where(runs.c.id == rid).values(lease_until=stamp() + 300)
            )
            # 两级额度在同一事务更新；账号超限时，全站的本次扣减也会回滚。
            for key, cap in [
                (f"model:{day}:global", self.settings.global_daily_calls),
                (f"model:{day}:{store.user_id}", self.settings.user_daily_calls),
            ]:
                row = (
                    c.execute(select(quota).where(quota.c.key == key))
                    .mappings()
                    .first()
                )
                calls = row["calls"] if row else 0
                money = row["reserved_usd"] if row else 0
                if calls >= cap or (
                    key.endswith(":global")
                    and money + self.settings.request_reserve_usd
                    > self.settings.daily_budget_usd + 1e-9
                ):
                    raise ModelError(
                        "今日模型预算已用完，请明天再试 / Daily model budget reached"
                    )
                values = {
                    "calls": calls + 1,
                    "reserved_usd": money + self.settings.request_reserve_usd,
                }
                if row:
                    c.execute(update(quota).where(quota.c.key == key).values(**values))
                else:
                    c.execute(insert(quota).values(key=key, **values))
            ticket = ident()
            c.execute(
                insert(attempts).values(
                    id=ticket,
                    user_id=store.user_id,
                    run_id=rid,
                    stage=stage,
                    created=stamp(),
                    usage={},
                    estimated_usd=None,
                )
            )
            return ticket

    def usage(self, store, ticket, usage):
        """按配置价格保存供应商报告的 token 用量；未知费用保留为 None。"""
        safe = {
            k: v
            for k, v in usage.items()
            if k
            in (
                "prompt_tokens",
                "completion_tokens",
                "input_tokens",
                "output_tokens",
                "total_tokens",
            )
            and isinstance(v, int)
            and v >= 0
        }
        # 两种协议的 token 字段名不同；统一到配置价格，但未知费用不会填成零。
        amount = (
            safe.get("prompt_tokens", safe.get("input_tokens", 0))
            * self.settings.input_usd_per_million
            + safe.get("completion_tokens", safe.get("output_tokens", 0))
            * self.settings.output_usd_per_million
        ) / 1e6
        with self.db.tx() as c:
            priced = (
                self.settings.input_usd_per_million > 0
                and self.settings.output_usd_per_million > 0
            )
            c.execute(
                update(attempts)
                .where(attempts.c.id == ticket, attempts.c.user_id == store.user_id)
                .values(usage=safe, estimated_usd=amount if safe and priced else None)
            )

    def execute(self, job):
        """绑定租约与逐请求计量钩子，执行任务或安全/澄清直接回应。"""
        store = ScopedStore(
            self.db, job["user_id"], job["person_ids"], worker=job["worker"]
        )
        try:
            with self.db.tx() as c:
                store.valid_job(c, job["id"])
            client = self.client()
            if hasattr(client, "before_request"):
                client.before_request = lambda stage: self.reserve(
                    store, job["id"], stage
                )
                client.after_request = lambda ticket, usage: self.usage(
                    store, ticket, usage
                )
            run = store.run(job["id"])
            if run["state"].get("direct_reply"):
                store.finish(
                    run["id"],
                    {
                        "kind": run["state"]["direct_kind"],
                        "reply": run["state"]["direct_reply"],
                        "choices": run["state"].get("choices", []),
                        "is_demo": client.mode == "mock",
                    },
                    [],
                )
            else:
                # Safety prompts remain active beyond the conservative preflight keyword rules.
                RelationshipAgent(client, store, PrivateSearch(store)).execute(
                    run["id"]
                )
        except Exception:
            try:
                current = store.run(job["id"])
                if current["status"] != "failed":
                    store.fail(
                        job["id"],
                        "任务未完成，可重试或重新发送 / Run failed; retry available",
                    )
            except Missing:
                pass
            logging.getLogger("persona").warning("job_failed run_id=%s", job["id"])

    def resume(self, user_id, rid):
        """仅将版本一致的失败任务重新排队，同时重新检查并发限制。"""
        store = ScopedStore(self.db, user_id)
        config = RelationshipAgent(self.client(), store).config()
        with self.db.tx() as c:
            run = store.own(c, runs, rid)
            if run["status"] == "complete":
                return rid
            if run["status"] != "failed":
                raise Busy("Task is already active")
            # 恢复不是重新提交：上下文、模型身份和提示词语义都必须匹配原任务。
            if (
                run["epoch"] != store.epoch(c)
                or run["config"] != config
                or run["state"].get("prompt_version") != PROMPT_VERSION
            ):
                raise ValueError(
                    "记忆或模型版本已变化，请重新发送 / Context changed; send again"
                )
            if c.execute(
                select(runs.c.id).where(
                    runs.c.session_id == run["session_id"],
                    runs.c.status.in_(["queued", "running"]),
                )
            ).first():
                raise Busy("Chat busy")
            # Resume is a new queue admission, not a way around the per-user limit.
            if (
                len(
                    c.execute(
                        select(runs.c.id).where(
                            runs.c.user_id == user_id,
                            runs.c.status.in_(["queued", "running"]),
                        )
                    ).all()
                )
                >= 3
            ):
                raise Busy("最多同时提交3个任务 / Too many active requests")
            c.execute(
                update(runs)
                .where(runs.c.id == rid, runs.c.user_id == user_id)
                .values(status="queued", worker=None, error=None, finished=None)
            )
        return rid

    def public_run(self, user_id, rid):
        """生成网页可见的任务与精简 Trace，排除冻结上下文和运行凭据。"""
        store = ScopedStore(self.db, user_id)
        r = store.run(rid)
        # No frozen context, model configuration, auth details or worker internals in public trace.
        result = r["result"] or None
        # 隐藏的是内部冻结上下文与未筛选的搜索结果；已验证的 sources 仍可展示。
        if result:
            result = {
                k: v
                for k, v in result.items()
                if k not in ("memory_used", "academic_search", "input", "model")
            }
            if result.get("memory_proposal"):
                from .database import events

                with self.db.read() as c:
                    candidate = c.execute(
                        select(events.c.status).where(
                            events.c.id == result["memory_proposal"]["id"],
                            events.c.user_id == user_id,
                        )
                    ).scalar()
                if candidate != "pending":
                    result.pop("memory_proposal", None)
        trace = []
        for t in store.traces(rid):
            detail = t["detail"]
            trace.append(
                {
                    "event": t["event"],
                    "stage": detail.get("stage"),
                    "elapsed_seconds": detail.get("elapsed_seconds"),
                }
            )
        return {
            k: r[k]
            for k in (
                "id",
                "session_id",
                "input",
                "status",
                "error",
                "created",
                "finished",
                "person_ids",
            )
        } | {"result": result, "trace": trace}

    def feedback(self, user_id, rid, category, note, consent):
        """记录回复评价及独立的审阅授权，不隐式授权查看私人对话。"""
        allowed = {
            "helpful",
            "not_helpful",
            "wrong_person",
            "wrong_memory",
            "unsafe",
            "bug",
        }
        if category not in allowed or len(note) > 1500:
            raise ValueError("Invalid feedback")
        with self.db.tx() as c:
            ScopedStore(self.db, user_id).own(c, runs, rid)
            c.execute(
                insert(feedback).values(
                    id=ident(),
                    user_id=user_id,
                    run_id=rid,
                    category=category,
                    note=note,
                    consent=int(consent),
                    created=stamp(),
                )
            )

    def start(self):
        """幂等启动固定数量的后台线程，已有线程运行时不重复启动。"""
        if any(t.is_alive() for t in self.threads):
            return
        # stop_event 同时用作可中断等待，停止服务时无需等完固定 sleep。
        self.stop_event.clear()
        self.threads = []

        def loop():
            while not self.stop_event.is_set():
                try:
                    job = self.claim()
                    if job:
                        self.execute(job)
                    else:
                        self.stop_event.wait(0.3)
                except Exception:
                    logging.getLogger("persona").warning("worker_poll_failed")
                    self.stop_event.wait(1)

        for _ in range(self.settings.workers):
            t = threading.Thread(target=loop, daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self):
        """请求线程停止并有限等待；正在进行的同步 HTTP 请求不能强制取消。"""
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=2)
