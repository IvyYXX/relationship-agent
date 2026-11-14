"""Synthetic attachment end-to-end tests; no private files or paid model calls.

验证真实 PDF 提取、文件/账号/会话边界、引用契约、明确记忆确认及删除依赖。
PDF 内容在内存构造，保证测试不依赖用户文件或系统字体。
"""

import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from sqlalchemy import select

from relationship_agent.attachments import extract, retrieve, MAX_BYTES
from relationship_agent.beta.app import create_app
from relationship_agent.beta.config import Settings
from relationship_agent.beta.database import attachments, versions, Database
from relationship_agent.beta.ops import backup, restore
from relationship_agent.schemas import validate_stage, ContractError


def pdf_bytes(text="Lin: Let's meet on Friday.", blank=False, encrypted=False):
    """构造有真实文字层的单页 PDF；空白与加密变体用于失败测试。"""
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-only")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class ExtractionTests(unittest.TestCase):
    """检查受限解析与按来源检索，不把成功提取当成内容真实性证明。"""

    def test_text_pdf_and_utf8_preserve_locations(self):
        txt = extract("chat.txt", "2026-11-08\n小林：周五见。".encode())
        self.assertEqual(txt["blocks"][1]["line"], 2)
        doc = extract("chat.pdf", pdf_bytes())
        self.assertIn("Friday", doc["blocks"][0]["text"])
        self.assertEqual(doc["blocks"][0]["page"], 1)

    def test_reject_bad_empty_scanned_encrypted_and_large_files(self):
        for name, data in [
            ("a.exe", b"xx"),
            ("a.txt", b"\x00"),
            ("a.txt", b""),
            ("a.txt", b"x" * (MAX_BYTES + 1)),
            ("a.pdf", b"not pdf"),
            ("a.txt", b"\xff"),
            ("a.pdf", pdf_bytes(blank=True)),
            ("a.pdf", pdf_bytes(encrypted=True)),
        ]:
            with (
                self.subTest(name=name, length=len(data)),
                self.assertRaises(ValueError),
            ):
                extract(name, data)

    def test_pdf_timeout_is_sanitized(self):
        import subprocess

        with (
            patch(
                "relationship_agent.attachments.subprocess.run",
                side_effect=subprocess.TimeoutExpired("pdf", 15),
            ),
            self.assertRaisesRegex(ValueError, "解析限额"),
        ):
            extract("x.pdf", b"%PDF-1.7")

    def test_partial_retrieval_finds_late_evidence_and_reports_scope(self):
        parsed = extract(
            "a.txt", ("hello world\n" * 500 + "Friday appointment cancelled").encode()
        )
        result = retrieve([{**parsed, "id": "a"}], "Friday cancelled", budget=600)
        self.assertTrue(result["coverage"][0]["partial"])
        self.assertTrue(any("Friday" in b["text"] for b in result["excerpts"]))
        self.assertLess(len(result["excerpts"]), len(parsed["blocks"]))

    def test_citations_reject_fabrication(self):
        payload = {"excerpts": [{"source_id": "a:1", "text": "Friday"}]}
        for citation in [
            {"source_id": "b:1", "quote": "Friday"},
            {"source_id": "a:1", "quote": "Monday"},
            {"source_id": "a:1", "quote": ""},
        ]:
            with self.assertRaises(ContractError):
                validate_stage(
                    "attachment_chat",
                    {"reply": "answer", "citations": [citation]},
                    payload,
                )


