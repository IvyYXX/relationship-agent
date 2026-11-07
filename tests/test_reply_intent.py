"""Regressions for support-plus-advice and opt-in simulation."""

import unittest
from workflow_fixture import HarnessFixture
from relationship_agent.demo import MockModelClient
from relationship_agent.routing import demo_route


class ReplyIntentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HarnessFixture()
        self.addCleanup(self.fixture.close)
        self.sid = self.fixture.store.create_session("reply-regression")["id"]

    def test_support_and_advice_use_one_model_call(self):
        class Recording(MockModelClient):
            def __init__(self):
                self.calls = []

            def complete_json(self, stage, payload, schema=None):
                self.calls.append(stage)
                return super().complete_json(stage, payload, schema)

        for message in (
            "你能安抚我现在对这个工作没安排好，然后给我点策略吗",
            "Comfort me about my disorganized work and give me a few strategies.",
            "我很焦虑，不知道怎么办，给我点建议。",
            "怎么回复他，简单一点",
        ):
            with self.subTest(message=message):
                client = Recording()
                agent = self.fixture.driver(client)
                sid = self.fixture.store.create_session("advice")["id"]
                result = agent.execute(agent.start(sid, message, "auto"))
                self.assertEqual(result["kind"], "chat")
                self.assertEqual(client.calls, ["respond"])
                self.assertNotIn("dialogue_rehearsal", result)

    def test_analysis_does_not_implicitly_create_simulation(self):
        agent = self.fixture.driver(MockModelClient())
        rid = agent.start(
            self.sid, "他一直没有联系我，请详细分析，但不要演练。", "auto"
        )
        result = agent.execute(rid)
        self.assertEqual(result["kind"], "analysis")
        self.assertIsNone(result["dialogue_rehearsal"])
        self.assertNotIn(
            "simulation", {r["layer"] for r in self.fixture.records(self.sid)}
        )
        stages = [
            t["detail"]["stage"]
            for t in self.fixture.store.traces(rid)
            if t["event"] == "stage_completed"
        ]
        self.assertNotIn("dialogue", stages)

    def test_explicit_rehearsal_still_works(self):
        agent = self.fixture.driver(MockModelClient())
        result = agent.execute(agent.start(self.sid, "请帮我演练一次沟通。", "auto"))
        self.assertEqual(result["routing"]["intent"], "rehearsal")
        self.assertTrue(result["dialogue_rehearsal"])
        self.assertIn(
            "simulation", {r["layer"] for r in self.fixture.records(self.sid)}
        )
        self.assertEqual(
            demo_route("Please rehearse a conversation")["intent"], "rehearsal"
        )

    def test_rehearsal_does_not_propose_real_person_memory(self):
        self.fixture.store.save_person({"nickname": "小雨"})
        agent = self.fixture.driver(MockModelClient())
        result = agent.execute(agent.start(self.sid, "请和小雨练习对话。", "auto"))
        self.assertTrue(result["dialogue_rehearsal"])
        self.assertNotIn("memory_proposal", result)

    def test_useful_advice_without_empathy_keywords_is_not_overwritten(self):
        class Answer(MockModelClient):
            calls = []

            def complete_json(self, stage, payload, schema=None):
                self.calls.append(stage)
                return {
                    "intent": "chat",
                    "reason": "Support and advice",
                    "reply": "事情堆在一起确实会让人喘不过气。先写下今天必须交的那一项，只做十分钟，再决定下一步。",
                }

        client = Answer()
        agent = self.fixture.driver(client)
        result = agent.execute(
            agent.start(self.sid, "我很焦虑，安慰我并给点建议。", "auto")
        )
        self.assertIn("只做十分钟", result["reply"])
        self.assertNotIn("此刻最压着你的是什么", result["reply"])
        self.assertEqual(client.calls, ["respond"])


if __name__ == "__main__":
    unittest.main()
