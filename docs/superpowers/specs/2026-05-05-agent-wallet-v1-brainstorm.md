# Agent Wallet 生产级 v1 — Brainstorm 快照

> **状态**：Brainstorm 进行中。Phase 1（产品定位 + 范围锁定）+ Phase 2（原型 internal stage 更新）+ Phase 3（pivot 至 skill-vendor + 锁定路线 B'）已落地。等待最终 spec 化。
> **日期**：2026-05-05（Phase 1）/ 2026-05-06（Phase 2 + Phase 3）
> **目标读者**：项目内部团队 / 初创共识对齐

---

## 1. 起点：原型 vs 现状

### 1.1 投资人 demo 原型（`localhost:10000`）

静态 SPA（Python `SimpleHTTP` 提供 HTML + React/Babel + Tailwind），品牌 **"Chief · Agent Wallet · Investor Demo"**，16 个 stage，约 7 分钟，EN/ZH 双语，编辑/纸本风。源文件位于 `src/{data,lib,components,stages}/`。

叙事弧（来自 `src/data/demoScript.js`）：

| 模块 | Stage | 投资人故事 |
|---|---|---|
| **M1 Opening** | `m1_opening` | "Agents 需要钱包 —— 为今天发货的开发者，不只是 crypto 原住民" |
| **M2 CLI + Claim** | `m2_cli`, `m2_claim` | 不要注册 / 不要助记词 / 不要 gas，钱包先创建，owner 后认领 |
| **M3 Onramp** | `m3_onramp`, `m3_processing`, `m3_funded` | 信用卡 → USDC，Circle 托管，Chief 跑 agent 层 |
| **M4 A2A 五步** | `m4_n1_quote` → `m4_n5_release` | Quote → Lock → Verify → Deliver → Release，**全链下账本，零链上交易** |
| **M5 Credit ★** | `m5_lookup`, `m5_self`, `m5_moat` | 每个 agent 的 reject-rate / volume / "eigenflux-verified"，行为数据 = 护城河 |
| **M6 收尾** | `m6_withdraw`, `m6_how_it_works` | 今日中心化，"like Stripe started" |

角色：`agentA`（文档工作流 / 付款方）、`agentB`（PDF 翻译，eigenflux-verified，4% 拒绝率）、`agentC`（更新的翻译者，18% 拒绝率）。Owner = `william@example.com`。

### 1.2 OntologyAgent repo 现状

原型是营销面，repo 是底层管道。**M2–M4 覆盖度约 70%，M5–M6 较薄**。

**已上线**

- **M2 Claim**：Agent Wallet MVP 已有 GitHub OAuth + 一次性 claim code（`agent`，状态在 `AGENT_WALLET_STATE_PATH`）
- **M3 Custody**：`chain` MCP 创建 / 复用 Circle sandbox 钱包（`CIRCLE_API_KEY` 等）
- **M4 N1–N5**：独立 `ledger` FastAPI 服务正是原型的"链下账本写入"故事 —— `POST /ledger/accounts/{id}/credit`、`/escrows`、`/release`、`/refund`
- **支付路由**：`agent` 中的 `route_payment_intent` 已经强制按原型分流：A2A 异步 ⇒ `ledger_escrow`，即时 API ⇒ `x402`，提现 ⇒ `chain_transfer`
- **x402 即时 API 路径**：标准 `exact` + Circle Gateway `GatewayWalletBatched`（Nanopayments）已端到端打通 Base Sepolia

**与 pitch 的差距**

- **M5 Credit（★ 护城河）**：`creditAsPayer` / `creditAsPayee`、reject-rate 聚合、"eigenflux-verified"、查询 API —— **repo 中完全没有**。这是投资人 demo vs 现实最大的差距
- **M3 Onramp 面板**：Circle 钱包创建是真的，但信用卡 → USDC onramp UX 在 agent web console 里没有；demo 屏幕是 mock
- **M6 Withdraw**：`chain` 有签名转账 + 上限（`SINGLE_TX_CAP`、`DAILY_LIMIT`、白名单），但 owner 端的 withdraw 确认流不在 Agent Wallet UI 里
- **自治故事**：在跑的 wealth loop（`AUTONOMY_*`、Freqtrade、`chain_execute_trade_intent`）现实里有但 deck 里没讲；这是后端能力被 pitch 低估，不是 gap

