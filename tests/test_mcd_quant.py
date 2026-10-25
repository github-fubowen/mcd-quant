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

    def test_kcal_efficiency_uses_kcal_metric(self):
        """Regression: efficiency ratio must use the objective's own unit.

        Earlier bug: ratio was always protein/price even for objective=kcal,
        so the kcal verdict compared a protein ratio against a kcal median.
        """
        r = mq.optimize(self.items, 40, objective="kcal")
        t = r["totals"]
        expected = t["kcal"] / t["price_yuan"]  # kcal per yuan of the winner
        line = [x for x in r["reasons"] if x.startswith("best_ratio=")][0]
        printed = float(line.split("=")[1].split()[0])
        self.assertAlmostEqual(printed, expected, places=2)
        self.assertGreater(printed, 1.0)  # sanity: kcal/yuan, not protein/yuan

    def test_feasible_confidence_floor(self):
        """Any feasible basket keeps confidence >= floor; only SKIP is 0."""
        saw_feasible = False
        for budget in (10, 15, 20, 25, 30, 40, 50, 60):
            for obj in ("protein", "kcal"):
                r = mq.optimize(self.items, budget, objective=obj)
                if r["verdict"] in ("BUY", "WAIT"):
                    saw_feasible = True
                    self.assertGreaterEqual(r["confidence"], mq.CONFIDENCE_FLOOR)
                    self.assertLessEqual(r["confidence"], mq.CONFIDENCE_CAP)
                else:
                    self.assertEqual(r["confidence"], 0.0)
        self.assertTrue(saw_feasible)


class TestMcpClientErrorSurfacing(unittest.TestCase):
    """API-level errors (success=false) must raise, not silently degrade."""

    def test_api_error_payload_raises(self):
        c = mq.McpClient(token="dummy-token")
        err_payload = {"jsonrpc": "2.0", "id": 1, "result": {
            "isError": False,
            "structuredContent": {"success": False, "code": 60002,
                                  "message": "未查询到门店信息, 检查storeCode和beCode是否配对"}}}
        c._post = lambda payload: err_payload
        with self.assertRaises(mq.McpError) as ctx:
            c.call_tool("query-meals", {"storeCode": "999999999",
                                        "orderType": 1, "beType": 1})
        self.assertIn("60002", str(ctx.exception))
        self.assertIn("未查询到门店信息", str(ctx.exception))

    def test_ok_payload_passes(self):
        c = mq.McpClient(token="dummy-token")
        ok_payload = {"jsonrpc": "2.0", "id": 1, "result": {
            "isError": False,
            "structuredContent": {"success": True, "code": 200,
                                  "message": "请求成功", "data": {"x": 1}}}}
        c._post = lambda payload: ok_payload
        out = c.call_tool("now-time-info", {})
        self.assertEqual(out.get("data"), {"x": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
