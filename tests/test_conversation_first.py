"""The default path answers once while retaining the harness boundaries."""

import unittest
from workflow_fixture import HarnessFixture
from relationship_agent.demo import MockModelClient
from relationship_agent.schemas import validate_stage, ContractError


class ConversationFirstTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HarnessFixture()
        self.addCleanup(self.fixture.close)
        self.sid = self.fixture.store.create_session("conversation-first")["id"]

    def test_combined_contract_rejects_missing_answer_and_premature_tool_answer(self):
        for value in (
            {"intent": "chat", "reason": "", "reply": ""},
            {
                "intent": "evidence",
                "reason": "",
                "reply": "I already searched the web.",
            },
            {"intent": "feedback", "reason": "", "reply": ""},
            {"intent": "delete_person", "reason": "", "reply": ""},
        ):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_stage("respond", value, {"has_report": False})

    def test_one_call_uses_confirmed_memory_and_history_without_search(self):
        class Capture(MockModelClient):
            def __init__(self):
                self.payloads = []

            def complete_json(self, stage, payload, schema=None):
                self.payloads.append((stage, payload))
                return super().complete_json(stage, payload, schema)

        class NoSearch:
            def search(self, *args, **kwargs):
                raise AssertionError("Ordinary conversation must not search")

        store = self.fixture.store
        person = store.save_person({"nickname": "小林"})
        store.save_event(person["id"], {"text": "小林上周提前告诉我他要改时间。"})
        client = Capture()
        agent = self.fixture.driver(client, NoSearch())
        agent.execute(agent.start(self.sid, "小林昨天说周末可以见面。", "auto"))
        message = "小林刚刚说周六上午十点见，我应该怎么回复？"
        rid = agent.start(self.sid, message, "auto")
        result = agent.execute(rid)
        self.assertEqual(
            [stage for stage, _ in client.payloads], ["respond", "respond"]
        )
        payload = client.payloads[-1][1]
        self.assertTrue(
            any("改时间" in item["text"] for item in payload["observations"])
        )
        self.assertTrue(
            any("周末可以见面" in turn["content"] for turn in payload["conversation"])
        )
        self.assertEqual(result["kind"], "chat")
        self.assertNotIn("hypotheses", result)
        agent.execute(rid)  # Completed runs must not bill again.
        self.assertEqual(len(client.payloads), 2)
        stages = [
            t["detail"]["stage"]
            for t in store.traces(rid)
            if t["event"] == "stage_completed"
        ]
        self.assertEqual(stages, ["respond"])

    def test_combined_call_failure_can_resume_without_fallback_to_mock(self):
        class FailOnce(MockModelClient):
            calls = 0

            def complete_json(self, stage, payload, schema=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("Injected interruption")
                return super().complete_json(stage, payload, schema)

        client = FailOnce()
        agent = self.fixture.driver(client)
        rid = agent.start(self.sid, "帮我想一句回复", "auto")
        with self.assertRaises(RuntimeError):
            agent.execute(rid)
        self.assertEqual(self.fixture.store.run(rid)["status"], "failed")
        self.assertEqual(agent.execute(rid)["kind"], "chat")
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
