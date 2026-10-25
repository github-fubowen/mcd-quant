#!/usr/bin/env python3
"""Offline deterministic tests for mcd_quant. Stdlib unittest only.

Run: python -E -s -X utf8 tests/test_mcd_quant.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcd_quant as mq


def load_fixture():
    with open(mq.FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_items():
    fx = load_fixture()
    menu_payload = fx.get("query-meals")
    nutrition_text = (fx.get("list-nutrition-foods") or {}).get("data", "")
    return mq.attach_nutrition(mq.parse_menu(menu_payload),
                               mq.parse_nutrition(nutrition_text))


class TestTextRepair(unittest.TestCase):
    def test_mojibake_repair(self):
        # the server double-encodes some Chinese payloads (utf-8 read as latin-1)
        broken = "麦辣鸡腿汉堡三件套".encode("utf-8").decode("latin-1")
        self.assertEqual(mq.fix_mojibake(broken), "麦辣鸡腿汉堡三件套")

    def test_plain_text_untouched(self):
        self.assertEqual(mq.fix_mojibake("巨无霸"), "巨无霸")
        self.assertEqual(mq.fix_mojibake("Big Mac"), "Big Mac")
        self.assertEqual(mq.fix_mojibake(""), "")


class TestParsers(unittest.TestCase):
    def test_nutrition_rows(self):
        fx = load_fixture()
        n = mq.parse_nutrition((fx.get("list-nutrition-foods") or {}).get("data", ""))
        # 160 rows in the feed, 158 unique names (two duplicated names, first wins)
        self.assertEqual(len(n), 158)
        row = n["猪柳麦满分"]
        self.assertEqual(row["kcal"], 308.0)
        self.assertEqual(row["protein_g"], 16.0)

    def test_menu_parse(self):
        items = load_items()
        self.assertEqual(len(items), 89)
        codes = [i["code"] for i in items]
        self.assertEqual(codes, sorted(codes))  # deterministic order
        by_name = {i["name"]: i for i in items}
        self.assertAlmostEqual(by_name["双层深海鳕鱼堡"]["price_yuan"], 28.0)
        self.assertAlmostEqual(by_name["麦辣鸡腿汉堡三件套"]["price_yuan"], 49.0)
        # nutrition join worked for at least one item
        self.assertIsNotNone(by_name["双层深海鳕鱼堡"]["kcal"])


class TestOptimizer(unittest.TestCase):
    def setUp(self):
        self.items = load_items()

    def test_buy_within_budget(self):
        r = mq.optimize(self.items, 30, objective="protein")
        self.assertEqual(r["verdict"], "BUY")
        self.assertLessEqual(r["totals"]["price_yuan"], 30)
        self.assertGreater(r["totals"]["protein_g"], 0)
        self.assertTrue(0 < r["confidence"] < 1)

    def test_determinism(self):
        r1 = mq.optimize(self.items, 30, objective="protein")
        r2 = mq.optimize(self.items, 30, objective="protein")
        self.assertEqual(json.dumps(r1, sort_keys=True, ensure_ascii=False),
                         json.dumps(r2, sort_keys=True, ensure_ascii=False))

    def test_skip_impossible_targets(self):
        r = mq.optimize(self.items, 30, objective="protein", min_protein=500)
        self.assertEqual(r["verdict"], "SKIP")
        self.assertEqual(r["confidence"], 0.0)

    def test_skip_tiny_budget(self):
        r = mq.optimize(self.items, 0.5, objective="protein")
        self.assertEqual(r["verdict"], "SKIP")

    def test_cheap_objective(self):
        r = mq.optimize(self.items, 60, objective="cheap", min_protein=25)
        self.assertEqual(r["verdict"], "BUY")
        self.assertGreaterEqual(r["totals"]["protein_g"], 25)
        self.assertLessEqual(r["totals"]["price_yuan"], 60)

    def test_kcal_objective(self):
        r = mq.optimize(self.items, 40, objective="kcal")
        self.assertIn(r["verdict"], ("BUY", "WAIT"))
        self.assertLessEqual(r["totals"]["price_yuan"], 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
