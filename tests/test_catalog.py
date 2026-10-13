"""GESTURES.md's sign table is generated from the code: this fails when they differ (python -m cameo signs prints it)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cameo.gestures.catalog import markdown, rows


class CatalogTest(unittest.TestCase):
    def test_gestures_md_matches_the_code(self):
        text = (ROOT / 'GESTURES.md').read_text(encoding='utf-8')
        table = text.split('<!-- signs:begin -->')[1].split('<!-- signs:end -->')[0].strip()
        self.assertEqual(table, markdown(), 'GESTURES.md is out of date: paste the output of python -m cameo signs')

    def test_every_sign_says_how_it_is_checked(self):
        for sign, rule, action, check in rows():
            self.assertTrue(sign and rule and action and check, sign)


if __name__ == '__main__':
    unittest.main()
