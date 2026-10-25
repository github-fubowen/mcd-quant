---
name: mcd-quant
description: 麦当劳量化点餐引擎。当用户想基于预算/营养目标（蛋白质、热量）获得确定性的麦当劳点餐决策（BUY/WAIT/SKIP）时使用。通过麦当劳 MCP（mcp.mcd.cn）拉取实时菜单、营养表并用官方 calculate-price 复核价格；也支持无 Token 的离线演示模式。触发词：麦当劳点餐、麦麦怎么点、点餐优化、蛋白预算、热量预算、mcd-quant。
---

# mcd-quant — 麦当劳量化点餐引擎

把点麦当劳当作组合优化问题：给定预算与营养目标，输出**确定性决策**，
而不是又一份聊天建议。

## 前置条件

- 麦当劳 MCP Token（<https://open.mcd.cn/mcp> 控制台申请），两种用法二选一：
  1. **CLI 直连**：`export MCD_TOKEN=<token>` 后运行 `mcd_quant.py`；
  2. **WorkBuddy 连接器**：按 `mcp-config.example.json` 配置 `mcd-mcp`
     （streamableHttp，`Authorization: Bearer <token>`），由 Agent 直接调工具。
- 无 Token 时可用 `--offline` 跑内置 fixture（2026-10-25 门店 1950212 快照）。

## 使用方式

```bash
# 定位门店
python mcd_quant.py stores --city 北京市 --keyword 麦当劳

# 门店菜单 + 营养
python mcd_quant.py menu --store 1950212

# 核心：预算 ¥30，最大化蛋白质
python mcd_quant.py optimize --store 1950212 --budget 30 --objective protein

# 约束变体：最低 25g 蛋白下最省钱的篮子
python mcd_quant.py optimize --store 1950212 --budget 60 --objective cheap --min-protein 25

# 机器可读输出（供 Agent 消费）
python mcd_quant.py optimize --store 1950212 --budget 30 --json

# 无 Token 离线演示
python mcd_quant.py optimize --offline --budget 30
```

## 输出契约（Agent 必须原样转述，不得修饰数字）

```json
{
  "store": "1950212",
  "budget": 30,
  "objective": "protein",
  "verdict": "BUY | WAIT | SKIP",
  "confidence": 0.58,
  "basket": [{"code": "...", "name": "...", "qty": 1, "price_yuan": 28.0, "kcal": 485.0, "protein_g": 28.0}],
  "totals": {"price_yuan": 28.0, "kcal": 485.0, "protein_g": 28.0},
  "price_check": {"ok": true, "price_yuan": 28.0, "delta_yuan": 0.0},
  "reasons": ["..."]
}
```

判定语义：`BUY` = 效率超池中位数 1.15 倍且约束余量 ≥10%；`WAIT` = 可行但
不达标；`SKIP` = 约束下无可行篮子。置信度 = 0.4 + 0.6 × (效率比 − 1)，封顶 0.99。

## 边界（必须遵守）

- **不自动下单**。引擎只出决策与价格复核；`create-order` 等执行类工具需用户
  显式确认后才可在对话中调用。
- 营养与价格数据以麦当劳官方渠道实时结果为准；本项目非官方产品。
- Token 只走环境变量/连接器配置，禁止写入任何文件或对话回显。
