# mcd-quant — 麦当劳量化点餐引擎

> 把「点麦当劳」当组合优化问题：给定预算与营养目标，输出确定性决策
> （BUY / WAIT / SKIP + 置信度），价格经麦当劳官方 MCP 计价引擎逐项复核。

基于[麦当劳中国 MCP](https://github.com/M-China/mcd-mcp-server) 开发，
参赛作品：麦当劳程序员创意开发大赛（2026 程序员节）。
本项目为独立参赛作品，**非麦当劳官方产品**。

## 为什么是又一个「麦当劳 Skill」

榜单上「省钱助手」「营养顾问」大多是聊天型建议——同一家门店、同一个预算，
问两遍可能得到两个答案。mcd-quant 把决策变成**可复现的计算**：

- 同一输入 ⇒ 同一输出（固定搜索序、显式 tie-break、无随机、无时钟依赖）；
- 有约束（预算 / 最低蛋白 / 最低热量）、有目标函数（蛋白/元、热量/元、最省）；
- 最优篮子的价格用官方 `calculate-price` 复核，本次实测
  **本地估算 ¥28.00 vs 官方 ¥28.00，delta = +0.00**；
- 零依赖：纯 Python 标准库，无 Token 也能离线跑（内置 fixture）。

## 安装

```bash
git clone https://github.com/github-fubowen/mcd-quant.git
cd mcd-quant
# 无需安装任何依赖（Python 3.8+ 标准库）
```

## 使用

```bash
# 1) 定位门店（需要 MCP_TOKEN）
export MCD_TOKEN=<你的麦当劳 MCP Token>     # https://open.mcd.cn/mcp 申请
python mcd_quant.py stores --city 北京市 --keyword 麦当劳

# 2) 核心：预算 ¥30，最大化蛋白质
python mcd_quant.py optimize --store 1950212 --budget 30 --objective protein

# 输出示例（2026-10-25 实测）：
# store: 1950212 | budget: 30.00 | objective: protein
#   - objective=protein pool=21 median_ratio=0.769
#   - best_ratio=1.000 efficiency=1.30x
#   - totals: price=28.00 kcal=485 protein=28.0g
# item               qty  price  kcal   protein_g
# 双层深海鳕鱼堡      1    28.0   485.0  28.0
# price_check (MCP calculate-price): venue=28.00 local=28.00 delta=+0.00
# VERDICT: BUY (confidence 0.580)

# 3) 其他目标与约束
python mcd_quant.py optimize --store 1950212 --budget 60 --objective cheap --min-protein 25
python mcd_quant.py optimize --store 1950212 --budget 40 --objective kcal --min-kcal 600
python mcd_quant.py optimize --store 1950212 --budget 30 --json     # 机器可读

# 4) 无 Token 离线演示（内置 2026-10-25 门店快照）
python mcd_quant.py optimize --offline --budget 30

# 5) 测试（离线，确定性）
python tests/test_mcd_quant.py        # 14/14 OK
```

## 目标用户

- **健身/控卡人群**：给定蛋白下限与热量预算，要「最值的一餐」；
- **学生党/价格敏感用户**：给定预算，要蛋白或热量最大化的组合；
- **开发者**：需要一个确定性、可复现、可测试的点餐决策组件
  （`--json` 输出可直接接入 Agent 或脚本）。

## 决策语义

| 判定 | 条件 |
|---|---|
| `BUY` | 篮子可行，且效率（蛋白/元 或 热量/元）≥ 候选池中位数 × 1.15，约束余量 ≥ 10% |
| `WAIT` | 篮子可行但未达上述阈值 |
| `SKIP` | 约束下无可行篮子 |

置信度 = `max(0.10, 0.4 + 0.6 × (效率比 − 1))`，封顶 0.99；仅 SKIP（无可行篮子）为 0。

## 架构

```text
mcd_quant.py            单文件 CLI：MCP 客户端 + 解析器 + 确定性搜索引擎 + 计价复核
fixtures/demo_fixture.json  2026-10-25 门店 1950212 快照（公开菜单+营养，无账户数据）
tests/test_mcd_quant.py     14 项离线确定性测试
SKILL.md                 WorkBuddy Skill 封装（供 Agent 调用）
MCP_INTEGRATION.md       MCP Server / Tool / 调用流程 / 实测读数 / 服务端行为适配
mcp-config.example.json  脱敏配置示例（仅 ${MCD_TOKEN} 占位符）
```

## 安全与合规

- Token 只从环境变量 `MCD_TOKEN` 读取；仓库内无任何真实凭证；
- 引擎**不自动下单**（v0.2 才在用户显式确认下接 `create-order`）；
- 餐品信息、价格与供应状态以麦当劳官方渠道实时结果为准；输出不构成
  医疗或营养专业建议。

## 路线图

- v0.2：优惠券感知优化（`query-store-coupons` / `auto-bind-coupons`）、
  用户显式确认后的下单闭环；
- v0.3：多门店比价、历史订单复盘（`order-list`）。