---

## 2. 锁死的范围（来自对齐过程）

| 维度 | 决策 | 说明 |
|---|---|---|
| 目标 | **B-pilot**：Base mainnet + Circle 生产，邀请制 5–20 个 agent dev | 不公开注册，不做 SOC2/MSB |
| 时间 / 人力 | **2–3 月 × 3–4 人 ≈ 24–36 工程师周** | 比 B 原本 6 月窗口紧 50–70%，必须取舍 |
| M5 信誉 | **Raw 版**：埋点 + 简单 reject-rate / volume 看板 | 不做评分模型 / verified / 抗博弈，约 2–3 周 |
| 法币 | **Coinbase Onramp**（已锁定，2026-05-06），**提现走链上 BYO** | 保留原型 M3 故事，KYC 由 Coinbase 扛；不接法币出金 |
| 复用基线 | `agent` / `chain` / `ledger` / `x402-seller` **70% 改造，非重写** | 现有代码已经覆盖 M2–M4 真实路径 |

**明确推 v2**

- M5 评分模型 / verified 标识 / 抗博弈
- 法币提现
- 多区域部署
- 自托管 / BYOW 支线
- 公开注册 / 增长漏斗
- SOC2 / MSB 等正式合规

---

## 3. v1 工程必须啃的硬骨头

| # | 项目 | 与现状的 gap | 粗估 |
|---|---|---|---|
| 1 | 单租户 JSON state → 多租户 Postgres | 现在 `agent` / `ledger` 都用本地 JSON 文件 | 3–4 周 |
| 2 | Circle entity secret + signer key 进 KMS / Vault | 现在用 `.env` | 1 周 |
| 3 | Mainnet 风控：单笔 / 日 / 月度上限、白名单、kill switch、审计流水 | 部分有（`SINGLE_TX_CAP` 等），但缺审计 + kill switch | 2 周 |
| 4 | Owner GitHub OAuth ✅ + Agent API key + revocation + scope | Agent 端鉴权基本空白 | 1.5 周 |
| 5 | 部署基建：单区域、托管 PG、容器平台、CI/CD、密钥管理 | 当前只有 docker compose | 2–3 周 |
| 6 | 可观测：结构化日志 + 指标 + 告警 + on-call | 基本空白 | 1.5 周 |
| 7 | Onramp 集成（选一家）：webhook 回执 + 对账 + 失败补偿 | 完全空白 | 2–3 周 |
| 8 | Withdraw owner 确认流：UI + 邮件 / TOTP 二次校验 + 风控 | 链上转账有，UI / 二次校验空白 | 1.5 周 |
| 9 | Web Console：原型视觉移植 + 核心流主路径 | 当前 console 极简 | 3–4 周 |
| 10 | M5 raw 版事件流 | 完全空白 | 2 周 |
| 11 | Pilot SDK（TS 或 Python，先一种）：x402 + ledger client + A2A 例子 | 完全空白 | 1.5 周 |
| 12 | A2A 协议 v1 文档化（保持 Chief 服务端中介） | 现在是 agent 内部约定，不是协议 | 1 周 |

> 总粗估：**~22–28 周**。3–4 人 × 3 月 = 36 周，缓冲 ~25–35%，**没有冗余**，遇到任何意外就要砍内容。

---

## 4. 三条执行路线

### 路线 A：原地加固（Strangler）

保持现在的 monorepo 服务边界（`agent` / `chain` / `ledger` / `x402-seller`），逐个把 JSON state 替换成 Postgres + 真实多租户 + 风控 + 鉴权。

