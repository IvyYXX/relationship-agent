"""Regression coverage for the unified entry point and the real worker lifecycle.

ReleaseTests 启用真实后台 worker，通过 HTTP 轮询观察完成与恢复，
覆盖应用生命周期；模型仍用测试替身。另检查安装包内容、统一入口和队列限制。
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient
from sqlalchemy import select
from relationship_agent.beta.app import create_app
from relationship_agent.beta.config import Settings
from relationship_agent.beta.database import attempts
from relationship_agent.beta.store import Busy
from relationship_agent.demo import MockModelClient
from relationship_agent.providers import APIClient
from workflow_fixture import HarnessFixture
from test_harness import provider

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    """在真实 worker 生命周期下验证网页服务的完整请求流程。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(
            Settings(
                database_url="sqlite:///" + self.temp.name + "/release.sqlite",
                origin="http://testserver",
            )
        )
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.headers["origin"] = "http://testserver"
        response = self.client.post(
            "/api/auth/register",
            json={
                "username": "release_user",
                "password": "synthetic-password-123",
                "adult": True,
                "consent": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.client.headers["x-csrf-token"] = response.json()["csrf"]

    def person(self, name="小林"):
        response = self.client.post("/api/people", json={"nickname": name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def session(self):
        response = self.client.post("/api/sessions", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def submit(self, message, sid=None, pid=None, key=None):
        response = self.client.post(
            "/api/message",
            json={
                "session_id": sid or self.session(),
                "message": message,
                "person_id": pid,
                "idempotency_key": key or uuid.uuid4().hex,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["run_id"]

    def wait(self, rid, status="complete"):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            run = self.client.get("/api/runs/" + rid).json()
            if run["status"] not in ("queued", "running"):
                self.assertEqual(run["status"], status, run)
                return run
            time.sleep(0.02)
        self.fail("Background worker did not finish")

    def test_startup_assets_and_retired_endpoints(self):
        for path in ("/", "/app.js", "/style.css", "/healthz", "/readyz"):
            self.assertEqual(self.client.get(path).status_code, 200)
        page = self.client.get("/")
        self.assertIn("Persona", page.text)
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        for path in (
            "/api/bootstrap",
            "/api/settings",
            "/api/analyze",
            "/api/chat",
            "/api/knowledge",
        ):
            response = (
                self.client.get(path)
                if path == "/api/bootstrap"
                else self.client.post(path, json={})
            )
            self.assertEqual(response.status_code, 404, path)

    def test_single_endpoint_routes_chat_knowledge_analysis_and_feedback(self):
        sid = self.session()
        for message, kind in [
            ("我很焦虑，只想聊聊", "chat"),
            ("什么是依恋？", "knowledge"),
            ("他一直没有联系我，请详细分析", "analysis"),
            ("后来他说他有新伴侣了，请更新判断。", "feedback"),
        ]:
            self.assertEqual(
                self.wait(self.submit(message, sid))["result"]["kind"], kind
            )
        saved = self.client.get("/api/sessions/" + sid).json()["runs"]
        self.assertEqual(len(saved), 4)
        self.assertEqual(
            saved[-1]["result"]["current_hypotheses"][1]["status"], "supported"
        )

    def test_confirm_recall_correct_delete_over_http(self):
        pid = self.person()
        created = self.wait(self.submit("小林昨天取消了约会。", pid=pid))["result"]
        proposal = created["memory_proposal"]
        before = self.wait(self.submit("小林之前做过什么？", pid=pid))
        self.assertNotIn("你之前确认记录过", before["result"]["reply"])
        path = f"/api/people/{pid}/events/{proposal['id']}"
        self.assertEqual(
            self.client.patch(path, json={"text": proposal["text"]}).status_code, 200
        )
        recalled = self.wait(self.submit("小林之前做过什么？", pid=pid))
        self.assertIn("取消了约会", recalled["result"]["reply"])
        self.assertEqual(
            self.client.patch(path, json={"text": "更正：是我先改了时间"}).status_code,
            200,
        )
        corrected = self.wait(self.submit("小林之前做过什么？", pid=pid))
        self.assertIn("是我先改了时间", corrected["result"]["reply"])
        self.assertNotIn("取消了约会", corrected["result"]["reply"])
        self.assertEqual(
            self.client.request(
                "DELETE", "/api/events/" + proposal["id"], json={}
            ).status_code,
            200,
        )
        after = self.wait(self.submit("小林之前做过什么？", pid=pid))
        self.assertNotIn("是我先改了时间", after["result"]["reply"])
        self.assertNotIn("取消了约会", self.client.get("/api/export").text)

    def test_retry_cannot_change_selected_person(self):
        first, second = self.person(), self.person("阿泽")
        sid, key = self.session(), uuid.uuid4().hex
        rid = self.submit("今天有点难过", sid, first, key)
        self.wait(rid)
        self.assertEqual(self.submit("今天有点难过", sid, first, key), rid)
        response = self.client.post(
            "/api/message",
            json={
                "session_id": sid,
                "message": "今天有点难过",
                "person_id": second,
                "idempotency_key": key,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_model_failure_is_sanitized_and_resume_uses_live_worker(self):
        healthy = False

        def responder(body, count):
            if not healthy:
                return 401, {"error": "fixture-provider-secret"}
            stage = body["messages"][0]["content"].split("Stage: ")[1].split("\n")[0]
            payload = json.loads(body["messages"][-1]["content"])
            value = MockModelClient().complete_json(stage, payload)
            return 200, {
                "choices": [
                    {"message": {"content": json.dumps(value, ensure_ascii=False)}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }

        with provider(responder) as (url, calls):
            self.app.state.service.client_factory = lambda: APIClient(
                "fixture-provider-secret", "fixture-model", url, "chat-completions"
            )
            rid = self.submit("我有点难过，只想聊聊")
            run = self.wait(rid, "failed")
            self.assertNotIn("fixture-provider-secret", json.dumps(run))
            healthy = True
            self.assertEqual(
                self.client.post("/api/runs/" + rid + "/resume", json={}).status_code,
                202,
            )
            self.wait(rid)
            self.assertEqual(
                len(calls), 2
            )  # Failed combined call, resumed combined answer.
            with self.app.state.db.read() as connection:
                self.assertEqual(len(connection.execute(select(attempts)).all()), 2)

    def test_blank_and_oversized_input_rejected(self):
        sid = self.session()
        for message, expected in [(" ", 400), ("x" * 6001, 422)]:
            response = self.client.post(
                "/api/message",
                json={
                    "session_id": sid,
                    "message": message,
                    "idempotency_key": uuid.uuid4().hex,
                },
            )
            self.assertEqual(response.status_code, expected)


class PackagingAndQueueTests(unittest.TestCase):
    """验证发布内容、入口兼容性与队列资源限制。"""

    def test_wheel_check_rejects_stale_modules(self):
        spec = importlib.util.spec_from_file_location(
            "build_release", ROOT / "scripts/build_release.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stale.whl"
            with ZipFile(path, "w") as wheel:
                wheel.writestr("relationship_agent/storage.py", "# retired")
            with self.assertRaisesRegex(ValueError, "Retired/private"):
                module.verify_wheel(path)

    def test_no_retired_imports_or_assets(self):
        retired = {"storage", "server", "cli", "mcp_server", "extractor"}
        for path in (ROOT / "src" / "relationship_agent").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        node.module,
                        {"relationship_agent." + name for name in retired},
                        str(path),
                    )
                    if path.parent.name == "relationship_agent" and node.level == 1:
                        self.assertNotIn(node.module, retired, str(path))
        for name in retired:
            self.assertFalse(
                (ROOT / "src" / "relationship_agent" / (name + ".py")).exists()
            )
        self.assertFalse(any((ROOT / "src" / "relationship_agent" / "web").glob("*")))

    def test_all_web_launchers_expose_new_app(self):
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for args in (["web_app.py"], ["persona_app.py"], ["-m", "relationship_agent"]):
            result = subprocess.run(
                [sys.executable, *args, "--help"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Persona AI", result.stdout)

    def test_resume_respects_three_active_jobs(self):
        with HarnessFixture() as fixture:
            driver = fixture.driver()
            sid = fixture.store.create_session()["id"]
            rid = driver.start(sid, "hello", "auto")
            driver.service.claim()
            fixture.store.fail(rid, "synthetic interruption")
            for _ in range(3):
                driver.start(fixture.store.create_session()["id"], "hello", "auto")
            with self.assertRaises(Busy):
                driver.service.resume(fixture.uid, rid)
            self.assertEqual(fixture.store.run(rid)["status"], "failed")

    def test_worker_start_is_idempotent_and_stop_drains_idle_workers(self):
        with HarnessFixture() as fixture:
            service = fixture.driver().service
            service.start()
            initial = list(service.threads)
            service.start()
            self.assertEqual(service.threads, initial)
            service.stop()
            self.assertTrue(all(not thread.is_alive() for thread in initial))
            service.start()
            service.stop()
            self.assertTrue(all(not thread.is_alive() for thread in service.threads))


if __name__ == "__main__":
    unittest.main()
