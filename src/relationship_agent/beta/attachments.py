"""Account/session-scoped attachment storage and explicit evidence-to-memory confirmation.

删除附件时清理其会话和派生记忆，避免原文残留在任务快照或回答中。
原件用 base64 文本入库，沿用 JSON 加密备份，不写公开静态目录。
"""

import base64
import threading
from sqlalchemy import select, insert, update, func
from .database import attachments, runs, events, people, sessions, ident, stamp, quota
from .store import ScopedStore, Busy
from ..attachments import extract


# 每个应用进程最多同时运行两个解析器，避免并发上传耗尽 CPU/内存。
PARSERS = threading.BoundedSemaphore(2)


class AttachmentStore:
    """复用 ScopedStore 的账号归属、事务锁和记忆版本规则。"""

    def __init__(self, db, user_id):
        """绑定已认证账号，不接受文件中指定的账号或人物身份。"""
        self.db, self.uid = db, user_id
        self.store = ScopedStore(db, user_id)

    def list(self, sid):
        """返回本会话元数据；列表不携带原件和全文。"""
        self.store.session(sid)
        with self.db.read() as c:
            rows = c.execute(
                select(attachments)
                .where(
                    attachments.c.user_id == self.uid, attachments.c.session_id == sid
                )
                .order_by(attachments.c.created)
            ).mappings()
            return [self.public(r) for r in rows]

    @staticmethod
    def public(row):
        """可展示的解析状态；总段数用于提示读取覆盖范围。"""
        return {
            k: row[k]
            for k in (
                "id",
                "name",
                "size",
                "pages",
                "characters",
                "blank_pages",
                "created",
            )
        }

    def get(self, aid):
        """读取当前账号文件，归属不匹配与不存在均返回 404。"""
        with self.db.read() as c:
            return self.store.own(c, attachments, aid)

    def add(self, sid, name, content):
        """检查归属后解析，事务内再次校验会话及每账号 20 MB / 20 份限额。"""
        self.store.session(sid)
        try:
            raw = base64.b64decode(content, validate=True)
        except ValueError:
            raise ValueError("文件编码无效 / Invalid file encoding") from None
        # 失败文件也消耗上传尝试次数，不能靠反复提交坏 PDF 无限占用解析器。
        with self.db.tx() as c:
            self.store.own(c, sessions, sid)
            self.store.idle(c)
            key = f"attachment:{self.uid}:{int(stamp() // 600)}"
            calls = c.execute(select(quota.c.calls).where(quota.c.key == key)).scalar()
            if calls is not None and calls >= 20:
                raise Busy("上传过于频繁，请稍后再试 / Upload rate limit reached")
            if calls is None:
                c.execute(insert(quota).values(key=key, calls=1, reserved_usd=0))
            else:
                c.execute(
                    update(quota).where(quota.c.key == key).values(calls=calls + 1)
                )
        if not PARSERS.acquire(blocking=False):
            raise Busy("正在读取其他附件，请稍后重试 / File parser busy")
        try:
            parsed = extract(name, raw)
        finally:
            PARSERS.release()
        with self.db.tx() as c:
            self.store.own(c, sessions, sid)
            self.store.idle(c)
            count, size = c.execute(
                select(
                    func.count(), func.coalesce(func.sum(attachments.c.size), 0)
                ).where(attachments.c.user_id == self.uid)
            ).one()
            if count >= 20 or size + len(raw) > 20 * 1024 * 1024:
                raise ValueError(
                    "附件空间已满，请先删除旧附件 / Attachment quota reached"
                )
            aid = ident()
            row = {
                **parsed,
                "id": aid,
                "session_id": sid,
                "user_id": self.uid,
                "size": len(raw),
                "original": content,
                "created": stamp(),
            }
            c.execute(insert(attachments).values(**row))
        return self.public(row)

    def select(self, sid, ids):
        """显式选择至多三份同会话文件，不自动读取其他会话的附件。"""
        if len(ids) > 3 or len(ids) != len(set(ids)):
            raise ValueError(
                "每次最多选择 3 个不同附件 / Select up to three distinct files"
            )
        rows = [self.get(aid) for aid in ids]
        if any(r["session_id"] != sid for r in rows):
            raise ValueError(
                "附件不属于当前会话 / Attachment belongs to another session"
            )
        return rows

    def delete(self, aid):
        """删除整个来源会话及关联记忆，防止附件在快照、历史中继续出现。"""
        row = self.get(aid)
        self.store.delete_session(row["session_id"])

    def confirm(self, rid, person_id, source_id, quote, text, kind):
        """用户明确确认后保存一条记忆，原文引用必须出现在本次已校验来源中。"""
        if (
            not text.strip()
            or len(text) > 3000
            or kind not in ("account", "feeling", "impression")
        ):
            raise ValueError("记忆内容无效 / Invalid memory")
        with self.db.tx() as c:
            self.store.idle(c)
            run = self.store.own(c, runs, rid)
            self.store.own(c, people, person_id)
            if person_id not in (run["person_ids"] or []):
                raise ValueError(
                    "仅能记入本次选定人物 / Choose the person before analysis"
                )
            citations = (run["result"] or {}).get("attachment_sources", [])
            citation = next(
                (
                    s
                    for s in citations
                    if s["source_id"] == source_id and s["quote"] == quote
                ),
                None,
            )
            if not citation:
                raise ValueError("来源不匹配 / Unverified source")
            self.store.own(c, attachments, citation["attachment_id"])
            if c.execute(
                select(events.c.id).where(
                    events.c.run_id == rid, events.c.person_id == person_id
                )
            ).first():
                raise ValueError(
                    "本次回答已保存记忆，可在人物档案修改 / Memory already saved; edit in profile"
                )
            eid = ident()
            c.execute(
                insert(events).values(
                    id=eid,
                    user_id=self.uid,
                    person_id=person_id,
                    run_id=rid,
                    quote=f"{citation['name']} · page {citation['page'] or '-'} · line {citation['line']}\n{quote}",
                    text=text.strip(),
                    kind=kind,
                    status="confirmed",
                    occurred_at="",
                    created=stamp(),
                )
            )
            self.store.invalidate(c)
        return {"id": eid}
