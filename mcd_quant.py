#!/usr/bin/env python3
"""mcd-quant: McDonald's meal portfolio optimizer.

Treats a McDonald's order as a constrained optimization problem:
given a budget and nutrition targets, pull the live menu / nutrition /
coupon data through the McDonald's MCP Server (https://mcp.mcd.cn),
search the bounded candidate space deterministically, verify the winning
basket with the authoritative calculate-price tool, and emit a verdict
(BUY / WAIT / SKIP) with a confidence score.

Design rules:
- Pure stdlib. Zero dependencies. Runs offline against bundled fixtures.
- Deterministic: same inputs -> same output (fixed iteration order,
  explicit tie-breaks, no randomness, no wall-clock in decisions).
- The MCP token is read from the environment (MCD_TOKEN) only.
  It is never written to disk by this tool and never part of any fixture.

Usage:
    MCD_TOKEN=... python mcd_quant.py optimize --store 1950212 --budget 30
    MCD_TOKEN=... python mcd_quant.py stores --city 北京市 --keyword 麦当劳
    MCD_TOKEN=... python mcd_quant.py menu --store 1950212
    python mcd_quant.py optimize --offline --budget 30      # no token needed
    python mcd_quant.py tools                               # list MCP tools
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MCP_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-03-26"

# ---- deterministic search bounds (named, measurable) ------------------------
POOL_SIZE = 24            # candidate items kept after ratio pruning
MAX_QTY = 2               # max quantity per item in a basket
MAX_ITEMS = 3             # max distinct items per basket

# ---- verdict thresholds (named, measurable) --------------------------------
EFFICIENCY_RATIO_MIN = 1.15   # best-per-yuan vs pool median, required for BUY
HEADROOM = 1.10               # constraint headroom required for BUY
CONFIDENCE_BASE = 0.4         # confidence = base + slope * (ratio - 1)
CONFIDENCE_SLOPE = 0.6
CONFIDENCE_FLOOR = 0.10       # any feasible basket keeps a minimal signal
CONFIDENCE_CAP = 0.99

VERDICTS = ("BUY", "WAIT", "SKIP")


# ============================================================================
# text repair: the MCP server double-encodes some Chinese payloads
# ============================================================================
def fix_mojibake(s):
    """Repair UTF-8-read-as-Latin1 double encoding when detected."""
    if not isinstance(s, str) or not s:
        return s
    try:
        repaired = s.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s
    if any("\u4e00" <= ch <= "\u9fff" for ch in repaired):
        return repaired
    return s


def deep_fix(v):
    if isinstance(v, str):
        return fix_mojibake(v)
    if isinstance(v, dict):
        return {k: deep_fix(x) for k, x in v.items()}
    if isinstance(v, list):
        return [deep_fix(x) for x in v]
    return v


# ============================================================================
# MCP client (streamable HTTP, JSON-RPC 2.0)
# ============================================================================
class McpError(Exception):
    pass


class McpClient:
    """Minimal streamable-HTTP MCP client for mcp.mcd.cn."""

    def __init__(self, token=None, url=MCP_URL):
        self.url = url
        self.token = token or os.environ.get("MCD_TOKEN", "")
        if not self.token:
            raise McpError("MCD_TOKEN env var missing (or pass --token)")
        self._rid = 0

    def _post(self, payload):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.token,
            "authToken": self.token,
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return self._parse(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise McpError("HTTP %s: %s" % (e.code, body[:200]))
        except urllib.error.URLError as e:
            raise McpError("network: %s" % e.reason)

    @staticmethod
    def _parse(body):
        body = body.lstrip()
        if not body:
            return {}  # e.g. 202 Accepted for notifications (empty body)
        if body.startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        last = None
        for line in body.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        last = json.loads(chunk)
                    except json.JSONDecodeError:
                        pass
        if last is None:
            raise McpError("unparseable MCP response: %s" % body[:200])
        return last

    def initialize(self):
        r = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "mcd-quant", "version": "0.1.0"}}})
        if "result" not in r:
            raise McpError("initialize failed: %s" % json.dumps(r)[:200])
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r["result"]

    def _next_id(self):
        self._rid += 1
        return self._rid

    def call_tool(self, name, arguments=None):
        r = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}})
        if "error" in r:
            raise McpError("tool %s error: %s" % (name, json.dumps(r["error"])[:200]))
        result = r.get("result", {})
        if result.get("isError"):
            raise McpError("tool %s isError: %s" % (name, json.dumps(result)[:200]))
        structured = result.get("structuredContent")
        if structured is None:
            # fall back to content[0].text (may embed JSON after prose)
            for block in result.get("content", []):
                text = block.get("text", "")
                idx = text.find("{")
                if idx >= 0:
                    try:
                        structured = json.loads(text[idx:])
                        break
                    except json.JSONDecodeError:
                        pass
        if structured is None:
            raise McpError("tool %s: no structuredContent" % name)
        if isinstance(structured, dict) and structured.get("success") is False:
            raise McpError("tool %s API error %s: %s" % (
                name, structured.get("code"), structured.get("message")))
        return deep_fix(structured)

    def list_tools(self):
        self.initialize()
        r = self._post({"jsonrpc": "2.0", "id": self._next_id(),
                        "method": "tools/list", "params": {}})
        return [t.get("name") for t in r.get("result", {}).get("tools", [])]


# ============================================================================
# parsers: MCP payloads -> normalized records
# ============================================================================
def parse_stores(payload):
    """query-nearby-stores structuredContent -> list of store dicts."""
    out = []
    for row in (payload or {}).get("data", []) or []:
        if not row.get("storeCode"):
            continue
        out.append({
            "storeCode": str(row["storeCode"]),
            "storeName": row.get("storeName", ""),
            "address": row.get("address", ""),
            "businessStatus": bool(row.get("businessStatus")),
            "reservation": bool(row.get("reservation")),
        })
    out.sort(key=lambda s: s["storeCode"])
    return out


def parse_menu(payload):
    """query-meals structuredContent -> list of item dicts (sorted by code)."""
    data = (payload or {}).get("data", {}) or {}
    cat_of = {}
    for cat in data.get("categories", []) or []:
        for meal in cat.get("meals", []) or []:
            code = str(meal.get("code", ""))
            if code and code not in cat_of:
                cat_of[code] = cat.get("name", "")
    items = []
    for code, info in (data.get("meals", {}) or {}).items():
        try:
            price = float(str(info.get("currentPrice", "0")))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        items.append({
            "code": str(code),
            "name": info.get("name", ""),
            "category": cat_of.get(str(code), ""),
            "price_yuan": price,
        })
    items.sort(key=lambda i: i["code"])
    return items


def parse_nutrition(text):
    """list-nutrition-foods data (CSV-ish text) -> {name: {kcal, protein, ...}}.

    Format:
        [160]{productName,nutritionDescription,energyKj,energyKcal,protein,
              fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213

    The live feed has 160 rows but 158 unique names (two names appear twice,
    e.g. 小杯玉米杯). First occurrence wins - deterministic join rule.
    """
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9 or not parts[0] or parts[0] in out:
            continue

        def num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        out[parts[0]] = {
            "energy_kj": num(parts[2]), "kcal": num(parts[3]),
            "protein_g": num(parts[4]), "fat_g": num(parts[5]),
            "carb_g": num(parts[6]), "sodium_mg": num(parts[7]),
            "calcium_mg": num(parts[8]),
        }
    return out


def attach_nutrition(items, nutrition):
    """Join menu items with nutrition by exact product name (deterministic)."""
    for it in items:
        n = nutrition.get(it["name"])
        it["kcal"] = n["kcal"] if n else None
        it["protein_g"] = n["protein_g"] if n else None
    return items


# ============================================================================
# the optimizer: bounded, deterministic, exhaustive within bounds
# ============================================================================
def _ratio_pool(items, key):
    """Top POOL_SIZE items by per-yuan ratio; ties broken by code (stable)."""
    pool = [i for i in items if i["price_yuan"] > 0 and i.get(key) is not None]
    pool.sort(key=lambda i: (-i[key] / i["price_yuan"], i["code"]))
    return pool[:POOL_SIZE]


def _median(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _combos(pool):
    """All multisets of size<=MAX_ITEMS with qty<=MAX_QTY, deterministic order."""
    n = len(pool)
    for a in range(n):
        yield [(a, 1)]
        for qa in (2,):
            yield [(a, qa)]
        for b in range(a, n):
            for qa, qb in ((1, 1), (2, 1), (1, 2), (2, 2)):
                yield [(a, qa), (b, qb)]
            for c in range(b, n):
                for qa, qb, qc in ((1, 1, 1), (2, 1, 1), (1, 2, 1), (1, 1, 2),
                                   (2, 2, 1), (2, 1, 2), (1, 2, 2), (2, 2, 2)):
                    yield [(a, qa), (b, qb), (c, qc)]


def _basket_totals(pool, combo):
    price = kcal = protein = 0.0
    lines = []
    for idx, qty in combo:
        it = pool[idx]
        price += it["price_yuan"] * qty
        kcal += (it.get("kcal") or 0.0) * qty
        protein += (it.get("protein_g") or 0.0) * qty
        lines.append({"code": it["code"], "name": it["name"], "qty": qty,
                      "price_yuan": round(it["price_yuan"] * qty, 2),
                      "kcal": round((it.get("kcal") or 0.0) * qty, 1),
                      "protein_g": round((it.get("protein_g") or 0.0) * qty, 1)})
    return price, kcal, protein, lines


def optimize(items, budget, objective="protein", min_protein=0.0, min_kcal=0.0):
    """Deterministic bounded search. Returns a result dict (never raises)."""
    if budget <= 0:
        return {"verdict": "SKIP", "confidence": 0.0, "reasons": ["budget must be > 0"]}

    if objective in ("protein", "kcal"):
        key = "protein_g" if objective == "protein" else "kcal"
        pool = _ratio_pool(items, key)
        if len(pool) < 1:
            return {"verdict": "SKIP", "confidence": 0.0,
                    "reasons": ["no menu items with %s data" % key]}
        ratios = [i[key] / i["price_yuan"] for i in pool]
        med = _median(ratios)

        best = None
        for combo in _combos(pool):
            price, kcal, protein, lines = _basket_totals(pool, combo)
            if price > budget + 1e-9:
                continue
            if protein + 1e-9 < min_protein or kcal + 1e-9 < min_kcal:
                continue
            if objective == "protein":
                score = protein
            else:
                score = kcal
            key_tuple = (-score, round(price, 6),
                         tuple(sorted((pool[i]["code"], q) for i, q in combo)))
            if best is None or key_tuple < best[0]:
                best = (key_tuple, combo, price, kcal, protein, lines)

        if best is None:
            return {"verdict": "SKIP", "confidence": 0.0,
                    "reasons": ["no basket within budget %.2f meets targets "
                                "(min_protein=%.1fg, min_kcal=%.1fkcal)"
                                % (budget, min_protein, min_kcal)]}

        _, combo, price, kcal, protein, lines = best
        # efficiency must be measured in the objective's own unit
        metric = protein if objective == "protein" else kcal
        ratio = (metric / price) if price > 0 else 0.0
        eff = ratio / med if med else 0.0
        feasible_headroom = (protein >= min_protein * HEADROOM and
                             kcal >= min_kcal * HEADROOM)
        verdict = "BUY" if (eff >= EFFICIENCY_RATIO_MIN and feasible_headroom) else "WAIT"
        confidence = min(CONFIDENCE_CAP,
                         max(CONFIDENCE_FLOOR,
                             CONFIDENCE_BASE + CONFIDENCE_SLOPE * (eff - 1.0)))
        reasons = [
            "objective=%s pool=%d median_ratio=%.3f" % (objective, len(pool), med),
            "best_ratio=%.3f efficiency=%.2fx" % (ratio, eff),
            "totals: price=%.2f kcal=%.0f protein=%.1fg" % (price, kcal, protein),
        ]
        if verdict == "WAIT":
            if eff < EFFICIENCY_RATIO_MIN:
                reasons.append("efficiency below %.2fx threshold" % EFFICIENCY_RATIO_MIN)
            if not feasible_headroom:
                reasons.append("constraint headroom below %.2fx" % HEADROOM)
        return {"verdict": verdict, "confidence": round(confidence, 3),
                "basket": lines, "totals": {"price_yuan": round(price, 2),
                                            "kcal": round(kcal, 1),
                                            "protein_g": round(protein, 1)},
                "reasons": reasons}

    if objective == "cheap":
        # minimize price subject to min protein / min kcal
        pool = _ratio_pool(items, "protein_g") if min_protein > 0 else items[:POOL_SIZE]
        pool = [i for i in pool if i.get("kcal") is not None or min_kcal <= 0]
        best = None
        for combo in _combos(pool):
            price, kcal, protein, lines = _basket_totals(pool, combo)
            if protein + 1e-9 < min_protein or kcal + 1e-9 < min_kcal:
                continue
            key_tuple = (round(price, 6),
                         tuple(sorted((pool[i]["code"], q) for i, q in combo)))
            if best is None or key_tuple < best[0]:
                best = (key_tuple, combo, price, kcal, protein, lines)
        if best is None:
            return {"verdict": "SKIP", "confidence": 0.0,
                    "reasons": ["no basket meets min_protein=%.1f min_kcal=%.1f"
                                % (min_protein, min_kcal)]}
        _, combo, price, kcal, protein, lines = best
        return {"verdict": "BUY", "confidence": 0.99,
                "basket": lines, "totals": {"price_yuan": round(price, 2),
                                            "kcal": round(kcal, 1),
                                            "protein_g": round(protein, 1)},
                "reasons": ["cheapest basket meeting constraints",
                            "totals: price=%.2f kcal=%.0f protein=%.1fg"
                            % (price, kcal, protein)]}

    return {"verdict": "SKIP", "confidence": 0.0,
            "reasons": ["unknown objective: %s" % objective]}


def verify_price(client, store_code, basket, order_type=1, be_type=1):
    """Authoritative price check via MCP calculate-price. Returns dict or None."""
    items = [{"productCode": b["code"], "quantity": b["qty"]} for b in basket]
    try:
        payload = client.call_tool("calculate-price", {
            "storeCode": str(store_code), "orderType": order_type,
            "beType": be_type, "items": items})
    except McpError as e:
        return {"ok": False, "error": str(e)}
    data = (payload or {}).get("data", {}) or {}
    if not data:
        return {"ok": False, "error": "empty calculate-price payload"}
    price_fen = data.get("price")
    if price_fen is None:
        return {"ok": False, "error": "no price field"}
    return {"ok": True, "price_yuan": round(price_fen / 100.0, 2),
            "discount_yuan": round((data.get("discount") or 0) / 100.0, 2)}


# ============================================================================
# data loading: live MCP or offline fixture
# ============================================================================
FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "demo_fixture.json")


def load_live(client, store_code, city=None, keyword=None):
    calls = []
    if not store_code:
        stores = parse_stores(client.call_tool("query-nearby-stores", {
            "beType": 1, "searchType": 2, "city": city or "北京市",
            "keyword": keyword or "麦当劳"}))
        calls.append({"tool": "query-nearby-stores", "stores": len(stores)})
        if not stores:
            raise McpError("no stores found for city=%s keyword=%s" % (city, keyword))
        store_code = stores[0]["storeCode"]
    menu_payload = client.call_tool("query-meals", {
        "storeCode": str(store_code), "orderType": 1, "beType": 1})
    calls.append({"tool": "query-meals", "storeCode": str(store_code)})
    nutrition_text = (client.call_tool("list-nutrition-foods", {}) or {}).get("data", "")
    calls.append({"tool": "list-nutrition-foods"})
    items = attach_nutrition(parse_menu(menu_payload), parse_nutrition(nutrition_text))
    return store_code, items, calls


def load_offline(path=FIXTURE_PATH):
    with open(path, "r", encoding="utf-8") as f:
        fx = json.load(f)
    menu_payload = fx.get("query-meals")
    nutrition_text = (fx.get("list-nutrition-foods") or {}).get("data", "")
    items = attach_nutrition(parse_menu(menu_payload), parse_nutrition(nutrition_text))
    return str(fx.get("store_code", "")), items, [{"tool": "offline-fixture",
                                                   "path": os.path.basename(path)}]


# ============================================================================
# CLI
# ============================================================================
def _print_table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join("%%-%ds" % w for w in widths)
    print(fmt % tuple(headers))
    print(fmt % tuple("-" * w for w in widths))
    for r in rows:
        print(fmt % tuple(str(c) for c in r))


def cmd_tools(args):
    client = McpClient()
    names = client.list_tools()
    print("MCP tools (%d):" % len(names))
    for n in sorted(names):
        print(" ", n)
    return 0


def cmd_stores(args):
    client = McpClient()
    stores = parse_stores(client.call_tool("query-nearby-stores", {
        "beType": 1, "searchType": 2, "city": args.city, "keyword": args.keyword}))
    if not stores:
        print("no stores found")
        return 1
    if args.json:
        print(json.dumps(stores, ensure_ascii=False, indent=1))
        return 0
    _print_table([[s["storeCode"], s["storeName"], s["address"],
                   "open" if s["businessStatus"] else "closed"] for s in stores],
                 ["storeCode", "storeName", "address", "status"])
    return 0


def cmd_menu(args):
    client = McpClient()
    store, items, _ = load_live(client, args.store, args.city, args.keyword)
    print("store %s | %d items" % (store, len(items)))
    _print_table([[i["code"], i["name"], i["category"], i["price_yuan"],
                   i.get("kcal"), i.get("protein_g")] for i in items],
                 ["code", "name", "category", "price", "kcal", "protein_g"])
    return 0


def cmd_optimize(args):
    if args.offline:
        store, items, calls = load_offline()
        client = None
    else:
        client = McpClient()
        client.initialize()
        store, items, calls = load_live(client, args.store, args.city, args.keyword)

    result = optimize(items, args.budget, objective=args.objective,
                      min_protein=args.min_protein, min_kcal=args.min_kcal)

    price_check = None
    if client and result.get("basket"):
        price_check = verify_price(client, store, result["basket"])
        if price_check and price_check.get("ok"):
            local = result["totals"]["price_yuan"]
            venue = price_check["price_yuan"]
            price_check["delta_yuan"] = round(venue - local, 2)
            if venue > args.budget + 1e-9 and result["verdict"] == "BUY":
                result["verdict"] = "WAIT"
                result["reasons"].append(
                    "venue price %.2f exceeded budget %.2f" % (venue, args.budget))

    out = {"store": store, "budget": args.budget,
           "objective": args.objective, "mcp_calls": calls,
           "verdict": result["verdict"], "confidence": result["confidence"],
           "basket": result.get("basket", []), "totals": result.get("totals", {}),
           "price_check": price_check, "reasons": result["reasons"]}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print("store: %s | budget: %.2f | objective: %s" % (store, args.budget, args.objective))
    for r in result["reasons"]:
        print("  -", r)
    if result.get("basket"):
        _print_table([[b["name"], b["qty"], b["price_yuan"], b["kcal"], b["protein_g"]]
                      for b in result["basket"]],
                     ["item", "qty", "price", "kcal", "protein_g"])
        t = result["totals"]
        print("totals: price=%.2f kcal=%.0f protein=%.1fg" %
              (t["price_yuan"], t["kcal"], t["protein_g"]))
    if price_check:
        if price_check.get("ok"):
            print("price_check (MCP calculate-price): venue=%.2f local=%.2f delta=%+.2f"
                  % (price_check["price_yuan"], result["totals"]["price_yuan"],
                     price_check["delta_yuan"]))
        else:
            print("price_check failed:", price_check.get("error"))
    print("VERDICT: %s (confidence %.3f)" % (result["verdict"], result["confidence"]))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="mcd_quant",
                                description="McDonald's meal portfolio optimizer (MCP-powered)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("tools", help="list MCP tools")
    sp.set_defaults(fn=cmd_tools)

    sp = sub.add_parser("stores", help="list nearby stores")
    sp.add_argument("--city", default="北京市")
    sp.add_argument("--keyword", default="麦当劳")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_stores)

    sp = sub.add_parser("menu", help="dump store menu with nutrition")
    sp.add_argument("--store", default="")
    sp.add_argument("--city", default="北京市")
    sp.add_argument("--keyword", default="麦当劳")
    sp.set_defaults(fn=cmd_menu)

    sp = sub.add_parser("optimize", help="run the optimizer")
    sp.add_argument("--store", default="")
    sp.add_argument("--city", default="北京市")
    sp.add_argument("--keyword", default="麦当劳")
    sp.add_argument("--budget", type=float, required=True)
    sp.add_argument("--objective", default="protein",
                    choices=["protein", "kcal", "cheap"])
    sp.add_argument("--min-protein", type=float, default=0.0, dest="min_protein")
    sp.add_argument("--min-kcal", type=float, default=0.0, dest="min_kcal")
    sp.add_argument("--offline", action="store_true",
                    help="run against bundled fixture (no token/network)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_optimize)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except McpError as e:
        print("MCP ERROR:", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