- ✅ 最快交付，最低风险
- ✅ 团队不需要重新理解代码地形
- ❌ `agent` 容器同时是 LLM 大脑 + owner HTTP API，多租户 + 扩缩容场景下会撞车
- ❌ 这个混合问题会带到 v2

### 路线 B：领域重画

显式重画服务：
- `wallet-api`：owner / auth / 钱包 CRUD / ledger 转发
- `agent-orchestrator`：LLM / tools
- `ledger`：结算
- `gateway`：onramp + 托管胶水

- ✅ v2 友好，边界清晰
- ❌ 开头 4–6 周在重画上花掉，等于 1.5 个工程师全月没交付
- ❌ 2–3 月窗口里风险过高

### 路线 C（推荐）：最小重画 + 原地加固

只做一处必要重画：把 `agent` 拆成

- `wallet-api`：HTTP / owner / agent / ledger 转发
- `agent-brain`：LLM 编排（LangGraph）

其它一切（`chain` / `ledger` / `x402-seller`）原地加固。

- ✅ 1 周重画 + 1 周布线，代价可控
- ✅ 把 v2 一定会撞车的边界提前画掉
- ✅ 剩下时间全部用来加固和补 onramp / withdraw / SDK

**推荐理由**：现在 `agent` 容器同时对外暴露 owner HTTP + 跑 LangGraph，这两条职责的扩缩容、超时、错误域、鉴权策略完全不同。多租户引入后强行不拆等于在 v2 给自己埋一个 P0。但 1 周重画的代价是可以承受的。

---

## 5. 待决策项

- [ ] **路线 A / B / C 选择**（推荐 C）

待路线确认后，本文档将转写为正式设计文档（含详细数据模型、API 边界、里程碑甘特、风险登记），路径：

```
docs/superpowers/specs/2026-05-05-agent-wallet-v1-design.md
```

---

## 6. 备忘 — 已淘汰的选项

便于以后回看，记录哪些路径在对齐过程中被明确否决：

- **目标 A（投资人 demo 级）**：太轻，撑不起"生产级"的定义
- **目标 C（公开 GA）**：9–12 月窗口超出团队当前节奏
- **M5 完整版 / 含评分**：5–7 周，会吃掉一个全职工程师 v1 全程
- **M5 完全推到 v2**：会丢掉 demo 故事的承接，且不留数据底座
- **法币入金 + 法币提现（Circle Mint / 银行通道）**：6 周以上 + 外部审批，超窗口
- **完全 BYO USDC（无 onramp）**：保住合规但丢掉 M3 完整故事
- **从 0 重写**：放弃现有 70% 已实现资产，明显错误
- **路线 A'（在现有 repo 内增量演进）**：v1.1 改名/迁移成本和 B' 开新 repo 一次性成本相当，B' 的长期可维护性更好（Phase 3）
- **路线 C'（双 repo 过渡）**：3 人小队维护成本翻倍（Phase 3）
- **把 Claude Code 杠杆吃满去压缩排期**：等同于把 mainnet money 的安全边际换进度，对真钱产品是坏 trade（Phase 3）

---

## 7. Phase 2 — 原型 internal stage 更新（2026-05-06）

`localhost:10000` 原型新增 8 个 `internalOnly` stage，是首次对工程语义做正式表述 —— 把工程合同写到 demo 里给 SDK 集成方看。

### 7.1 新增 stage 与产品契约

