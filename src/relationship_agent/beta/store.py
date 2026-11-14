"""Tenant-scoped repository implementing the shared workflow storage protocol.

阅读分组：归属/版本检查 → 人物与事件 → 会话与任务 → 提交 → 上下文检索。
user_id 是身份范围；person_ids 是本次话题范围；epoch 是记忆版本。
已保存的聊天用于展示，送入模型的历史还要通过人物范围、版本和长度过滤。
"""

import json
import re
from sqlalchemy import select, insert, update, delete
from .database import (
    users,
    people,
    sessions,
    runs,
    events,
    memories,
    traces,
    feedback,
    ident,
    stamp,
)


class Missing(ValueError):
    """记录不存在或不属于当前账号；统一错误避免泄露其他用户记录。"""

    pass


class Busy(ValueError):
    """任务执行中或并发限制阻止当前操作。"""

    pass


def public_profile(value):
    """筛选并规范化允许的人物字段，拒绝未知字段和超长资料。"""
    allowed = {
        "nickname",
        "aliases",
        "relationship",
        "age",
        "height",
        "occupation",
        "zodiac",
        "background",
        "impression",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Invalid profile fields")
    profile = {key: str(value.get(key, "")).strip() for key in allowed}
    if not profile["nickname"] or len(profile["nickname"]) > 40:
        raise ValueError("昵称需1–40字 / Nickname required")
    if any(len(v) > 600 for v in profile.values()) or len(profile["aliases"]) > 120:
        raise ValueError("Profile too long")
    return profile


class ScopedStore:
    """在所有资源访问中校验 user_id，并实现工作流存储协议。"""

    def __init__(self, db, user_id, person_ids=None, worker=None, idempotency_key=None):
        """绑定账号、选定人物和可选 worker 身份，不自行授予访问权限。"""
        self.db, self.user_id = db, user_id
        self.person_ids = person_ids or []
        self.worker, self.idempotency_key = worker, idempotency_key

    def own(self, c, table, record_id):
        """在给定连接内读取归属于当前账号的记录，否则抛出 Missing。"""
        row = (
            c.execute(
                select(table).where(
                    table.c.id == record_id, table.c.user_id == self.user_id
                )
            )
            .mappings()
            .first()
        )
        # 无论记录不存在还是属于别人，都返回相同错误，避免泄露其他账号的数据存在性。
        if not row:
            raise Missing("记录不存在 / Record not found")
        return dict(row)

    def epoch(self, c):
        """读取账号的记忆版本；账号已删除时拒绝继续执行。"""
        row = c.execute(select(users.c.epoch).where(users.c.id == self.user_id)).first()
        if not row:
            raise Missing("Account not found")
        return row[0]

    def idle(self, c):
        """确保当前账号没有排队或运行任务，再允许修改记忆。"""
        if c.execute(
            select(runs.c.id).where(
                runs.c.user_id == self.user_id, runs.c.status.in_(["queued", "running"])
            )
        ).first():
            raise Busy("请等待当前任务完成再修改记忆 / Wait for your active tasks")

    # 修改记忆的路径共用这一规则；只改事件文本而不换版本会让旧对话重新污染上下文。
    def invalidate(self, c):
        """增加记忆版本并清除失败任务的旧快照，保留删除依赖。"""
        c.execute(
            update(users)
            .where(users.c.id == self.user_id)
            .values(epoch=users.c.epoch + 1)
        )
        # Failed jobs retain snapshots; they may no longer be resumed after an edit.
        failed = (
            c.execute(
                select(runs.c.id, runs.c.state).where(
                    runs.c.user_id == self.user_id, runs.c.status == "failed"
                )
            )
            .mappings()
            .all()
        )
        for run in failed:
            # Retain only deletion dependencies; frozen message/context must be discarded.
            state = {
                "mentioned_person_ids": (run["state"] or {}).get(
                    "mentioned_person_ids", []
                )
            }
            c.execute(
                update(runs)
                .where(runs.c.id == run["id"], runs.c.user_id == self.user_id)
                .values(
                    state=state,
                    error="记忆已修改，请重新发送 / Memory changed; send again",
                )
            )

    def people(self):
        """按创建顺序返回当前账号的人物列表。"""
        with self.db.read() as c:
            return [
                dict(r)
                for r in c.execute(
                    select(people)
                    .where(people.c.user_id == self.user_id)
                    .order_by(people.c.created)
                ).mappings()
            ]

    def person(self, pid):
        """读取单个人物并校验归属。"""
        with self.db.read() as c:
            return self.own(c, people, pid)

    def save_person(self, profile, pid=None):
        """创建或替换人物资料；修改已有资料会使旧上下文失效。"""
        profile = public_profile(profile)
        with self.db.tx() as c:
            self.idle(c)
            if pid:
                self.own(c, people, pid)
                c.execute(
                    update(people)
                    .where(people.c.id == pid, people.c.user_id == self.user_id)
                    .values(profile=profile)
                )
                self.invalidate(c)
            else:
                count = len(
                    c.execute(
                        select(people.c.id).where(people.c.user_id == self.user_id)
                    ).all()
                )
                if count >= 50:
                    raise ValueError("人物数量上限50 / Person limit reached")
                pid = ident()
                c.execute(
                    insert(people).values(
                        id=pid, user_id=self.user_id, profile=profile, created=stamp()
                    )
                )
        return self.person(pid)

    def purge_person_context(self, c, pid):
        # Strong deletion: remove conversations that contain this person's input or derived output.
        """删除包含该人物的会话与派生产物，并使账号旧上下文失效。"""
        # 既检查执行时选中的人物，也检查实际提及过的人物，覆盖多人对话的删除依赖。
        affected = {
            r["session_id"]
            for r in c.execute(
                select(runs).where(runs.c.user_id == self.user_id)
            ).mappings()
            if pid in (r["person_ids"] or [])
            or pid in (r["state"] or {}).get("mentioned_person_ids", [])
        }
        affected.update(
            c.execute(
                select(sessions.c.id).where(
                    sessions.c.user_id == self.user_id, sessions.c.person_id == pid
                )
            ).scalars()
        )
        if affected:
            c.execute(
                delete(sessions).where(
                    sessions.c.user_id == self.user_id, sessions.c.id.in_(affected)
                )
            )
        self.invalidate(c)

    def delete_person(self, pid):
        """在无活跃任务时删除人物及其相关对话、经历。"""
        with self.db.tx() as c:
            self.idle(c)
            self.own(c, people, pid)
            self.purge_person_context(c, pid)
            c.execute(
                delete(people).where(
                    people.c.id == pid, people.c.user_id == self.user_id
                )
            )

    def resolve(self, message, selected=None):
        """返回 (确定的人物 ID 列表, 待澄清人物列表)。

        只依据显式选择或已登记昵称/别名匹配，不进行性格或身份语义推断。
        单个明确提及可覆盖旧选择；多人或不明确代词不会自动归档。"""
        all_people = self.people()
        if selected:
            self.person(selected)
        matched = []
        for person in all_people:
            names = [person["profile"]["nickname"]] + re.split(
                r"[,，、\n]", person["profile"].get("aliases", "")
            )
            # 英文别名要求完整词边界，例如 Ann 不应匹配 Joanne；中文按登记片段匹配。
            for name in filter(None, (n.strip() for n in names)):
                pattern = re.escape(name)
                if name.isascii():
                    pattern = r"(?<!\w)" + pattern + r"(?!\w)"
                if re.search(pattern, message, re.I):
                    matched.append(person["id"])
                    break
        # Multiple named people require an explicit focus choice; never silently attach an event.
        if len(matched) > 1:
            if selected in matched:
                return [selected], []
            return [], [p for p in all_people if p["id"] in matched]
        if (
            not matched
            and not selected
            and all_people
            and re.search(r"他|她|\b(he|she|him|her|they|them)\b", message, re.I)
        ):
            return [], all_people
        # 单个明确名称优先于旧的下拉框选择；没有名称时才退回显式选择。
        return matched or ([selected] if selected else []), []

    def list_events(self, pid):
        """读取人物的待确认和已确认事件，不返回已拒绝事件。"""
        with self.db.read() as c:
            self.own(c, people, pid)
            return [
                dict(r)
                for r in c.execute(
                    select(events)
                    .where(
                        events.c.user_id == self.user_id,
                        events.c.person_id == pid,
                        events.c.status != "rejected",
                    )
                    .order_by(events.c.created.desc())
                ).mappings()
            ]

    def save_event(self, pid, payload, eid=None):
        """保存用户手动记录或确认/修改候选事件，并使旧模型上下文失效。"""
        text = str(payload.get("text", "")).strip()
        kind = payload.get("kind", "account")
        status = payload.get("status", "confirmed")
        if (
            not 1 <= len(text) <= 3000
            or kind not in ("account", "feeling", "impression")
            or status not in ("confirmed", "rejected")
        ):
            raise ValueError("Invalid memory")
        occurred = str(payload.get("occurred_at", "")).strip()
        if len(occurred) > 64:
            raise ValueError("Invalid date")
        with self.db.tx() as c:
            self.idle(c)
            self.own(c, people, pid)
            if eid:
                previous = self.own(c, events, eid)
                if previous["person_id"] != pid:
                    raise Missing("Memory not found")
                c.execute(
                    update(events)
                    .where(events.c.id == eid, events.c.user_id == self.user_id)
                    .values(text=text, kind=kind, status=status, occurred_at=occurred)
                )
                # Exclude stale transcript/checkpoints from subsequent model context.
                self.invalidate(c)
            else:
                eid = ident()
                c.execute(
                    insert(events).values(
                        id=eid,
                        user_id=self.user_id,
                        person_id=pid,
                        run_id=None,
                        quote=text,
                        text=text,
                        kind=kind,
                        status=status,
                        occurred_at=occurred,
                        created=stamp(),
                    )
                )
                self.invalidate(c)
        return eid

    def delete_event(self, eid):
        """删除事件及涉及该人物的对话，避免旧输出重新带回删除内容。"""
        with self.db.tx() as c:
            self.idle(c)
            old = self.own(c, events, eid)
            self.purge_person_context(c, old["person_id"])
            c.execute(
                delete(events).where(
                    events.c.id == eid, events.c.user_id == self.user_id
                )
            )

    def create_session(self, title="", mode="mock", language="zh", person_id=None):
        """创建指定语言的会话并校验可选人物归属；mode 为兼容参数。"""
        if language not in ("zh", "en"):
            raise ValueError("Invalid language")
        with self.db.tx() as c:
            if person_id:
                self.own(c, people, person_id)
            sid = ident()
            c.execute(
                insert(sessions).values(
                    id=sid,
                    user_id=self.user_id,
                    title=(title.strip() or "新对话 / New chat")[:100],
                    language=language,
                    person_id=person_id,
                    created=stamp(),
                )
            )
        return self.session(sid)

    def session(self, sid):
        """读取当前账号的单个会话。"""
        with self.db.read() as c:
            return self.own(c, sessions, sid)

    def sessions(self):
        """按创建时间倒序返回当前账号的会话。"""
        with self.db.read() as c:
            return [
                dict(x)
                for x in c.execute(
                    select(sessions)
                    .where(sessions.c.user_id == self.user_id)
                    .order_by(sessions.c.created.desc())
                ).mappings()
            ]

    def delete_session(self, sid):
        """删除会话、执行记录及由该会话产生的人物记忆。"""
        with self.db.tx() as c:
            self.idle(c)
            self.own(c, sessions, sid)
            # Source-linked memories also removed when deleting their source conversation.
            # 先清理来源事件，再删会话；否则事件的 SET NULL 外键会留下脱离来源的记忆。
            ids = select(runs.c.id).where(
                runs.c.user_id == self.user_id, runs.c.session_id == sid
            )
            c.execute(
                delete(events).where(
                    events.c.user_id == self.user_id, events.c.run_id.in_(ids)
                )
            )
            c.execute(
                delete(sessions).where(
                    sessions.c.id == sid, sessions.c.user_id == self.user_id
                )
            )
            self.invalidate(c)

    def create_run(self, sid, kind, message, config, state=None):
        """原子校验幂等键、并发上限和记忆版本，保存待执行任务。"""
        with self.db.tx() as c:
            self.own(c, sessions, sid)
            # 此处在写事务里再次检查幂等键，防两个并发请求都通过 service 层的提前检查。
            if self.idempotency_key:
                old = (
                    c.execute(
                        select(runs).where(
                            runs.c.user_id == self.user_id,
                            runs.c.idempotency_key == self.idempotency_key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if old:
                    if (
                        old["session_id"] != sid
                        or old["input"] != message
                        or old["state"].get(
                            "selected_person_id",
                            (state or {}).get("selected_person_id"),
                        )
                        != (state or {}).get("selected_person_id")
                    ):
                        raise ValueError("Idempotency key reused for different input")
                    return old["id"]
            if c.execute(
                select(runs.c.id).where(
                    runs.c.session_id == sid, runs.c.status.in_(["queued", "running"])
                )
            ).first():
                raise Busy("此对话正在回复 / This chat is busy")
            if (
                len(
                    c.execute(
                        select(runs.c.id).where(
                            runs.c.user_id == self.user_id,
                            runs.c.status.in_(["queued", "running"]),
                        )
                    ).all()
                )
                >= 3
            ):
                raise Busy("最多同时提交3个任务 / Too many active requests")
            for pid in self.person_ids:
                self.own(c, people, pid)
            from .database import attachments

            for aid in (state or {}).get("attachment_ids", []):
                attachment = self.own(c, attachments, aid)
                if attachment["session_id"] != sid:
                    raise ValueError("Attachment session mismatch")
            # 准备上下文与入队之间可能发生纠错；版本不一致时宁可让用户重发。
            if (state or {}).get("context_epoch", self.epoch(c)) != self.epoch(c):
                raise Busy(
                    "准备消息期间记忆发生变化，请重试 / Memory changed while preparing; please retry"
                )
            rid = ident()
            c.execute(
                insert(runs).values(
                    id=rid,
                    user_id=self.user_id,
                    session_id=sid,
                    kind=kind,
                    input=message,
                    config=config,
                    state=state or {},
                    status="queued",
                    created=stamp(),
                    epoch=self.epoch(c),
                    person_ids=self.person_ids,
                    idempotency_key=self.idempotency_key,
                    lease_until=0,
                )
            )
        return rid

    def run(self, rid):
        """读取当前账号的内部任务记录，包含不可直接公开的冻结上下文。"""
        with self.db.read() as c:
            return self.own(c, runs, rid)

    def runs(self, sid):
        """校验会话归属后按创建顺序读取其中的任务。"""
        with self.db.read() as c:
            self.own(c, sessions, sid)
            return [
                dict(r)
                for r in c.execute(
                    select(runs)
                    .where(runs.c.user_id == self.user_id, runs.c.session_id == sid)
                    .order_by(runs.c.created)
                ).mappings()
            ]

    def valid_job(self, c, rid):
        """校验任务归属、记忆版本和 worker 租约，拒绝过期执行者写入。"""
        run = self.own(c, runs, rid)
        if run["epoch"] != self.epoch(c):
            raise ValueError("记忆已更新，请重新发送 / Memory changed")
        if self.worker and (
            run["worker"] != self.worker
            or run["lease_until"] < stamp()
            or run["status"] != "running"
        ):
            raise ValueError("Execution lease lost")
        return run

    def checkpoint(self, rid, state):
        """在有效租约下保存已完成阶段并续租，支持失败恢复。"""
        with self.db.tx() as c:
            self.valid_job(c, rid)
            c.execute(
                update(runs)
                .where(runs.c.id == rid, runs.c.user_id == self.user_id)
                .values(state=state, lease_until=stamp() + 300)
            )

    def fail(self, rid, error):
        """记录脱敏失败信息；不允许旧 worker 覆盖新 worker 的状态。"""
        with self.db.tx() as c:
            run = self.own(c, runs, rid)
            if self.worker and run["worker"] != self.worker:
                return
            c.execute(
                update(runs)
                .where(runs.c.id == rid, runs.c.user_id == self.user_id)
                .values(status="failed", error=error[:200], finished=stamp())
            )

    def trace(self, rid, event, detail=None):
        """保存属于该账号任务的执行事件；调用方负责不传入凭据。"""
        with self.db.tx() as c:
            self.own(c, runs, rid)
            c.execute(
                insert(traces).values(
                    id=ident(),
                    user_id=self.user_id,
                    run_id=rid,
                    event=event,
                    detail=detail or {},
                    created=stamp(),
                )
            )

    def traces(self, rid):
        """按时间返回任务执行记录，不代表其内容可直接公开。"""
        with self.db.read() as c:
            self.own(c, runs, rid)
            return [
                dict(r)
                for r in c.execute(
                    select(traces)
                    .where(traces.c.user_id == self.user_id, traces.c.run_id == rid)
                    .order_by(traces.c.created)
                ).mappings()
            ]

    def finish(self, rid, result, records):
        """原子提交回答、分层工作流记忆及可选的待确认人物陈述。

        候选不是已核实事实。已完成任务直接返回，避免重复生成记忆。"""
        with self.db.tx() as c:
            run = self.valid_job(c, rid)
            # finish 必须幂等：重复提交不能再插入一份事件或工作流记忆。
            if run["status"] == "complete":
                return
            for layer, kind, content in records:
                if layer not in ("observation", "inference", "simulation"):
                    raise ValueError("Invalid memory layer")
                c.execute(
                    insert(memories).values(
                        id=ident(),
                        user_id=self.user_id,
                        session_id=run["session_id"],
                        run_id=rid,
                        layer=layer,
                        kind=kind,
                        content=content,
                        created=stamp(),
                    )
                )
            result = {**result, "person_ids": run["person_ids"]}
            # This is a verbatim candidate user account, NOT extracted/verified fact.
            # Questions, research, ambiguous multi-person messages and rehearsals aren't auto-proposed.
            if (
                len(run["person_ids"] or []) == 1
                and result.get("kind") in ("chat", "analysis", "feedback")
                and not result.get("attachment_coverage")
                and not result.get("dialogue_rehearsal")
                and not any(
                    x in run["input"]
                    for x in ("？", "?", "演练", "假设", "如果", "rehearse", "what if")
                )
            ):
                pid = run["person_ids"][0]
                if not run["state"].get("multi_person"):
                    eid = ident()
                    c.execute(
                        insert(events).values(
                            id=eid,
                            user_id=self.user_id,
                            person_id=pid,
                            run_id=rid,
                            quote=run["input"],
                            text=run["input"][:3000],
                            occurred_at="",
                            kind="account",
                            status="pending",
                            created=stamp(),
                        )
                    )
                    result["memory_proposal"] = {
                        "id": eid,
                        "person_id": pid,
                        "text": run["input"][:3000],
                        "kind": "account",
                    }
            c.execute(
                update(runs)
                .where(runs.c.id == rid, runs.c.user_id == self.user_id)
                .values(status="complete", result=result, error=None, finished=stamp())
            )

    def context(self, sid, max_chars=6000, query=""):
        """按账号/人物范围检索资料和已确认事件，受字符预算限制。

        先提供资料，再按词项相关度与时间选择事件；不是向量检索。"""
        self.session(sid)
        selected = []
        used = 0
        with self.db.read() as c:
            for pid in self.person_ids:
                p = self.own(c, people, pid)
                profile = {
                    "id": pid,
                    "person_id": pid,
                    "source": "user_profile_unverified",
                    "text": json.dumps(p["profile"], ensure_ascii=False),
                }
                cost = len(json.dumps(profile, ensure_ascii=False))
                if used + cost <= max_chars:
                    selected.append(profile)
                    used += cost
            rows = (
                c.execute(
                    select(events)
                    .where(
                        events.c.user_id == self.user_id,
                        events.c.person_id.in_(self.person_ids),
                        events.c.status == "confirmed",
                    )
                    .order_by(events.c.created.desc())
                )
                .mappings()
                .all()
                if self.person_ids
                else []
            )
        # 这是简单词项匹配：相关度优先，创建时间次之；没有语义向量或人格相似度。
        words = set(re.findall(r"[a-z]{2,}|[\u4e00-\u9fff]{2}", query.lower()))
        rows = sorted(
            rows,
            key=lambda r: (sum(w in r["text"].lower() for w in words), r["created"]),
            reverse=True,
        )
        for r in rows:
            item = {
                "id": r["id"],
                "person_id": r["person_id"],
                "source": "user_report_unverified",
                "kind": r["kind"],
                "text": r["text"],
                "occurred_at": r["occurred_at"],
            }
            cost = len(json.dumps(item, ensure_ascii=False))
            if used + cost > max_chars:
                continue
            selected.append(item)
            used += cost
            if len(selected) >= 10:
                break
        return selected

    def conversation(self, sid, budget=8500):
        """返回同一人物范围、同一记忆版本的最近十二轮有限历史。

        保留用户原话；生成的报告和模拟内容不作为真实对话证据回填。"""
        rows = self.runs(sid)
        with self.db.read() as c:
            epoch = self.epoch(c)
        pairs = []
        used = 0
        for r in reversed(rows):
            if (
                r["status"] != "complete"
                or r["epoch"] != epoch
                or sorted(r["person_ids"] or []) != sorted(self.person_ids)
            ):
                continue
            result = r["result"] or {}
            # Keep what the user actually said across workflow changes. Generated
            # reports and simulations remain excluded; neither becomes real evidence.
            if result.get("kind") not in (
                "chat",
                "knowledge",
                "evidence",
                "analysis",
                "feedback",
            ):
                continue
            pair = [{"role": "user", "content": r["input"]}]
            if result.get("kind") in ("chat", "knowledge", "evidence"):
                reply = result.get("reply") or result.get("answer", "")
                pair.append({"role": "assistant", "content": reply})
            cost = len(json.dumps(pair, ensure_ascii=False))
            # 按整轮的可用消息计算预算，不截断消息正文；报告轮只保留用户陈述。
            if used + cost > budget:
                break
            pairs.append(pair)
            used += cost
            if len(pairs) >= 12:
                break
        return [m for pair in reversed(pairs) for m in pair]

    def latest_report(self, sid):
        """返回同人物范围与记忆版本下最近完成的报告或报告修正。"""
        with self.db.read() as c:
            epoch = self.epoch(c)
        for r in reversed(self.runs(sid)):
            if (
                r["epoch"] == epoch
                and r["status"] == "complete"
                and sorted(r["person_ids"] or []) == sorted(self.person_ids)
                and (r["result"] or {}).get("kind") in ("analysis", "feedback")
            ):
                return r["result"]
        return None

    def delete_account(self):
        """无活跃任务时删除当前账号，依靠外键级联清理所属数据。"""
        with self.db.tx() as c:
            self.idle(c)
            c.execute(delete(users).where(users.c.id == self.user_id))

    def export(self):
        """导出当前账号业务数据，不包含密码、令牌或供应商密钥。"""
        from .database import attachments

        with self.db.read() as c:
            return {
                t.name: [
                    dict(r)
                    for r in c.execute(
                        select(t).where(t.c.user_id == self.user_id)
                    ).mappings()
                ]
                for t in (people, sessions, runs, events, feedback, attachments)
            }
