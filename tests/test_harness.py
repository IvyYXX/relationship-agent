"""Integration tests for evidence boundaries, persistence, recovery and protocols.

重点检查执行器的状态与来源边界：失败后从检查点恢复、演练不变成经历、
引用必须来自输入。ProviderTests 用本机 HTTP 服务模拟两种模型协议，
因此需要本地网络权限，但不连接真实模型服务。
"""

import json
import sys
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from relationship_agent.demo import MockModelClient
from relationship_agent.providers import APIClient, ModelError
from relationship_agent.schemas import validate_stage, ContractError
from relationship_agent.beta.store import ScopedStore
from workflow_fixture import HarnessFixture


class HarnessTests(unittest.TestCase):
    """验证执行阶段、持久化检查点和证据来源之间的一致性。"""

    def test_auto_routes_and_preserves_cross_workflow_history(self):
        for message, intent in [
            ("我很焦虑，只想聊聊，不想分析", "chat"),
            ("什么是依恋？", "knowledge"),
            ("他一直没有联系我，请详细分析", "analysis"),
            ("后来他说他有新伴侣了，请更新判断。", "feedback"),
        ]:
            result = self.agent.execute(self.agent.start(self.sid, message, "auto"))
            self.assertEqual(result["kind"], intent)
            self.assertEqual(result["routing"]["intent"], intent)
        self.assertEqual(len(self.store.conversation(self.sid)), 6)
        # User reports survive workflow changes; generated reports/rehearsals stay excluded.
        self.assertEqual(self.store.latest_report(self.sid)["kind"], "feedback")

    def test_route_contract_rejects_bad_intent_and_unfounded_feedback(self):
        for intent in ["delete", "feedback"]:
            with self.assertRaises(ContractError):
                validate_stage(
                    "route", {"intent": intent, "reason": "test"}, {"has_report": False}
                )

    def test_auto_resume_does_not_repeat_router(self):
        class InterruptOnce(MockModelClient):
            calls = []

            def complete_json(inner, stage, payload, schema=None):
                inner.calls.append(stage)
                if stage == "knowledge" and inner.calls.count("knowledge") == 1:
                    raise RuntimeError("interrupted")
                return super().complete_json(stage, payload, schema)

        client = InterruptOnce()
        agent = self.fixture.driver(client)
        rid = agent.start(self.sid, "什么是依恋？", "auto")
        with self.assertRaises(RuntimeError):
            agent.execute(rid)
        self.assertEqual(agent.execute(rid)["kind"], "knowledge")
        self.assertEqual(client.calls.count("respond"), 1)

    def test_duplicate_reply_repair_preserves_the_new_request(self):
        class Repeater(MockModelClient):
            calls = []

            def complete_json(self, stage, payload, schema=None):
                self.calls.append(stage)
                if stage == "respond":
                    return {
                        "intent": "chat",
                        "reason": "conversation",
                        "reply": "先确认明天的截止时间。",
                    }
                if stage == "chat":
                    self.repair = payload
                    return {
                        "reply": "可以发：我今天先完成甲任务，乙任务能否调整到明天下午？"
                    }
                return super().complete_json(stage, payload, schema)

        client = Repeater()
        agent = self.fixture.driver(client)
        agent.execute(agent.start(self.sid, "任务太多了怎么办", "auto"))
        result = agent.execute(
            agent.start(self.sid, "帮我写一句确认优先级的话", "auto")
        )
        self.assertIn("可以发", result["reply"])
        self.assertEqual(client.calls, ["respond", "respond", "chat"])
        self.assertIn("确认优先级", client.repair["user_message"])

    def test_persistent_duplicate_fails_instead_of_fabricating_a_generic_answer(self):
        class Repeater(MockModelClient):
            def complete_json(self, stage, payload, schema=None):
                if stage == "respond":
                    return {
                        "intent": "chat",
                        "reason": "conversation",
                        "reply": "同一句回复",
                    }
                if stage == "chat":
                    return {"reply": "同一句回复"}
                return super().complete_json(stage, payload, schema)

        agent = self.fixture.driver(Repeater())
        agent.execute(agent.start(self.sid, "今天工作很忙", "auto"))
        rid = agent.start(self.sid, "帮我写一句确认优先级的话", "auto")
        with self.assertRaisesRegex(RuntimeError, "repeated"):
            agent.execute(rid)
        self.assertEqual(self.store.run(rid)["status"], "failed")

    def test_knowledge_retrieval_and_memory_boundary(self):
        self.analyze()
        result = self.agent.execute(
            self.agent.start(self.sid, "什么是不确定性焦虑？", "knowledge")
        )
        self.assertEqual(result["retrieved"][0]["id"], "uncertainty")
        self.assertTrue(result["sources"])
        self.assertEqual(result["memory_used"], [])
        self.assertFalse(
            any("什么是不确定性" in x["text"] for x in self.store.context(self.sid))
        )
        self.assertTrue(result["retrieved"][0]["text"])

    def test_knowledge_english_and_missing_topic(self):
        from relationship_agent.knowledge import search

        self.assertEqual(search("What is attachment?")[0]["id"], "attachment")
        self.assertEqual(search("量子计算芯片"), [])
        result = self.agent.execute(
            self.agent.start(self.sid, "量子计算芯片", "knowledge")
        )
        self.assertEqual(result["sources"], [])
        self.assertIn("没有覆盖", result["answer"])

    def test_knowledge_rejects_invented_citations(self):
        from relationship_agent.knowledge import search

        docs = search("依恋")
        value = {
            "answer": "test",
            "sources": [
                {
                    "title": docs[0]["title"],
                    "source": docs[0]["source"],
                    "snippet": "fabricated evidence",
                }
            ],
        }
        with self.assertRaises(ContractError):
            validate_stage("knowledge", value, {"retrieved": docs})

    def setUp(self):
        self.fixture = HarnessFixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.client = MockModelClient()
        self.agent = self.fixture.driver(self.client)
        self.sid = self.store.create_session("test")["id"]

    def analyze(self, message="他一直没有联系我。我不知道是不是因为他有其他人了。"):
        rid = self.agent.start(self.sid, message)
        return self.agent.execute(rid)

    def test_suspicion_not_fact(self):
        result = self.analyze()
        self.assertEqual(result["evidence"]["facts"], [{"quote": "他一直没有联系我"}])
        self.assertTrue(result["evidence"]["assumptions"])
        self.assertTrue(result["is_demo"])

    def test_unrelated_input_does_not_invent_default_events(self):
        result = self.analyze("我们因为家务吵架，我很生气。")
        self.assertEqual(result["evidence"]["facts"], [])
        self.assertEqual(result["evidence"]["feelings"], ["我很生气"])

    def test_context_never_contains_generated_rehearsal(self):
        self.analyze()
        context = self.store.context(self.sid)
        self.assertEqual(context, [])
        # Raw chat logs and simulations do not become confirmed person memory.
        self.assertNotIn("practice_reply", json.dumps(context))
        self.assertEqual(
            {r["layer"] for r in self.fixture.records(self.sid)},
            {"observation", "inference", "simulation"},
        )

    def test_other_session_cannot_leak(self):
        self.analyze("secret in session one")
        other = self.store.create_session("other", "mock")["id"]
        self.assertEqual(self.store.context(other), [])

    def test_model_configuration_mismatch_rejected(self):
        rid = self.agent.start(self.sid, "hello")
        self.agent.service.execute(self.agent.service.claim())
        # A failure made resumable cannot be replayed against a different model.
        self.store.fail(rid, "synthetic failure")
        self.client.model = "different-model"
        with self.assertRaises(ValueError):
            self.agent.service.resume(self.fixture.uid, rid)

    def test_feedback_changes_existing_hypothesis_with_provenance(self):
        self.analyze()
        result = self.agent.execute(
            self.agent.start(self.sid, "他说他有新伴侣了。", "feedback")
        )
        h = next(h for h in result["current_hypotheses"] if h["id"] == "h2")
        self.assertEqual(h["status"], "supported")
        self.assertEqual(h["last_update"]["quote"], "他说他有新伴侣了")
        self.assertEqual(len(self.fixture.records(self.sid, "observation")), 2)
        self.assertNotIn("reflection", json.dumps(self.store.context(self.sid)))

    def test_restart_restores_latest_beliefs(self):
        self.analyze()
        self.agent.execute(self.agent.start(self.sid, "他说他有新伴侣了。", "feedback"))
        reopened = ScopedStore(self.fixture.db, self.fixture.uid)
        self.assertEqual(
            reopened.latest_report(self.sid)["current_hypotheses"][1]["status"],
            "supported",
        )
        agent = self.fixture.driver(self.client)
        rid = agent.start(self.sid, "现在我想整理一下自己的需要。")
        self.assertEqual(
            reopened.run(rid)["state"]["previous_judgments"][1]["status"], "supported"
        )
        resumed = agent.execute(rid)
        self.assertEqual(resumed["current_hypotheses"][1]["status"], "supported")
        self.assertIn("last_update", resumed["current_hypotheses"][1])

    def test_feedback_requires_history(self):
        with self.assertRaises(ValueError):
            self.agent.start(self.sid, "真实反馈", "feedback")

    def test_completed_run_is_idempotent(self):
        result = self.analyze()
        again = self.agent.execute(result["run_id"])
        self.assertEqual(result, again)
        self.assertEqual(len(self.fixture.records(self.sid)), 3)

    def test_failure_resume_skips_completed_calls(self):
        class Failing(MockModelClient):
            calls = []
            fail = True

            def complete_json(self, stage, payload, schema=None):
                self.calls.append(stage)
                if stage == "actions" and self.fail:
                    raise ModelError("temporary failure")
                return super().complete_json(stage, payload, schema)

        client = Failing()
        agent = self.fixture.driver(client)
        rid = agent.start(self.sid, "他一直没有联系我")
        with self.assertRaises(RuntimeError):
            agent.execute(rid)
        self.assertEqual(self.store.run(rid)["status"], "failed")
        self.assertEqual(self.fixture.records(self.sid), [])
        client.fail = False
        agent.execute(rid)
        self.assertEqual(
            client.calls, ["extract", "hypotheses", "actions", "actions", "dialogue"]
        )
        self.assertEqual(len(self.fixture.records(self.sid)), 3)

    def test_context_bound(self):
        person = self.store.save_person({"nickname": "Known person"})
        self.store.person_ids = [person["id"]]
        for i in range(8):
            self.store.save_event(person["id"], {"text": str(i) + "A" * 2500})
        self.assertGreater(len(self.store.context(self.sid)), 1)
        self.assertLessEqual(
            len(json.dumps(self.store.context(self.sid), ensure_ascii=False)), 6000
        )

    def test_english_output_and_gender_swap(self):
        def output(gender):
            sid = self.store.create_session(gender, "mock", "en")["id"]
            return self.agent.execute(
                self.agent.start(sid, f"we broke up and {gender} hasn't contacted me")
            )

        male, female = output("he"), output("she")
        self.assertEqual(male["hypotheses"], female["hypotheses"])
        self.assertEqual(male["actions"], female["actions"])
        self.assertIn("Private rehearsal", male["dialogue_rehearsal"]["opening"])

    def test_rehearsal_matches_selected_action(self):
        r = self.analyze()
        self.assertEqual(
            r["actions"]["recommended_id"], r["dialogue_rehearsal"]["action_id"]
        )
        self.assertTrue(r["dialogue_rehearsal"]["opening"].startswith("仅在心里练习"))

    def test_fabricated_quote_rejected(self):
        payload = {"user_message": "我们分开了", "language": "zh"}
        value = self.client.complete_json("extract", payload)
        value["facts"] = [{"quote": "我们仍然每天聊天"}]
        with self.assertRaises(ContractError):
            validate_stage("extract", value, payload)

    def test_generated_question_cannot_support_hypothesis(self):
        payload = {
            "user_message": "我们约定一周不联系。",
            "observations": [],
            "evidence": {
                "facts": [{"quote": "我们约定一周不联系。"}],
                "feelings": [],
                "assumptions": [],
                "unknowns": [],
                "questions": ["你们为什么约定？"],
            },
        }
        value = self.client.complete_json("hypotheses", payload)
        value["items"][0]["support"] = ["你们为什么约定？"]
        with self.assertRaises(ContractError):
            validate_stage("hypotheses", value, payload)

    def test_wrong_type_and_unknown_update_rejected(self):
        payload = {"user_message": "test"}
        value = self.client.complete_json("extract", payload)
        value["feelings"] = "not a list"
        with self.assertRaises(ContractError):
            validate_stage("extract", value, payload)
        with self.assertRaises(ContractError):
            validate_stage(
                "feedback",
                {
                    "updates": [
                        {
                            "hypothesis_id": "h99",
                            "status": "supported",
                            "reason": "x",
                            "quote": "x",
                        }
                    ],
                    "reflection": "",
                    "next_steps": [],
                },
                {"hypotheses": [], "feedback": "x"},
            )

    def test_no_updates_without_new_evidence(self):
        self.analyze()
        r = self.agent.execute(self.agent.start(self.sid, "我还是在想他。", "feedback"))
        self.assertEqual(r["update"]["updates"], [])
        self.assertTrue(all(h["status"] == "unknown" for h in r["current_hypotheses"]))


