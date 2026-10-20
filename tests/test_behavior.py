import unittest

from chris.behavior.engine import BehaviorAction, RuleBasedBehaviorEngine


class TestRuleBasedBehaviorEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RuleBasedBehaviorEngine()

    def test_respond_on_user_message(self):
        decision = self.engine.decide({"has_user_message": True})
        self.assertEqual(decision.action, BehaviorAction.RESPOND)

    def test_no_action_by_default(self):
        decision = self.engine.decide({})
        self.assertEqual(decision.action, BehaviorAction.NO_ACTION)

    def test_remind_when_proactive_and_long_idle(self):
        decision = self.engine.decide({"proactive_enabled": True, "idle_hours": 48})
        self.assertEqual(decision.action, BehaviorAction.REMIND)

    def test_no_remind_when_proactive_disabled(self):
        decision = self.engine.decide({"proactive_enabled": False, "idle_hours": 48})
        self.assertEqual(decision.action, BehaviorAction.NO_ACTION)

    def test_no_remind_when_not_idle_enough(self):
        decision = self.engine.decide({"proactive_enabled": True, "idle_hours": 1})
        self.assertEqual(decision.action, BehaviorAction.NO_ACTION)


if __name__ == "__main__":
    unittest.main()
