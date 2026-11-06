from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase


class TestRecords(SandboxCase):
    def test_records_come_here_with_or_without_a_time(self):
        h = load_tool("20-lifelog", "handler")
        for text in ("19時に夕食", "12時にランチを食べた", "体重65.2", "ベンチ60kg 10回3セット", "食事：明日のパン"):
            self.assertTrue(h.match(text), text)