@contextmanager
def provider(responder):
    """用调用者提供的响应逻辑启动本地模型替身，退出时关闭线程与端口。"""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(
                {"path": self.path, "body": body, "headers": dict(self.headers)}
            )
            status, result = responder(body, len(calls))
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ProviderTests(unittest.TestCase):
    """通过本地 HTTP 替身检查请求协议和有限重试。"""

    def test_api_full_loop_and_feedback_with_local_provider(self):

        def responder(body, count):
            system = body.get("system") or body["messages"][0]["content"]
            stage = system.split("Stage: ")[1].split("\n", 1)[0]
            payload = json.loads(body["messages"][-1]["content"])
            value = MockModelClient().complete_json(stage, payload)
            content = json.dumps(value, ensure_ascii=False)
            return 200, {"content": [{"type": "text", "text": content}]}

        with provider(responder) as (url, calls), HarnessFixture() as fixture:
            agent = fixture.driver(APIClient("fixture-key", "fixture-model", url))
            sid = fixture.store.create_session("fixture")["id"]
            result = agent.execute(agent.start(sid, "他一直没有联系我"))
            self.assertFalse(result["is_demo"])
            result = agent.execute(agent.start(sid, "他说他有新伴侣了。", "feedback"))
            self.assertEqual(result["current_hypotheses"][1]["status"], "supported")
            self.assertEqual(len(calls), 5)

    def test_both_protocols_use_expected_auth_and_paths(self):
        payload = {"user_message": "他一直没有联系我", "language": "zh"}
        expected = MockModelClient().complete_json("extract", payload)
        for protocol in ("anthropic", "chat-completions"):

            def respond(body, count):
                content = json.dumps(expected, ensure_ascii=False)
                return 200, (
                    {
                        "content": [{"type": "text", "text": content}],
                        "usage": {"input_tokens": 5},
                    }
                    if protocol == "anthropic"
                    else {
                        "choices": [{"message": {"content": content}}],
                        "usage": {"prompt_tokens": 5},
                    }
                )

            with provider(respond) as (url, calls):
                c = APIClient("test-key", "custom-model", url, protocol)
                self.assertEqual(c.complete_json("extract", payload), expected)
                self.assertEqual(
                    calls[0]["path"],
                    "/v1/messages" if protocol == "anthropic" else "/chat/completions",
                )
                headers = {k.lower(): v for k, v in calls[0]["headers"].items()}
                self.assertEqual(
                    (
                        headers["x-api-key"]
                        if protocol == "anthropic"
                        else headers["authorization"]
                    ),
                    "test-key" if protocol == "anthropic" else "Bearer test-key",
                )
                self.assertNotIn("test-key", repr(c))

    def test_malformed_json_repair_is_bounded(self):
        with provider(
            lambda b, n: (200, {"content": [{"type": "text", "text": "not-json"}]})
        ) as (url, calls):
            with self.assertRaises(ModelError):
                APIClient("key", "m", url).complete_json(
                    "extract", {"user_message": "x"}
                )
            self.assertEqual(len(calls), 2)

    def test_key_error_not_retried_or_echoed(self):
        with provider(lambda b, n: (401, {"error": "secret-test-key"})) as (url, calls):
            with self.assertRaises(ModelError) as caught:
                APIClient("secret-test-key", "m", url).complete_json(
                    "extract", {"user_message": "x"}
                )
            self.assertNotIn("secret-test-key", str(caught.exception))
            self.assertEqual(len(calls), 1)

    def test_local_request_limit_blocks_extra_provider_calls(self):
        payload = {
            "language": "zh",
            "user_message": "只想聊聊",
            "conversation": [],
            "observations": [],
        }
        expected = MockModelClient().complete_json("chat", payload)

        def respond(body, count):
            return 200, {
                "choices": [
                    {"message": {"content": json.dumps(expected, ensure_ascii=False)}}
                ]
            }

        with provider(respond) as (url, calls):
            client = APIClient(
                "fixture-key", "glm-5.3", url, "chat-completions", request_limit=1
            )
            self.assertEqual(client.complete_json("chat", payload), expected)
            with self.assertRaises(ModelError):
                client.complete_json("chat", payload)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["body"]["reasoning_effort"], "low")

    def test_gpt_preset_uses_chat_completions_without_glm_parameter(self):
        payload = {
            "language": "zh",
            "user_message": "只想聊聊",
            "conversation": [],
            "observations": [],
        }
        expected = MockModelClient().complete_json("chat", payload)
        with provider(
            lambda body, count: (
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(expected, ensure_ascii=False)
                            }
                        }
                    ]
                },
            )
        ) as (url, calls):
            client = APIClient(
                "openai-test-key", "gpt-4.1-mini", url, "chat-completions"
            )
            self.assertEqual(client.complete_json("chat", payload), expected)
            self.assertEqual(calls[0]["path"], "/chat/completions")
            self.assertNotIn("reasoning_effort", calls[0]["body"])
            self.assertEqual(calls[0]["body"]["model"], "gpt-4.1-mini")
            self.assertEqual(
                calls[0]["headers"]["Authorization"], "Bearer openai-test-key"
            )

    def test_url_validation_and_endpoint_normalization(self):
        with self.assertRaises(ValueError):
            APIClient("key", "m", "http://example.com")
        with self.assertRaises(ValueError):
            APIClient("key", "m", "https://user:password@example.com")
        self.assertEqual(
            APIClient("key", "m", "https://example.com/v1").endpoint,
            "https://example.com/v1/messages",
        )


if __name__ == "__main__":
    unittest.main()
