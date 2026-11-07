"""Isolated harness fixtures backed by the current tenant-scoped database.

Forced kinds exercise individual engine stages only. Public requests always use
Service.submit() and the router; no test-only endpoint is exposed by the app.
"""

import tempfile
from unittest.mock import patch

from sqlalchemy import select

from relationship_agent.beta.auth import Auth
from relationship_agent.beta.config import Settings
from relationship_agent.beta.database import Database, memories
from relationship_agent.beta.service import Service, PROMPT_VERSION
from relationship_agent.beta.store import ScopedStore
from relationship_agent.demo import MockModelClient
from relationship_agent.engine import RelationshipAgent
from relationship_agent.knowledge import search


class HarnessFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_url="sqlite:///" + self.temp.name + "/test.sqlite"
        )
        self.db = Database(self.settings.database_url)
        auth = Auth(self.db, self.settings)
        token, _ = auth.login(
            "fixture", "synthetic-password-123", register=True, adult=True, consent=True
        )
        self.uid = auth.authenticate(token)["user_id"]
        self.store = ScopedStore(self.db, self.uid)

    def driver(self, client=None, academic=None):
        return Driver(self, client or MockModelClient(), academic)

    def records(self, sid, layer=None):
        with self.db.read() as connection:
            query = select(memories).where(
                memories.c.user_id == self.uid, memories.c.session_id == sid
            )
            if layer:
                query = query.where(memories.c.layer == layer)
            return [dict(row) for row in connection.execute(query).mappings()]

    def close(self):
        self.db.close()
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Driver:
    def __init__(self, fixture, client, academic=None):
        self.fixture, self.client, self.academic = fixture, client, academic
        self.store = fixture.store
        self.service = Service(fixture.db, fixture.settings, lambda: client)

    def start(self, sid, message, kind="rehearsal"):
        if kind == "auto":
            return self.service.submit(self.fixture.uid, sid, message)
        previous = self.store.latest_report(sid)
        if kind == "feedback" and not previous:
            raise ValueError("Complete an analysis before feedback")
        with self.fixture.db.read() as connection:
            epoch = self.store.epoch(connection)
        state = {
            "language": self.store.session(sid)["language"],
            "context": self.store.context(sid),
            "conversation": self.store.conversation(sid),
            "context_epoch": epoch,
            "prompt_version": PROMPT_VERSION,
        }
        if previous:
            state["previous_judgments"] = previous["current_hypotheses"]
        if kind == "feedback":
            state["hypotheses"] = previous["current_hypotheses"]
        if kind == "knowledge":
            state["context"] = []
            state["retrieved"] = search(message, language=state["language"])
        return self.store.create_run(
            sid,
            kind,
            message,
            RelationshipAgent(self.client, self.store).config(),
            state,
        )

    def execute(self, rid):
        run = self.store.run(rid)
        if run["status"] == "complete":
            return run["result"]
        if run["status"] == "failed":
            self.service.resume(self.fixture.uid, rid)
        job = self.service.claim()
        assert job is not None and job["id"] == rid
        if self.academic is None:
            self.service.execute(job)
        else:
            with patch(
                "relationship_agent.beta.service.PrivateSearch",
                return_value=self.academic,
            ):
                self.service.execute(job)
        run = self.store.run(rid)
        if run["status"] != "complete":
            raise RuntimeError(run["error"])
        return run["result"]
