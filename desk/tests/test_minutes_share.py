"""Minutes → Google Doc and thank-you draft: the parts that need neither Google nor Gmail."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('MINUTES_DIR', __import__('tempfile').mkdtemp(prefix='desk-minutes-test-'))  # never the real minutes
import minutes_share  # noqa: E402
import minutes  # noqa: E402
from unittest import mock  # noqa: E402


class Share(unittest.TestCase):
    def test_markdown_to_html(self):
        page = minutes_share.md_html('## 決まったこと\n- **採用**：A <b>\n  - 下の点\n\n## アクション\n| 何を | 誰が | いつまで |\n|---|---|---|\n| 送る | 自分 | 2026-11-25 |')
        self.assertIn('<h2>決まったこと</h2>', page)
        self.assertIn('<b>採用</b>：A &lt;b&gt;', page)
        self.assertIn('<ul><li>下の点</li></ul>', page)
        self.assertIn('<th>何を</th>', page)
        self.assertIn('<td>2026-11-25</td>', page)
        self.assertNotIn('---', page)

    def test_names_from_the_who_line(self):
        self.assertEqual(minutes_share.names({'body': '## 会議の情報\n- 参加者：山田、佐藤、自分'}), ['山田', '佐藤'])
        self.assertEqual(minutes_share.names({'body': '- Who: Ken, Speaker A'}), ['Ken'])
        self.assertEqual(minutes_share.names({'body': '- 参加者：記録から分かりません'}), [])

    def test_name_matches_an_address(self):
        self.assertTrue(minutes_share._matches('山田 さん', '山田 太郎', 'x@example.com'))
        self.assertTrue(minutes_share._matches('Kenta', '', 'kenta.s@example.com'))
        self.assertFalse(minutes_share._matches('Ken', 'Aya', 'aya@example.com'))


class Auto(unittest.TestCase):
    def setUp(self):
        self.assertIn('desk-minutes-test-', minutes.DIR)
        os.environ['MEETCHECK_FAKE'] = '1'

    def tearDown(self):
        del os.environ['MEETCHECK_FAKE']

    def test_every_minute_gets_a_doc_and_the_doc_follows_the_text(self):
        m = minutes.create({'title': 'Plan', 'body': '## Decided\n- Go'})
        minutes_share.auto()
        g = minutes.get(m['id'])['gdoc']
        self.assertIn('docs.google.com', g['url'])
        self.assertEqual(minutes_share.auto(), 0)  # nothing changed: no new call
        minutes.put(m['id'], {'body': '## Decided\n- Stop'})
        self.assertEqual(minutes_share.auto(), 0)  # just edited: waits
        with mock.patch.object(minutes_share.time, 'time', return_value=minutes.get(m['id'])['updated'] + 300):
            self.assertEqual(minutes_share.auto(), 1)
        self.assertEqual(minutes.get(m['id'])['gdoc']['id'], g['id'])  # same doc, same link


if __name__ == '__main__':
    unittest.main()