class AttachmentAPITests(unittest.TestCase):
    """通过真实 HTTP 与数据库验证上传、执行、确认和删除闭环。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(
            Settings(
                database_url="sqlite:///" + self.tmp.name + "/test.db",
                origin="http://testserver",
            ),
            start_workers=False,
        )
        self.db = self.app.state.db
        self.a, self.b = self.client("alice"), self.client("bravo")
        self.sid = self.a.post("/api/sessions", json={}).json()["id"]
        self.pid = self.a.post("/api/people", json={"nickname": "Lin"}).json()["id"]

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.db.close()
        self.tmp.cleanup()

    def client(self, name):
        c = TestClient(self.app)
        c.headers["origin"] = "http://testserver"
        r = c.post(
            "/api/auth/register",
            json={
                "username": name,
                "password": "synthetic-password-123",
                "adult": True,
                "consent": True,
            },
        )
        c.headers["x-csrf-token"] = r.json()["csrf"]
        return c

    def upload(self, content=b"Lin: Let's meet Friday.\nMe: Great.", name="chat.txt"):
        r = self.a.post(
            "/api/attachments",
            json={
                "session_id": self.sid,
                "name": name,
                "content": base64.b64encode(content).decode(),
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def run_file(self, aid, key="attachment-test-key", person=True):
        body = {
            "session_id": self.sid,
            "message": "How should I reply?",
            "attachment_ids": [aid],
            "idempotency_key": key,
        }
        if person:
            body["person_id"] = self.pid
        r = self.a.post("/api/message", json=body)
        self.assertEqual(r.status_code, 202, r.text)
        svc = self.app.state.service
        svc.execute(svc.claim())
        run = self.a.get("/api/runs/" + r.json()["run_id"]).json()
        self.assertEqual(run["status"], "complete", run)
        return run, body

    def test_upload_preview_analysis_confirmation_delete(self):
        aid = self.upload()
        preview = self.a.get("/api/attachments/" + aid).json()
        self.assertNotIn("original", preview)
        self.assertIn("Friday", preview["blocks"][0]["text"])
        run, _ = self.run_file(aid)
        self.assertEqual(run["result"]["kind"], "chat")
        self.assertNotIn("memory_proposal", run["result"])
        self.assertEqual(self.a.get(f"/api/people/{self.pid}").json()["events"], [])
        citation = run["result"]["attachment_sources"][0]
        payload = {
            "person_id": self.pid,
            "source_id": citation["source_id"],
            "quote": citation["quote"],
            "text": "Lin suggested Friday.",
            "kind": "account",
        }
        r = self.a.post(f"/api/runs/{run['id']}/attachment-memory", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(
            "chat.txt",
            self.a.get(f"/api/people/{self.pid}").json()["events"][0]["quote"],
        )
        self.assertEqual(
            self.a.post(
                f"/api/runs/{run['id']}/attachment-memory", json=payload
            ).status_code,
            400,
        )
        self.assertEqual(
            self.a.request("DELETE", "/api/attachments/" + aid, json={}).status_code,
            200,
        )
        self.assertEqual(self.a.get("/api/sessions/" + self.sid).status_code, 404)
        self.assertEqual(self.a.get(f"/api/people/{self.pid}").json()["events"], [])

    def test_cross_account_session_and_csrf_boundaries(self):
        aid = self.upload()
        self.assertEqual(self.b.get("/api/attachments/" + aid).status_code, 404)
        self.assertEqual(
            self.b.request("DELETE", "/api/attachments/" + aid, json={}).status_code,
            404,
        )
        sid2 = self.a.post("/api/sessions", json={}).json()["id"]
        r = self.a.post(
            "/api/message",
            json={
                "session_id": sid2,
                "message": "explain",
                "attachment_ids": [aid],
                "idempotency_key": "session-boundary-key",
            },
        )
        self.assertEqual(r.status_code, 400)
        self.a.headers["x-csrf-token"] = "invalid"
        self.assertEqual(
            self.a.post(
                "/api/attachments",
                json={"session_id": self.sid, "name": "a.txt", "content": "YQ=="},
            ).status_code,
            403,
        )

    def test_no_model_call_on_upload_and_idempotency_includes_files(self):
        with patch.object(
            self.app.state.service,
            "client",
            side_effect=AssertionError("upload called model"),
        ):
            aid = self.upload()
        run, body = self.run_file(aid)
        self.assertEqual(
            self.a.post("/api/message", json=body).json()["run_id"], run["id"]
        )
        body["attachment_ids"] = []
        self.assertEqual(self.a.post("/api/message", json=body).status_code, 400)
        stages = [t["stage"] for t in run["trace"] if t["event"] == "stage_completed"]
        self.assertEqual(stages, ["attachment_chat"])

    def test_pdf_upload_and_explicit_selection(self):
        aid = self.upload(pdf_bytes(), "sample.pdf")
        run, _ = self.run_file(aid, person=False)
        self.assertEqual(run["result"]["attachment_sources"][0]["page"], 1)
        self.assertEqual(len(run["person_ids"]), 0)
        r = self.a.post(
            "/api/message",
            json={
                "session_id": self.sid,
                "message": "hello",
                "idempotency_key": "without-files-key",
            },
        )
        rid = r.json()["run_id"]
        with self.db.read() as c:
            from relationship_agent.beta.database import runs

            state = c.execute(select(runs.c.state).where(runs.c.id == rid)).scalar()
        self.assertNotIn("attachment_context", state)

    def test_export_backup_restore_and_schema_version(self):
        from cryptography.fernet import Fernet

        aid = self.upload()
        exported = self.a.get("/api/export").json()
        self.assertEqual(exported["attachments"][0]["id"], aid)
        target = Database("sqlite:///" + self.tmp.name + "/restored.db")
        try:
            key = Fernet.generate_key()
            path = Path(self.tmp.name) / "backup.enc"
            backup(self.db, path, key)
            restore(target, path, key)
            with target.read() as c:
                self.assertEqual(c.execute(select(attachments.c.id)).scalar(), aid)
                self.assertEqual(
                    max(c.execute(select(versions.c.version)).scalars()), 2
                )
        finally:
            target.close()

    def test_larger_upload_keeps_other_request_limits(self):
        aid = self.upload(("Lin: Friday. " * 4000).encode())
        self.assertGreater(
            self.a.get("/api/attachments/" + aid).json()["characters"], 40000
        )
        response = self.a.post("/api/message", json={"junk": "x" * 41000})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            self.a.post("/api/attachments", json={"junk": "x" * 2900001}).status_code,
            413,
        )

    def test_bad_uploads_are_rate_limited(self):
        body = {"session_id": self.sid, "name": "bad.exe", "content": "YQ=="}
        for _ in range(20):
            self.assertEqual(
                self.a.post("/api/attachments", json=body).status_code, 400
            )
        self.assertEqual(self.a.post("/api/attachments", json=body).status_code, 409)

    def test_v1_database_upgrade_preserves_existing_session(self):
        from sqlalchemy import delete, insert

        with self.db.tx() as c:
            attachments.drop(c)
            c.execute(delete(versions))
            c.execute(insert(versions).values(version=1))
        self.db.migrate()
        self.assertEqual(self.a.get("/api/sessions/" + self.sid).status_code, 200)
        self.upload()
        with self.db.read() as c:
            self.assertEqual(max(c.execute(select(versions.c.version)).scalars()), 2)

    def test_long_input_keeps_model_context_bounded(self):
        import json
        from relationship_agent.demo import MockModelClient

        seen = []

        class Capture(MockModelClient):
            def complete_json(self, stage, payload, schema=None):
                seen.append((stage, payload))
                return super().complete_json(stage, payload, schema)

        self.app.state.service.client_factory = Capture
        aid = self.upload(("Lin: Friday. " * 6000).encode())
        response = self.a.post(
            "/api/message",
            json={
                "session_id": self.sid,
                "message": "x" * 6000,
                "attachment_ids": [aid],
                "idempotency_key": "long-context-key",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        service = self.app.state.service
        service.execute(service.claim())
        self.assertEqual(seen[0][0], "attachment_chat")
        self.assertLess(len(json.dumps(seen[0][1], ensure_ascii=False)), 24000)
        run = self.a.get("/api/runs/" + response.json()["run_id"]).json()
        self.assertEqual(run["status"], "complete")
        self.assertTrue(run["result"]["attachment_coverage"][0]["partial"])

    def test_failed_citation_can_resume_without_losing_selected_files(self):
        from relationship_agent.demo import MockModelClient

        broken = [True]

        class Flaky(MockModelClient):
            def complete_json(self, stage, payload, schema=None):
                result = super().complete_json(stage, payload, schema)
                if broken[0] and stage == "attachment_chat":
                    result["citations"][0]["quote"] = "fabricated quote"
                return result

        service = self.app.state.service
        service.client_factory = Flaky
        aid = self.upload()
        response = self.a.post(
            "/api/message",
            json={
                "session_id": self.sid,
                "message": "How to reply?",
                "attachment_ids": [aid],
                "idempotency_key": "retry-attachment-key",
            },
        )
        rid = response.json()["run_id"]
        service.execute(service.claim())
        self.assertEqual(self.a.get("/api/runs/" + rid).json()["status"], "failed")
        broken[0] = False
        self.assertEqual(
            self.a.post(f"/api/runs/{rid}/resume", json={}).status_code, 202
        )
        service.execute(service.claim())
        self.assertEqual(self.a.get("/api/runs/" + rid).json()["status"], "complete")

    def test_quote_tampering_cannot_become_memory(self):
        run, _ = self.run_file(self.upload())
        source = run["result"]["attachment_sources"][0]
        r = self.a.post(
            f"/api/runs/{run['id']}/attachment-memory",
            json={
                "person_id": self.pid,
                "source_id": source["source_id"],
                "quote": "invented",
                "text": "invented",
            },
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