| 新 stage | 透露的产品契约 | v1 增量 |
|---|---|---|
| `m3_processing__internal_stuck` | Deposit 是异步管道，有 stuck queue；Base gas spike 是已知运营场景 | 新增：deposit 状态机 + 重试队列 + ops 视图（~1w） |
| `m4_n1__internal_expired` | SDK 有 **5 档返回 taxonomy**，`failed_terminal` 是其中一档 | SDK 升成正式状态机；契约文档 + 测试矩阵（~0.5w） |
| `m4_n2__internal_first_payee` | **首次付款给新 payee 必须 owner 审批**，webhook 驱动；SDK MUST NOT retry | 新增 v1 feature：owner 审批通道 + webhook 回执（~1.5w） |
| `m4_n2__internal_limit_exceeded` | 限额是 operator-configurable，first-payee 是硬编码 | 显式两条不同处置路径（~0w 增量） |
| `m4_n4__internal_reject` | Buyer 主观拒绝**会写入 seller credit**；产品不假装能客观判定交付 | 升级 M5 raw：从纯事件流升成"带 reject 原因聚合 + 影响他方信号"（+1w） |
| `m4_n4__internal_timeout` | **24h buyer-silent → auto-release**，默认信任 seller | 新增 `ledger` 端 timer / cron（~0.5–1w） |
| `m5__internal_anti_abuse` | "**One rule, no algorithm**" —— 不上模型，只一条显式规则（reject-rate × volume 阈值触发 freeze） | 升级 M5 raw：加 freeze action + 一条显式规则（~0.5w） |
| `m6__internal_reconciliation` | 唯一不变量：`ledger 内部账面 ≡ Circle delegation total` | 新增 v1 必做：批对账 job + dashboard + ops alert（~1.5w） |

### 7.2 新增组件
- `SDKReturnCallout`：5 档 taxonomy 在 SDK 返回中的可视化
- `RejectReasonModal`：N4 reject 原因结构化录入
- `ReconciliationDashboard`：Chief 账面 vs Circle delegation 实时差异

### 7.3 影响

- 新增工程量 **~6–8w**
- 强化路线 C 的合理性 —— first-payee 审批 / reconciliation / anti-abuse / stuck-deposit 全部落在 owner / wallet 治理域

---

## 8. Phase 3 — Pivot 至 skill-vendor + Eigenflux 集成 + 路线 B'（2026-05-06）

### 8.1 产品形态校准

| 维度 | 旧（B-pilot 假设） | 新（plugin/skills + Eigenflux） |
|---|---|---|
| 我们做什么 | Chief = 跑 LangGraph 的 agent 平台 | Chief = **钱包 + 账本 + 信用 + 风控**基础设施层，**不跑 agent**；用户在他们的 stack 里挂我们的 skill |
| 用户是谁 | 最终用户 + agent 开发者 | **agent 开发者**（LangChain / Cursor / Claude Code / Vercel AI SDK / 自研 stack） |
| 网络面 | 自有 A2A 协议（Chief 中介） | 跑在 **Eigenflux 网络** 上（链下 REST）；Chief 只承接资金 + 风控 + 账本 |

### 8.2 Eigenflux 边界（确认版）

> **Eigenflux** 是**纯消息/身份网络层**（链下 REST），不碰钱、不做信誉、不参与争议。
> **Chief** 是**全部金钱栈**：托管（Circle Gateway）、结算（ledger）、信用信号（M5 raw）、风控、审批、对账、争议仲裁、风险承担。
> 两边都有 agent identity：Eigenflux 颁发的网络身份用来发现/通信，Chief 颁发的钱包身份用来授权付款。

#### Eigenflux 答复要点（来自 2026-05-06 对齐）

- **A.1** 网络层
- **A.2** 并行；Eigenflux 不关心钱
- **A.3** 暂时不考虑 verified 徽章
- **B.4** REST API
- **B.5** 链下网络
- **B.6** Eigenflux 有自己的 agent identity；Chief **也要做自己的**（双层）
- **C.7** 涉及钱的部分都走 Chief
- **C.8** 24h auto-release timer 在 Chief `ledger`
- **D.9** 资金真实托管在 Circle Gateway
- **D.10** 出风险时 Chief 承担
- **D.11** Agent 身份从 Eigenflux 来
- **E.12** 暂时不考虑收费
- **E.13** 失败 / 争议仲裁权在 Chief
- **F.14** Eigenflux 不参与信誉服务（M5 raw 完全归 Chief）

