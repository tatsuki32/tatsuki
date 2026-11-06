from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase


class TestPlans(SandboxCase):
    def test_a_plan_is_left_for_the_calendar(self):
        h = load_tool("20-lifelog", "handler")
        for text in ("明日の12時にランチを友達と", "19時に夕食"):
            self.assertFalse(h.match(text), text)

    def test_records_still_come_here(self):
        h = load_tool("20-lifelog", "handler")
        for text in ("12時にランチを食べた", "体重65.2", "ベンチ60kg 10回3セット", "食事：明日のパン"):
            self.assertTrue(h.match(text), text)
