"""Keep the user's question and earlier reports available across workflows."""

import unittest
from workflow_fixture import HarnessFixture
from relationship_agent.demo import MockModelClient
from relationship_agent.schemas import validate_stage, ContractError
from relationship_agent.routing import demo_route


class QuestionFirstTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HarnessFixture()
        self.addCleanup(self.fixture.close)
        self.sid = self.fixture.store.create_session("question-first")["id"]

    def test_ordinary_analysis_and_updates_are_conversation(self):
        for message in (
            "你可以帮我分析这个男生性格特点吗",
            "帮我分析下，他这个举动是什么意思",
            "Can you analyze his behavior?",
            "后来他说周末可以见面，我怎么回复",
        ):
            with self.subTest(message=message):
                self.assertEqual(demo_route(message, has_report=True)["intent"], "chat")
        self.assertEqual(demo_route("请出一份详细分析报告")["intent"], "analysis")
        self.assertEqual(demo_route("请根据新消息更新判断", True)["intent"], "feedback")

    def test_user_background_survives_analysis_and_rehearsal(self):
        agent = self.fixture.driver(MockModelClient())
        message = "他答应周五帮我确认进度，周五没回复，周六解释说忘了。"
        report = agent.execute(agent.start(self.sid, message, "rehearsal"))
        conversation = self.fixture.store.conversation(self.sid)
        self.assertEqual(conversation, [{"role": "user", "content": message}])
        self.assertNotIn(report["dialogue_rehearsal"]["opening"], str(conversation))
        rid = agent.start(self.sid, "你可以帮我分析这个男生性格特点吗", "auto")
        self.assertEqual(
            self.fixture.store.run(rid)["state"]["conversation"], conversation
        )
        result = agent.execute(rid)
        self.assertEqual(result["kind"], "chat")
        self.assertNotIn("hypotheses", result)
        self.assertEqual(self.fixture.store.context(self.sid), [])

    def test_explicit_report_receives_earlier_user_reports(self):
        class Capture(MockModelClient):
            def __init__(self):
                self.payloads = {}

            def complete_json(self, stage, payload, schema=None):
                self.payloads[stage] = payload
                return super().complete_json(stage, payload, schema)

        client = Capture()
        agent = self.fixture.driver(client)
        original = "他答应周五确认进度，但没有回复。"
        agent.execute(agent.start(self.sid, original, "chat"))
        agent.execute(agent.start(self.sid, "请结合之前的事给出详细分析报告。", "auto"))
        for stage in ("hypotheses", "actions"):
            self.assertEqual(
                client.payloads[stage]["user_history"],
                [{"role": "user", "content": original}],
            )

    def test_history_support_accepts_only_user_quotes(self):
        payload = {
            "user_message": "请分析",
            "observations": [],
            "user_history": [
                {"role": "user", "content": "他答应周五回复。"},
                {"role": "assistant", "content": "他一定在撒谎。"},
            ],
        }
        hypothesis = {
            "id": "h1",
            "explanation": "暂不确定",
            "status": "possible",
            "support": ["他答应周五回复。"],
            "limitations": [],
            "check": "",
        }
        validate_stage("hypotheses", {"items": [hypothesis]}, payload)
        hypothesis["support"] = ["他一定在撒谎。"]
        with self.assertRaises(ContractError):
            validate_stage("hypotheses", {"items": [hypothesis]}, payload)
        validate_stage("hypotheses", {"items": []}, {"user_message": "请分析"})


if __name__ == "__main__":
    unittest.main()