#### Eigenflux 答复要点 — Round 2（来自 2026-05-06 第二轮，针对简化版 Q1–Q5）

- **R.Q1 身份认证**：Eigenflux **只给 Agent ID，无密码学**；ID 在 agent 全生命周期唯一稳定（无轮换） → **Chief 必须自建 credential 层用于资金授权**
- **R.Q2 状态通道**：仅 pull 查询，无 webhook 推送 → Chief 在钱关键路径同步查 + 缓存 30s TTL
- **R.Q3 Quote 验真**：v1 不防伪造，质量责任由 buyer 自判 → N2 Lock 不验签 quote，所有欺诈风险压到 reject path + anti-abuse 单规则
- **R.Q4 消息接口**：双向（Chief→Agent 和 Agent→Chief 都可），交付保证 / 去重细节待联调
- **R.Q5 沙箱**：存在；URL / 测试 ID / 契约一致性等具体接入参数推到 Phase A 联调时再要

#### Round 2 对设计模型的关键修订

1. **认证模型反转**：Eigenflux ID **仅识别 / 路由用**，授权完全靠 **Chief credentials**（API key + HMAC 签名 + audit log）
2. **Quote 验真砍掉**：N2 Lock 路径中"验 Eigenflux 签名"步骤不存在，buyer 自报 amount/payee 即直接锁
3. **质量责任全部下沉**：N3 Verify、N4 Reject、anti-abuse 三道闸门变成唯一防御；M5 raw 从"重要"升到"载荷件"
4. **撤销改为 owner 端 kill-switch 主导**：Eigenflux 不推送撤销，Chief owner 自己掌握"我的 agent 不能再花钱"按钮，不依赖 Eigenflux 状态及时性

### 8.3 A2A N1–N5 步骤归属

| 步骤 | 走哪边 | 理由 |
|---|---|---|
| N1 Quote | **Eigenflux 撮合 + 服务目录** | 不涉及钱；Eigenflux 主动撮合并提供 directory，Chief **不验证撮合结果真伪**（R.Q3） |
| N2 Lock | **Chief ledger** | 涉及钱；buyer 凭 Chief credential 自报 amount/payee 锁单 |
| N3 Verify | Eigenflux 消息 + 读 Chief ledger 状态 | seller 直接查我们的公开 escrow 状态 |
| N4 Deliver | Eigenflux 消息 + Chief 24h timer | 交付声明走网络，过期由我们处理；reject 写入 M5 |
| N5 Release | **Chief ledger** | 涉及钱 |

### 8.4 v1 服务结构（最终版）

**淘汰**（不进 v1）：
- 旧 `agent`（LangGraph 大脑、autonomy loop、wealth sub-agent）
- 旧 `freqtrade` MCP 整条线
- 任何"我们自己跑 agent"的产品语义

**保留 + 升级**：
- `services/ledger/`：v1 价值核心，加 escrow timer、reject 原因、anti-abuse、reconciliation
- `packages/x402-buyer/`：从旧 `chain` 提炼的 lib
- `packages/circle-custody/`：Circle wallet provisioning + entity secret 处理
- `packages/risk-policy/`：caps、白名单、kill-switch
- `packages/payment-router/`：原 `route_payment_intent` 路由规则

**新建**：
- `services/wallet-api/`：owner 域 HTTP（OAuth、wallet CRUD、first-payee 审批、限额配置、Console 后端、Eigenflux 身份绑定）
- `services/skill-server/`：对外 plugin/skill 入口，承载 buyer/seller skill；**v1 唯一交付目标 = OpenClaw plugin**（同时支持 MCP server 和 OpenClaw native plugin 两种格式）；分发渠道 = **GitHub repo**，用户用 `openclaw plugin install <github-repo>` 安装；credential 接入走 **OAuth device-code flow**，由 Owner Console 完成确认；REST 面保留作为内部 / 未来扩展契约，v1 不对外承诺稳定性
- `services/eigenflux-client/`：薄薄一层 REST 客户端 + 身份解析缓存

