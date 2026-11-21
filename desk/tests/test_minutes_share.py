"""Minutes → Google Doc and thank-you draft: the parts that need neither Google nor Gmail."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('MINUTES_DIR', __import__('tempfile').mkdtemp(prefix='desk-minutes-test-'))  # never the real minutes
import minutes_share  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
