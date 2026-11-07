"""Portable SQLAlchemy schema. All application reads are tenant-scoped.

Version 1 creates a separate database; legacy single-user records are never claimed.
SQLite is only for local development. PostgreSQL is required in production.

逻辑关系：users → people / conversations → jobs；人物事件关联 person_id，
工作流记忆和 Trace 关联 run_id。quota 是配额账本，attempts 记录请求用量。
表名和外键属于持久数据契约，修改时需设计迁移，不能只改 Python 字段。
"""

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import time
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Text,
    Integer,
    Float,
    JSON,
    ForeignKey,
    UniqueConstraint,
    create_engine,
    event,
    select,
    insert,
)

meta = MetaData()


def ident():
    """生成应用记录 ID；ID 的不可猜测性不替代归属校验。"""
    return uuid4().hex


def stamp():
    """返回 Unix 秒时间戳，用于租约、排序和过期检查。"""
    return time.time()


def idcol():
    """为每张实体表创建独立的主键列对象。"""
    return Column("id", String(64), primary_key=True)


def owner():
    """创建用户外键列，账号删除时级联清理其记录。"""
    return Column(
        "user_id",
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


versions = Table("schema_versions", meta, Column("version", Integer, primary_key=True))
# 账号 epoch 是上下文版本号：确认/更正记忆后增加，旧任务不能继续用旧快照。
users = Table(
    "users",
    meta,
    idcol(),
    Column("username", String(64), unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created", Float),
    Column("epoch", Integer, default=0),
    Column("consent_version", String(32)),
)
tokens = Table(
    "auth_tokens",
    meta,
    Column("digest", String(64), primary_key=True),
    owner(),
    Column("csrf", String(128)),
    Column("expires", Float),
)
people = Table(
    "people", meta, idcol(), owner(), Column("profile", JSON), Column("created", Float)
)
# conversations 是会话容器；人物删除时外键置空，业务层还会清理相关会话。
sessions = Table(
    "conversations",
    meta,
    idcol(),
    owner(),
    Column("title", String(100)),
    Column("language", String(5)),
    Column("person_id", String(64), ForeignKey("people.id", ondelete="SET NULL")),
    Column("created", Float),
)
# jobs 同时保存用户消息和任务状态：queued → running → complete/failed。
# config 是模型身份，state 是中间检查点，result 是最终输出；worker/lease_until 防旧执行者写入。
runs = Table(
    "jobs",
    meta,
    idcol(),
    owner(),
    Column(
        "session_id",
        String(64),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    ),
    Column("kind", String(32)),
    Column("input", Text),
    Column("config", JSON),
    Column("state", JSON),
    Column("result", JSON),
    Column("status", String(32)),
    Column("error", String(200)),
    Column("created", Float),
    Column("finished", Float),
    Column("lease_until", Float, default=0),
    Column("worker", String(64)),
    Column("epoch", Integer),
    Column("person_ids", JSON),
    Column("idempotency_key", String(64)),
    UniqueConstraint("user_id", "idempotency_key"),
)
# person_events 是可跨会话检索的候选/已确认记忆；quote 保留原始来源，text 可被纠正。
events = Table(
    "person_events",
    meta,
    idcol(),
    owner(),
    Column(
        "person_id", String(64), ForeignKey("people.id", ondelete="CASCADE"), index=True
    ),
    Column("run_id", String(64), ForeignKey("jobs.id", ondelete="SET NULL")),
    Column("quote", Text),
    Column("text", Text),
    Column("occurred_at", String(64)),
    Column("kind", String(24)),
    Column("status", String(24)),
    Column("created", Float),
    UniqueConstraint("run_id", "person_id"),
)
# workflow_memory 按 observation/inference/simulation 分层留痕；不等同于已确认人物事件。
memories = Table(
    "workflow_memory",
    meta,
    idcol(),
    owner(),
    Column(
        "session_id", String(64), ForeignKey("conversations.id", ondelete="CASCADE")
    ),
    Column("run_id", String(64), ForeignKey("jobs.id", ondelete="CASCADE")),
    Column("layer", String(24)),
    Column("kind", String(32)),
    Column("content", JSON),
    Column("created", Float),
    UniqueConstraint("run_id", "layer", "kind"),
)
traces = Table(
    "execution_trace",
    meta,
    idcol(),
    owner(),
    Column("run_id", String(64), ForeignKey("jobs.id", ondelete="CASCADE")),
    Column("event", String(64)),
    Column("detail", JSON),
    Column("created", Float),
)
feedback = Table(
    "user_feedback",
    meta,
    idcol(),
    owner(),
    Column("run_id", String(64), ForeignKey("jobs.id", ondelete="CASCADE")),
    Column("category", String(40)),
    Column("note", Text),
    Column("consent", Integer),
    Column("created", Float),
)
# 配额键区分认证窗口与每日模型额度；模型配额还有 global 和账号两个层级。
quota = Table(
    "quota_ledger",
    meta,
    Column("key", String(128), primary_key=True),
    Column("calls", Integer),
    Column("reserved_usd", Float),
)
# 一次用户消息可能产生多次 HTTP 尝试；用 attempts 才能计入重试和 JSON 修复成本。
attempts = Table(
    "model_attempts",
    meta,
    idcol(),
    owner(),
    Column("run_id", String(64), ForeignKey("jobs.id", ondelete="CASCADE")),
    Column("created", Float),
    Column("stage", String(40)),
    Column("usage", JSON),
    Column("estimated_usd", Float),
)
locks = Table("transaction_locks", meta, Column("name", String(32), primary_key=True))


class Database:
    """封装 SQLite/PostgreSQL 连接、初始表结构和短写事务。"""

    def __init__(self, url):
        """规范化数据库 URL、配置连接并确保初始 schema 存在。"""
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            **(
                {"connect_args": {"check_same_thread": False, "timeout": 15}}
                if url.startswith("sqlite")
                else {}
            ),
        )
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def sqlite_pragmas(db, _):
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA journal_mode=WAL")

        self.migrate()

    def migrate(self):
        """幂等创建 v1 表并拒绝较新版本；当前不执行历史列结构升级。"""
        with self.engine.begin() as c:
            if self.engine.dialect.name == "postgresql":
                from sqlalchemy import text

                # 多个实例并发启动时串行建表，避免同时写入初始版本与锁行。
                c.execute(text("SELECT pg_advisory_xact_lock(73918421)"))
            meta.create_all(c)
            current = c.execute(select(versions.c.version)).scalars().all()
            if current and max(current) > 1:
                raise RuntimeError("Database is newer than this application")
            if not current:
                c.execute(insert(versions).values(version=1))
            if not c.execute(select(locks).where(locks.c.name == "write")).first():
                c.execute(insert(locks).values(name="write"))

    @contextmanager
    def tx(self):
        # Short metadata transactions only; NEVER hold this lock during model/network work.
        """提供串行短写事务，成功提交、异常回滚；事务内禁止网络请求。"""
        with self.engine.connect() as c:
            if self.engine.dialect.name == "sqlite":
                c.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                c.begin()
                c.execute(
                    select(locks).where(locks.c.name == "write").with_for_update()
                )
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    @contextmanager
    def read(self):
        """提供只读用途的连接作用域，退出时归还连接。"""
        with self.engine.connect() as c:
            yield c

    def close(self):
        """释放连接池；调用方必须先停止使用数据库的工作线程。"""
        self.engine.dispose()