#### 服务图

```
[ user agents on Eigenflux network ]
              │
              │ MCP / REST
              ▼
       [ skill-server ]  ─────► chain lib ─► Base mainnet (USDC / x402)
              │                       │
              │ internal API          └► Circle Gateway (custody)
              ▼
       [ wallet-api ] ◄── Owner Console ─── owner browser
              │
              ▼
         [ ledger ] ─── Postgres
              │
              └► batch reconciler ─► Circle delegation

       [ eigenflux-client ] ◄─── used by skill-server / wallet-api
              │
              ▼
        Eigenflux REST
```

### 8.5 路线 B'：开新 repo `chief/`

**搬运清单**：

| 从旧 repo 提炼到 `chief/` | 处置 |
|---|---|
| `ledger/`（FastAPI + escrow 逻辑 + 测试） | 整包搬，目录改名 `services/ledger/` |
| `chain/` 里的 x402 buyer 流 | 提炼成 `packages/x402-buyer/` |
| `chain/` 里的 Circle wallet provisioning | 提炼成 `packages/circle-custody/` |
| `chain/` 的链上风控 caps | 提炼成 `packages/risk-policy/` |
| `route_payment_intent` 的路由规则 | 搬到 `packages/payment-router/` |
| `agent/` / `freqtrade/` / `autonomy/` | **不搬**，旧 repo 作为历史归档 |
| 旧 `docker-compose.yml` | 不搬，新 repo 用 Terraform + ECS Fargate |

搬运 + lib 重打包成本：**约 2–3w**。

### 8.6 AWS + 监控范围

| 模块 | 选型 | 周数 |
|---|---|---|
| 计算 | ECS Fargate（单 cluster，每服务一个 service） | 1 |
| 数据库 | RDS PostgreSQL（prod multi-AZ + staging single） | 0.5 |
| 密钥 | Secrets Manager + KMS（Circle entity secret、signer key、API token） | 0.5 |
| 网络 | VPC、public/private subnet、ALB + ACM | 0.5 |
| 异步 | SQS（stuck-deposit、webhook 重投）、EventBridge（24h timer、每日 reconciler） | 0.5 |
| CI/CD | GitHub Actions → ECR → ECS（含 staging→prod 手工 gate） | 0.5 |
| 日志 / 指标 / 追踪 | CloudWatch Logs + **AMP + AMG**（OpenTelemetry + ADOT sidecar 推送）；X-Ray 按需 | 1.5–2 |
| 告警 | CloudWatch Alarms → SNS → Slack（v1 不上 PagerDuty） | 0.3 |
| IaC | Terraform（modular，env per workspace） | 1 |
| 安全基线 | IAM least-priv、GuardDuty、AWS Config、S3 access logs | 0.5 |
| 备份 / 合规 | RDS 自动备份 + 跨 AZ、30 天点恢复、KMS rotate | 0.2 |
| 运维 runbook | mainnet kill-switch、reconciler 失败处置、Circle webhook 中断处置 | 0.5 |
| **小计** | | **~7w** |

### 8.7 Claude Code 杠杆模型

| 工种 | 占比 | 倍数 |
|---|---|---|
| Boilerplate / scaffolding（schema、CRUD、SDK client、IaC、测试） | ~30% | 2–3× |
| 集成胶水（Eigenflux REST、Circle、onramp、x402） | ~20% | 1.5–2× |
| 域逻辑（state machine、escrow、reconciler、M5 raw、风控规则） | ~35% | 1.3–1.5× |
| 安全 / mainnet 边缘 / 调试 / 威胁建模 | ~15% | 1.0–1.2× |

加权 ≈ **1.5×**。**不直接吃进排期**，作为缓冲 + 质量富余 + 风险吸收。

### 8.8 最终工程量

