# workbuddy.md — WorkBuddy 开发对话上下文导出

本文件按《麦当劳程序员创意开发大赛》WorkBuddy 专项奖励要求提交：记录本项目
使用腾讯 WorkBuddy 智能体开发过程中的关键对话上下文与决策依据，供核验
「真实使用 WorkBuddy 开发」之用。

- 会话日期：2026-10-25（北京时间）
- 开发环境：WorkBuddy（Windows 桌面端，Git Bash）+ 本地 Python 3.13
- 项目仓库：https://github.com/github-fubowen/mcd-quant

## 1. 用户指令（会话首条）

> @skill:user-vibe_coding-sdk-moe 制定计划,获取workbuddy积分:
> "https://github.com/M-China/mcd-developer-innovation-challenge.git":
> 麦当劳mcp token:"<已脱敏，详见仓库安全记录>"

指令意图：加载编码技能 → 制定参赛计划 → 获取 WorkBuddy 积分奖励。

## 2. 调研阶段（WorkBuddy 执行的只读取证）

- 克隆大赛官方仓库与 MCP Server 指南仓库，读取 `README.md`、
  `activityGuidelines.md`、`RANKING.md`、`CONTEST_DECLARATION.md`。
- 关键读数（2026-10-25 15:00 榜单快照）：前 100 名门槛 73–76 stars；
  第 1 名 1,567 stars；已提交报名 Issue 1,371 条；报名与仓库创建截止
  10-25 23:59（北京时间），榜单快照 10-26 00:00。
- 奖励机制确认：进入排行榜前 100 且提交 `workbuddy.md` → 3,000 WorkBuddy
  积分；前三名 → 10,240 积分。
- GitHub 身份核实：`github-fubowen`（gh CLI 已登录，含 repo 权限，个人账户），
  此前未报名、无其他参赛项目。

## 3. 方案决策（WorkBuddy 提出选项，用户确认）

WorkBuddy 给出三个候选方案并推荐「mcd-quant 量化引擎」：榜单前 100 已被
省钱/营养/拼单/推荐四类聊天型助手占满，量化引擎（确定性输出 + 可复现计算）
是差异化最强的类别。用户经选项卡片确认：**选 mcd-quant，授权按计划执行**
（含建公开仓、推送、发报名 Issue）。

## 4. 开发过程（关键节点与实测读数）

1. **MCP 连接**：WorkBuddy 写入本机 `~/.workbuddy/mcp.json` 配置
   `mcd-mcp`（streamableHttp）；裸协议探测确认握手成功，**实测 35 个工具**
   （官方 README 列 34 个，线上多出 `query-promotions`、`query-survey-coupon`）。
2. **Schema 取证**：拉取 9 个引擎相关工具的 inputSchema，明确调用链
   `query-nearby-stores → query-meals → list-nutrition-foods → calculate-price`。
3. **Fixture 抓取**：门店 1950212（麦当劳北京前门西街餐厅）实时菜单 89 个
   餐品 + 160 行营养表；仅存公开菜单与营养，剔除距离等位置字段，不存账户券数据。
4. **服务端行为适配**（联调实测，详见 MCP_INTEGRATION.md §4）：
   中文 payload 双重编码（修复函数）、`notifications/initialized` 返回 202
   空 body、营养数据为 CSV 文本、菜单价格为「元」而算价返回「分」、
   160 行中 2 个重名产品（首现行获胜）。
5. **引擎与测试**：纯 stdlib 单文件 CLI + 离线确定性测试（提交时 10 项，
   TEST 阶段新增 4 项回归后 14 项，全部通过）。
6. **端到端实测**（live，budget ¥30，objective=protein）：

   ```text
   store: 1950212 | budget: 30.00 | objective: protein
     - objective=protein pool=21 median_ratio=0.769
     - best_ratio=1.000 efficiency=1.30x
     - totals: price=28.00 kcal=485 protein=28.0g
   双层深海鳕鱼堡  1  28.0  485.0  28.0
   price_check (MCP calculate-price): venue=28.00 local=28.00 delta=+0.00
   VERDICT: BUY (confidence 0.580)
   ```

   本地估算价与官方计价引擎**零偏差**。
7. **发布**：2026-10-25 15:30 创建公开仓库（锁定创建时间门槛），
   commit `b3fe4f2`，9 个文件推送至 `github-fubowen/mcd-quant`。
   提交前经 token 泄漏扫描（0 命中，带正控）。

## 5. 方法论备注

本项目遵循「读数 + 可复跑」纪律：所有结论附实测数据与复跑命令
（`python tests/test_mcd_quant.py` 14/14 OK；`optimize --offline` 无 Token
可复现同一决策）。开发中两处失败均为测试仪器自身误差（手写 mojibake 样本
错误、重名行计数口径），修正后全绿——判定与主体实现严格分离。
