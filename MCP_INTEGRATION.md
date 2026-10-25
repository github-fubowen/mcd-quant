# MCP_INTEGRATION.md — 麦当劳 MCP 集成说明

## 1. 使用的 MCP Server

| 项 | 值 |
|---|---|
| 服务名 | 麦当劳中国 MCP Server（mcd-mcp） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（MCP 协议版本 2025-03-26） |
| 鉴权 | 请求头 `Authorization: Bearer <MCP_TOKEN>`（Token 由 <https://open.mcd.cn/mcp> 控制台申请） |
| 工具数 | 实测 **35** 个（2026-10-25 live 探测，官方 README 列 34 个，线上多出 `query-promotions`、`query-survey-coupon`） |

本项目的 Token 只从环境变量 `MCD_TOKEN` 读取；仓库内不含任何真实凭证
（`mcp-config.example.json` 仅使用 `${MCD_TOKEN}` 占位符）。

## 2. 实际调用的 Tool 与调用流程

```text
query-nearby-stores(beType=1, searchType=2, city, keyword)
        │  定位门店，取 storeCode（到店自取场景无需 beCode）
        ▼
query-meals(storeCode, orderType=1, beType=1)
        │  拉取门店实时菜单：分类 + 89 个餐品（code / 名称 / 当前价）
        ▼
list-nutrition-foods({})
        │  拉取全量营养表：160 行 CSV 文本（能量/蛋白质/脂肪/碳水/钠/钙）
        ▼
[本地引擎] 名称精确 join → 约束搜索（见 README「确定性」一节）
        ▼
calculate-price(storeCode, orderType=1, beType=1, items=[{productCode, quantity}])
            官方计价引擎复核最优篮子的权威价格（返回单位：分）
```

预留未用（v0.2 范围）：`query-store-coupons` / `available-coupons` /
`auto-bind-coupons`（优惠券感知优化）、`create-order` / `cancel-order` /
`query-order`（执行闭环，需用户显式确认）。

## 3. 实测读数（2026-10-25，store 1950212，budget ¥30，objective=protein）

```text
store: 1950212 | budget: 30.00 | objective: protein
  - objective=protein pool=21 median_ratio=0.769
  - best_ratio=1.000 efficiency=1.30x
  - totals: price=28.00 kcal=485 protein=28.0g
item     qty  price  kcal   protein_g
双层深海鳕鱼堡  1    28.0   485.0  28.0
price_check (MCP calculate-price): venue=28.00 local=28.00 delta=+0.00
VERDICT: BUY (confidence 0.580)
```

**本地估算价与官方 `calculate-price` 零偏差（delta +0.00）**——引擎的价格模型
经官方计价工具逐项复核。

## 4. 联调中实测到的服务端行为（已适配）

| 现象 | 处理 |
|---|---|
| 部分中文 payload 双重编码（UTF-8 被按 Latin-1 读） | `fix_mojibake()` 探测性修复，仅当修复后含 CJK 才采用 |
| `notifications/initialized` 返回 202 空 body | 空 body 视为合法响应（`{}`），不报错 |
| `query-meals` 的 `data` 为 dict（`meals` 按 code 索引 + `categories`） | `parse_menu()` 归一化为列表并建 code→分类映射 |
| `list-nutrition-foods` 的 `data` 是 CSV 文本而非 JSON | `parse_nutrition()` 解析；160 行中 2 个重名（小杯玉米杯、纯牛奶（盒装）），**首现行获胜** |
| 菜单价格为字符串「元」，`calculate-price` 返回整数「分」 | 分别归一化为元（float） |
| `structuredContent` 与 `content[0].text`（散文头 + 内嵌 JSON）并存 | 优先 `structuredContent`，失败回退解析 text 中的 JSON |

## 5. 业务价值

把「怎么点麦当劳」从聊天建议升级为**可复现的约束优化**：给定预算与营养目标，
输出确定性决策（BUY/WAIT/SKIP + 置信度），价格经官方引擎复核。
典型用户：控卡/控蛋白的健身人群、预算敏感的学生党、想量化「怎么点最值」的开发者。