| 大块 | 周数 |
|---|---|
| 原 12 项硬骨头 | 22–28 |
| Phase 2 八个 internal stage 带来的新工作 | 6–8 |
| Pivot 调整：–10w（砍 agent/freqtrade）+6w（plugin 包装 + Eigenflux + 身份绑定） | –4 |
| 路线 B' 搬运成本 | 2–3 |
| AWS + 监控展开（净增） | 2 |
| **小计 Raw** | **28–37w** |

3 人容量：

| 排期 | 容量 | 缓冲 |
|---|---|---|
| 3.0 月 | 36w | -3% ~ +29%（边缘） |
| **3.5 月（推荐）** | **42w** | **+14% ~ +50%** |
| 4.0 月 | 48w | +30% ~ +71%（保守） |

### 8.9 里程碑（粗略）

```
Phase A 基础设施
M0  W0       开 repo / IaC 框架 / Postgres / Auth / 监控骨架
M1  W3-4     ledger 搬运 + 多租户 + 测试通过
M2  W6       chain lib 拆分 + skill-server 第一刀（buyer skill）
M3  W8       wallet-api owner 域 + first-payee 审批 + Console v0

Phase B 产品
M4  W10      Eigenflux 身份绑定 + identity webhook
M5  W12      M5 raw 事件流 + reject path + anti-abuse 规则
M6  W14      Onramp 集成 + reconciliation job
M7  W15      Withdraw 流 + 24h timer + stuck-deposit queue
M8  W16      OpenClaw plugin v1 GA（含 OAuth device-code flow） + 5 档 taxonomy + 集成测试

Phase C 上线
M9  W17      Mainnet dry-run
M10 W18-19   邀请 5–20 dev alpha + 监控调阈值
M11 W20+     生产切流
```

### 8.10 v1 设计阶段需要单独成页的子主题

- **【P0】身份绑定 & 鉴权威胁模型**：Eigenflux 不提供密码学认证，Chief credential 是 v1 唯一的资金授权门 —— 必须独立威胁建模（含 R1 攻击场景、credential lifecycle、kill-switch、audit log、密钥轮换）
- **5 档 SDK return taxonomy 契约**：`success` / `pending_approval` / `failed_retryable` / `failed_terminal` / `unknown`
- **Reconciliation 不变量**：`Σ(ledger.balances + ledger.escrows.locked) ≡ Circle.delegation_total` 的失败处置 SOP
- **First-payee 审批语义**：webhook 重投策略、approval 撤销窗口、SDK 端 `pending_approval` 不可重试的强制规则

### 8.11 风险登记（Round 2 后）

| ID | 风险 | 应对 |
|---|---|---|
| **R1** | Eigenflux ID 可被冒名（无密码学校验） | Chief credentials 强：HMAC 签名 + 短 TTL token + 旋转 + audit log；owner 端 kill-switch 不依赖 Eigenflux |
| **R2** | 撮合结果不验签（R.Q3） | mainnet 头一个月：≥ $10 等值 lock 请求**人工 review**；anti-abuse 阈值开严；发现伪造模式立即 freeze；M5 raw 数据保留以便回溯 |
| **R3** | Eigenflux 状态 pull-only（R.Q2） | Chief 缓存命中即放行；缓存失效时**降级**：拒绝高额操作，保留低额操作；Eigenflux 不可用时核心 lock/release 链路不阻塞（owner kill-switch 是最终防线） |

---

## 9. 待决策项（Phase 3 后）

- [x] 产品定位（B-pilot）
- [x] 团队规模（3 人）
- [x] M5 范围（raw 版）
- [x] Onramp 策略（第三方）
- [x] 路线选择（B'）
- [x] 排期（3.5 月推荐）
- [x] AWS / 监控范围
- [x] Claude Code 杠杆使用方式（缓冲，不压缩）
- [x] Eigenflux Round 2 答复（Q1–Q5 全部到位）
- [x] 正式 design.md 落笔（文件名 `2026-05-05-agent-wallet-v1-design.md`，2026-05-06 起草）
