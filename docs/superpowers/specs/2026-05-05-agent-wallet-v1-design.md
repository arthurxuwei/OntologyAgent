# Agent Wallet (Chief) v1 — 正式设计

> **状态**：起草中（Draft 0.1）
> **日期**：2026-05-06 起草
> **依赖**：`2026-05-05-agent-wallet-v1-brainstorm.md`（v1 共识）+ `2026-05-06-eigenflux-integration-questions.md`（Eigenflux 答复已收）
> **下一步**：评审通过后由 `superpowers:writing-plans` 出实现计划

---

## 1. 目标 & 非目标

### 1.1 v1 目标

- **B-pilot 上线**：Base mainnet + Circle Gateway 真实托管，邀请制 5–20 个 agent 开发者
- **承担全部金钱栈**：托管 / 结算 / 信用信号 / 风控 / 争议仲裁 / 风险
- **Plugin/Skill 形态**：v1 唯一目标 = **OpenClaw plugin**（同时打包为 MCP server 和 OpenClaw native plugin），分发到 GitHub repo，OpenClaw 用户用 `openclaw plugin install <repo>` 安装；Eigenflux 可推 webhook 触发 OpenClaw 提示用户安装
- **运行在 Eigenflux 网络上**：身份 + 撮合 + 消息走 Eigenflux，钱走 Chief
- **3.5 月 / 3 人 / 29.5–38.5w 工程量**

### 1.2 v1 非目标（明确推 v2）

- 信誉评分模型 / verified 徽章 / 抗博弈算法
- 法币提现（仅链上 BYO）
- 公开注册 / 公开增长漏斗
- SOC2 / MSB 等正式合规
- 多区域 / 自托管支线
- 自有 agent runtime（LangGraph 大脑、autonomy loop）
- 量化 / Freqtrade 类垂直 agent

---

## 2. 架构总览

### 2.1 服务拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│             user agents on Eigenflux network                    │
└────────────────┬────────────────────────────────────┬───────────┘
                 │ MCP / REST                         │ Eigenflux REST
                 │ (Chief credential auth)            │
                 ▼                                    ▼
        ┌──────────────────┐                ┌─────────────────┐
        │  skill-server    │                │  Eigenflux       │
        │  (buyer + seller)│                │  network         │
        └────┬─────────────┘                │  (matchmaking,   │
             │                              │   directory,     │
             │ internal API                 │   messaging)     │
             ▼                              └────────┬────────┘
        ┌──────────────────┐                         │
        │   wallet-api     │◄───── Owner Console ◄──── owner browser
        │   (owner / auth /│
        │    binding /     │      ┌──────────────────┐
        │    first-payee)  │      │ eigenflux-client │
        └────┬─────────────┘──────│ (REST + cache)   │
             │                    └──────────────────┘
             ▼
        ┌──────────────────┐                ┌─────────────────┐
        │     ledger       │───────────────►│   Postgres      │
        │  (escrow / M5    │                │  (multi-tenant) │
        │   raw / timer)   │                └─────────────────┘
        └────┬─────────────┘
             │
             ▼
        ┌──────────────────┐                ┌─────────────────┐
        │  reconciler      │───────────────►│ Circle Gateway  │
        │  (batch job)     │                │ (custody)       │
        └──────────────────┘                └─────────────────┘
                                                     ▲
                                                     │
        ┌──────────────────┐                         │
        │  chain lib       │─────────────────────────┘
        │  (x402 buyer,    │     ┌──────────────────┐
        │   risk policy,   │────►│  Base mainnet    │
        │   USDC transfer) │     │  (USDC / x402)   │
        └──────────────────┘     └──────────────────┘
```

### 2.2 服务清单

| 服务 / 包 | 职责 | 形态 |
|---|---|---|
| `services/skill-server` | Plugin/skill 入口，承载 buyer skill + seller skill | ECS Fargate service，对外 |
| `services/wallet-api` | Owner 域 HTTP，OAuth、wallet CRUD、credential issue、first-payee 审批、限额配置 | ECS Fargate service，对外（Owner Console）+ 对内（skill-server） |
| `services/ledger` | Escrow 状态机、credit、M5 事件流、timer、anti-abuse | ECS Fargate service，仅内部访问 |
| `services/reconciler` | 每日批量对账 job | ECS Fargate scheduled task |
| `packages/x402-buyer` | x402 buyer 流（lib） | npm 包，被 skill-server 引用 |
| `packages/circle-custody` | Circle wallet provisioning + entity secret 处理（lib） | npm/python 包 |
| `packages/risk-policy` | Caps / 白名单 / kill-switch（lib） | npm 包 |
| `packages/payment-router` | Route_payment_intent 规则（lib） | npm 包，被 skill-server 引用 |
| `packages/eigenflux-client` | Eigenflux REST 客户端 + 30s 状态缓存（lib） | npm 包，被 skill-server / wallet-api 引用 |
| Owner Console | Web UI（owner 自服务） | 静态 SPA，CloudFront + S3 |

### 2.3 部署形态

- **AWS ECS Fargate**：单 cluster，每服务一个 service，按需扩缩
- **RDS PostgreSQL**：prod multi-AZ + staging single
- **Secrets Manager + KMS**：Circle entity secret、signer key、HMAC master key
- **SQS**：stuck-deposit recovery queue、webhook 重投
- **EventBridge**：24h auto-release timer、每日 reconciler、credential rotation
- **CloudWatch Logs + AMP + AMG**（Amazon Managed Prometheus + Amazon Managed Grafana，via OpenTelemetry SDK + ADOT sidecar 推送），AWS X-Ray 按需补；月成本预算 ~$80
- **OpenTelemetry 必须从 day 1 装好**：哪怕 v1 不开 tracing，instrumentation 必须先到位，避免 mainnet 跨服务排障时再补
- **Terraform**：IaC，env per workspace

---

## 3. 数据模型

### 3.1 核心表（Postgres）

> 仅列关键表 + 关键字段，详细 DDL 在实现期生成。

#### `owners`

```
id            uuid PK
github_id     text UNIQUE
email         text
created_at    timestamptz
disabled_at   timestamptz NULL  -- owner 级 kill-switch
```

#### `wallets`

```
id              uuid PK
owner_id        uuid FK → owners
circle_wallet_id text  -- Circle Gateway 钱包
address          text  -- Base 链上地址
state            enum('active', 'frozen', 'disabled')
single_tx_cap_usdc    numeric(36,6)  -- 单笔上限
daily_cap_usdc        numeric(36,6)
monthly_cap_usdc      numeric(36,6)
created_at       timestamptz
```

#### `agent_bindings`（Eigenflux ID ↔ Chief wallet 绑定）

```
id                uuid PK
owner_id          uuid FK → owners
wallet_id         uuid FK → wallets
eigenflux_agent_id text UNIQUE  -- Eigenflux 颁发，全生命周期稳定（R.Q1）
display_name      text
created_at        timestamptz
revoked_at        timestamptz NULL  -- owner kill-switch
```

> **基数**：v1 默认 1:1（一个 Eigenflux agent 绑一个钱包）；多对一 / 一对多 v2 再说。

#### `agent_credentials`

```
id              uuid PK
binding_id      uuid FK → agent_bindings
key_id          text UNIQUE  -- 公开标识（请求里带这个）
secret_hash     text  -- HMAC secret 的哈希（argon2id）
scopes          text[]  -- v1 canonical: 'lock' | 'release' | 'deliver' | 'x402' | 'route'
expires_at      timestamptz
revoked_at      timestamptz NULL
created_at      timestamptz
last_used_at    timestamptz
```

#### `escrows`（沿用 ledger v1）

```
id              uuid PK
buyer_binding_id  uuid FK → agent_bindings
seller_binding_id uuid FK → agent_bindings
amount_usdc       numeric(36,6)
state           enum('LOCKED', 'RELEASED', 'REFUNDED', 'EXPIRED')
locked_at       timestamptz
delivered_at    timestamptz NULL  -- N4 delivery claim
released_at     timestamptz NULL
refunded_at     timestamptz NULL
expires_at      timestamptz       -- N4 24h auto-release deadline
quote_metadata  jsonb             -- buyer 自报，不验签（R.Q3）
```

#### `events`（M5 raw + audit log）

```
id              bigint PK
binding_id      uuid FK → agent_bindings  -- 主体
event_type      text  -- 'escrow.locked', 'escrow.released', 'escrow.rejected', ...
counterparty_binding_id uuid NULL
amount_usdc     numeric(36,6) NULL
reject_reason   jsonb NULL  -- N4 reject 原因（结构化）
metadata        jsonb
occurred_at     timestamptz
ingested_at     timestamptz
```

> **不可变 append-only 表**。M5 信用聚合从这里离线计算（v1 raw 版仅做 reject_rate × volume）。

#### `first_payee_approvals`

```
id              uuid PK
buyer_binding_id  uuid FK → agent_bindings
seller_binding_id uuid FK → agent_bindings
state           enum('pending', 'approved', 'rejected', 'expired')
requested_at    timestamptz
decided_at      timestamptz NULL
expires_at      timestamptz  -- 7 天
```

#### `deposits` / `withdrawals` / `reconciliation_runs`

> 略，按状态机 §5 展开。

### 3.2 关键索引

- `agent_bindings(eigenflux_agent_id)` UNIQUE
- `agent_credentials(key_id)` UNIQUE
- `escrows(state, expires_at)` 用于 timer 扫描
- `events(binding_id, occurred_at DESC)` 用于 M5 聚合

---

## 4. 鉴权 & 身份模型（v1 资金授权门）

> 因 Eigenflux 不提供密码学认证（R.Q1），本节是 v1 唯一的资金授权护栏，**P0 等级**。

### 4.1 双层身份

- **Eigenflux Agent ID**：标识 / 路由用，**绝不用于授权**。可视为公开信息。
- **Chief Credentials (key_id + secret)**：授权用。每个 `agent_binding` 有 0–N 个 active credential。

### 4.2 Credential 颁发流程

v1 的标准方式是 **OAuth device-code flow**（详见 §6.1 客户端范围）—— plugin 自己拉 credential，不经 owner 手工复制粘贴。流程：

```
准备阶段（owner 在 Console 一次性完成）：
  1. Owner GitHub OAuth → owners 表
  2. Owner 在 Console 创建 wallet（Circle Gateway 调用 → wallets 表）
  3. Owner 在 Console 输入 Eigenflux Agent ID（手工 paste）
  4. Chief 调 Eigenflux REST 验证该 ID 存在且状态 active → agent_bindings 表

每次 agent / plugin 实例登录（device-code flow）：
  5. Plugin 调 POST /v1/oauth/device/authorize → device_code + user_code + verification_uri
  6. 用户在浏览器进入 verification_uri，输入 user_code，选 binding + scope，确认
  7. Chief 生成 key_id (16 bytes) + secret (32 bytes)
     - secret 仅在 device-code 轮询响应中明文返回一次；DB 仅存 argon2id hash
  8. Plugin 收到 (key_id, secret) 存进本地 secure store
```

> 同一个 binding 可以挂多个 active credential（dev / staging / prod），按 §4.4 lifecycle 各自独立旋转。

### 4.3 请求鉴权（HMAC-SHA256 签名）

每个 agent → skill-server 请求带：

```
X-Chief-Key-Id: <key_id>
X-Chief-Timestamp: <unix_ms>
X-Chief-Nonce: <16 bytes hex>
X-Chief-Signature: hmac_sha256(secret, signing_string)

signing_string = METHOD || '\n' ||
                 PATH || '\n' ||
                 X-Chief-Timestamp || '\n' ||
                 X-Chief-Nonce || '\n' ||
                 sha256(BODY)
```

**校验**：
- timestamp 偏差 ≤ 5 分钟（防重放窗口）
- nonce 在 Redis 里 5 分钟去重
- secret hash 比对（argon2id verify）
- key 未 expire / revoke
- scope 包含本接口需要的 scope

### 4.4 Credential 生命周期

| 事件 | 触发 | 处理 |
|---|---|---|
| 颁发 | Owner 在 Console 创建 | 默认 TTL 90 天 |
| 旋转 | Owner 主动旋转 / TTL 到期 | 旧 key 立即 revoke，新 key 颁发，重叠期 0（不做 grace） |
| 撤销 | Owner 主动 / 检测到滥用 | `revoked_at = now()`，所有 in-flight 请求继续完成（不打断 escrow），新请求拒绝 |
| Owner kill-switch | Owner 触发 | `owner.disabled_at` 设置；该 owner 下所有 binding 的 credential 全部失效 |

### 4.5 审计日志

每次鉴权成功 + 每次拒绝都写 `events` 表（`event_type='auth.succeed'` / `'auth.reject'`），保留 ≥ 1 年。Owner Console 可查最近 30 天。

---

## 5. 状态机

### 5.1 Escrow

```
       create
   nil ──────► LOCKED
              │  ├─── (deliver claim, optional) ──► LOCKED (delivered_at set)
              │  │
              │  ├─── (buyer release N5) ────────► RELEASED
              │  ├─── (buyer reject within 24h) ─► REFUNDED
              │  └─── (24h timeout, no action) ──► EXPIRED → auto RELEASED
              │
              └─── (anti-abuse freeze) ──────────► (transition blocked, owner manual unlock)
```

**关键不变量**：
- `LOCKED` 时 `amount` 同时计入 buyer 的 `locked_balance`（不可花）
- `RELEASED` 把 amount 移到 seller 的 `available_balance`
- `REFUNDED` / `EXPIRED→REFUNDED` 退回 buyer `available_balance`
- 终态（`RELEASED` / `REFUNDED`）不可再改
- `EXPIRED→RELEASED` 由 24h timer 触发，事件记录区分手动 release 和 timeout release

### 5.2 Deposit（Circle Gateway 入金）

```
   入金来源:
     ├─ 链上 BYO：dev 自己从外部钱包转 USDC 到 Circle Gateway 地址
     └─ Coinbase Onramp：dev 用信用卡，Coinbase 把 USDC 直接发到该地址

   PENDING ──► CONFIRMED  (Circle webhook，无论来源都走同一通道)
      │
      └──► STUCK (>10 min 未 confirm) ──► RECOVER_QUEUED ──► CONFIRMED / FAILED
                                                              │
                                                              └─► ops review
```

> **Coinbase Onramp 集成形态**：使用 [Coinbase Onramp](https://docs.cdp.coinbase.com/onramp/) 的托管 widget（前端 SDK）+ session API（后端）。Owner 在 Console 发起 onramp session（`POST /v1/onramp/sessions` 返回 widget URL），dev 完成支付后 Coinbase 将 USDC 直接打到指定 Circle Gateway 地址。**Chief 不接触卡号、不做 KYC**。Coinbase 的 webhook 用于 UI 进度展示；deposit 状态机以 Circle webhook 为唯一权威触发，不依赖 Coinbase webhook 推进资金状态（避免双源对账）。

### 5.3 Withdraw（链上转出）

```
   REQUESTED  ──► AWAITING_OWNER_APPROVAL  (any withdraw 都需要 owner 确认)
                       │
                       ├─► APPROVED (owner 在 Console 点确认 + TOTP)
                       │       │
                       │       ▼
                       │   BROADCAST ──► CONFIRMED / FAILED (Base 链)
                       │
                       └─► REJECTED (owner 拒绝 / 24h 未确认 → expire)
```

### 5.4 First-payee approval

```
   buyer call N2 Lock with new (buyer, seller) pair
           │
           ▼
   pending_approval (return SDK code)
           │
           ├─► owner 在 Console 通过 ──► proceed to LOCKED
           ├─► owner 拒绝 ──► escrow 不创建
           └─► 7 天未决定 ──► expired
```

> **SDK 强约束**：`pending_approval` 状态下 SDK MUST NOT retry；必须等 webhook 通知或下次 polling。

---

## 6. API 表面

### 6.1 skill-server（对外，agent 用）

#### 客户端范围（v1 单一目标）

v1 唯一交付目标 = **OpenClaw plugin**。受众 = OpenClaw 终端用户（不写 agent 代码、不直接调 LangChain / Claude API / OpenAI Assistants）。

| 客户端 | 接入方式 | v1 优先级 |
|---|---|---|
| **OpenClaw** | 同时打包为 MCP server 和 OpenClaw native plugin；分发到 **GitHub repo**；用户用 `openclaw plugin install <github-repo>` 安装 | P0（v1 唯一目标） |

**v1 不在范围**：MCP 客户端通用支持（Cursor / Cline / Windsurf 等）/ OpenAI Assistants / LangChain / Vercel AI SDK / 自研 stack。REST 接口保留作为内部 / 未来扩展契约，**v1 不对外承诺稳定性**，也不交付集成示例。

**远程触发安装**：Eigenflux 可推 webhook 给用户的 OpenClaw 实例建议安装我们的 plugin；最终是否安装由用户在 OpenClaw 内确认。v1 不强制注册到 Eigenflux 网络目录。

**Credential 注入：OAuth device-code flow**

```
1. 用户在 OpenClaw 内运行: openclaw plugin agent-wallet login
2. Plugin 调 POST /v1/oauth/device/authorize
   → 返回 device_code (轮询用) + user_code (人类可读) + verification_uri
3. Plugin 在终端展示 user_code 和 verification_uri，并开始轮询
4. 用户在浏览器打开 verification_uri，登录 GitHub OAuth（Owner 身份）
5. Owner Console 提示输入 user_code，选钱包 + scope，确认
6. Plugin 轮询 POST /v1/oauth/device/token，获得 (key_id, secret)
   → 存进 OpenClaw plugin 本地 secure store
7. 后续请求用 (key_id, secret) 走 §4.3 的 HMAC-SHA256 签名
```

> 这个流程**完全替代**§4.2 早期版本中"owner 复制粘贴 secret"的手工方式 —— 安全更好（secret 不经人手）+ UX 更好（plugin 启动即可登录）。

> **Owner 操作仍只在 Web Console**：钱包创建 / agent 绑定 / credential 签发授权 / first-payee 审批 / kill-switch / withdraw 全部在 Web Console。OpenClaw plugin 仅承载"agent 在用钱"这一面。

#### Buyer Skill

| Endpoint | 用途 | Scope |
|---|---|---|
| `POST /v1/payment/route` | 路由决策（ledger / x402 / chain transfer） | 通用 |
| `POST /v1/escrow/lock` | N2 Lock | `lock` |
| `POST /v1/escrow/{id}/release` | N5 Release | `release` |
| `POST /v1/escrow/{id}/reject` | N4 Reject + reason | `release` |
| `POST /v1/x402/fetch` | 即时付费 HTTP 调用 | `x402` |
| `GET /v1/escrow/{id}` | 查询 escrow 状态 | 公开（Eigenflux ID 可读） |

#### Seller Skill

| Endpoint | 用途 | Scope |
|---|---|---|
| `POST /v1/escrow/{id}/deliver-claim` | N4 交付声明 | `deliver` |
| `GET /v1/escrow/{id}` | 查询自己 inbound escrow | 公开 |

### 6.2 wallet-api（Owner Console 用）

| Endpoint | 用途 |
|---|---|
| `POST /oauth/github/callback` | GitHub OAuth |
| `GET /v1/wallets` | 列出我的钱包 |
| `POST /v1/wallets` | 创建钱包（Circle Gateway） |
| `POST /v1/agents/bind` | 绑定 Eigenflux Agent ID |
| `POST /v1/agents/{id}/credentials` | 颁发 / 旋转 credential（管理用，OpenClaw 走 device-code flow） |
| `DELETE /v1/agents/{id}/credentials/{key_id}` | 撤销 credential |
| `POST /v1/oauth/device/authorize` | OpenClaw plugin 发起 device-code flow，返回 device_code + user_code + verification_uri |
| `POST /v1/oauth/device/token` | Plugin 轮询 credential（pending → ready 后返回 key_id + secret） |
| `POST /v1/oauth/device/grant` | Owner Console 内部端点：owner 输入 user_code 后确认授权 |
| `POST /v1/agents/{id}/disable` | 单 agent kill-switch |
| `POST /v1/owner/disable` | Owner 全局 kill-switch |
| `GET /v1/first-payee-approvals` | 待审批列表 |
| `POST /v1/first-payee-approvals/{id}` | 通过 / 拒绝 |
| `POST /v1/withdrawals` | 发起提现 |
| `POST /v1/withdrawals/{id}/confirm` | 确认提现（TOTP） |
| `POST /v1/onramp/sessions` | 创建 Coinbase Onramp session，返回 widget URL |
| `GET /v1/onramp/sessions/{id}` | 查询 onramp session 状态（仅 UI 进度展示） |
| `GET /v1/events?since=...` | Audit log 查询 |
| `GET /v1/credit/{eigenflux_agent_id}` | M5 raw 信用查询（公开 API） |

### 6.3 ledger（仅内部）

由 wallet-api / skill-server 调用，不对外。Endpoint：
- `POST /internal/ledger/credit`
- `POST /internal/ledger/escrows`
- `POST /internal/ledger/escrows/{id}/release`
- `POST /internal/ledger/escrows/{id}/refund`
- `GET /internal/ledger/state/{wallet_id}`

---

## 7. 风险策略

### 7.1 钱包级 caps

| 维度 | 默认值（USDC） | 配置位置 |
|---|---|---|
| 单笔 | 100 | `wallets.single_tx_cap_usdc`，owner 可调（≤ daily_cap） |
| 日累计 | 500 | `wallets.daily_cap_usdc`，owner 可调（≤ monthly_cap） |
| 月累计 | 5000 | `wallets.monthly_cap_usdc`，owner 可调 |

> v1 mainnet 邀请制 5–20 dev，默认值压低，pilot 头一个月观察后再松。

### 7.2 First-payee approval gate

- 每次 buyer 与新 seller 第一次交易，**强制 owner 审批**（§5.4）
- 已审批的 (buyer, seller) 对在该 wallet 终生不再触发审批
- Owner 可在 Console 主动撤销已审批 pair，触发后续交易重新走审批

### 7.3 Anti-abuse 单规则

> "One rule, no algorithm"（来自 Phase 2 内部 stage `m5__internal_anti_abuse`）

```
定义：
  N_total   = 该 binding 作为 seller 在过去 30 天内进入终态（RELEASED 或 REFUNDED）的 escrow 总笔数
  N_reject  = 其中 buyer 主动 N4 reject 触发 REFUNDED 的笔数（不含 24h 超时 auto-release，那是默认信任 seller）
  reject_rate = N_reject / N_total

触发 freeze 当：
  N_total ≥ 10  AND  reject_rate > 30%

freeze 含义：
  Chief 拒绝该 binding 作为 seller 的所有新 escrow lock 请求
  已 LOCKED 的 escrow 不受影响，正常走 release / refund / timeout
  通知 owner（webhook + 邮件）
  仅 owner 可解 freeze（Console 操作 + 强制写 audit log）
```

### 7.4 Kill-switches

| 级别 | 触发者 | 影响 |
|---|---|---|
| Per-credential | Owner / 自动 | 单 credential 拒收 |
| Per-binding | Owner | 该 agent 所有 credential 拒收 |
| Per-wallet | Owner | 该钱包所有支出拒绝 |
| Per-owner（全局） | Owner | 该 owner 名下所有 wallet 拒绝支出 |
| Platform | Chief 运维 | 全平台冻结 lock / withdraw（reconciliation 失败、incident） |

---

## 8. Reconciliation

### 8.1 不变量

```
∀ wallet:
  ledger.balances[wallet].available
  + ledger.balances[wallet].locked
  ≡ Circle.delegation_balance(wallet)
```

聚合到平台级：

```
Σ ledger.balance_total ≡ Σ Circle.delegation_total
```

### 8.2 频率

- **每日 02:00 UTC** 全量批对账
- **每次** lock / release / refund / withdraw / deposit 后的事务级软对账（计数器更新，不查 Circle）
- **手动触发**：Owner Console / 运维 CLI 可随时触发

### 8.3 失败处置 SOP

```
diff = ledger_total - circle_total

IF |diff| ≤ $0.01      → ignored (rounding tolerance, log)
IF |diff| ≤ $10        → warning, page Slack #chief-recon
IF |diff| > $10        → CRITICAL, freeze 全平台 lock + withdraw
                         page on-call 立即介入
                         走 incident response runbook
```

**调查 runbook 大纲**（实现期细化）：
1. 拉最近 24h 的 events + Circle 入金 webhook 流水
2. 比对 reconciliation_runs 历史，定位 diff 出现的时点
3. 识别可疑事务（高额 / 异常时序 / 异常 binding）
4. 必要时手工 patch ledger（仅 platform admin，全审计）

---

## 9. Eigenflux 集成

### 9.1 `eigenflux-client` 包职责

- REST 调用包装（list agents / get agent state / send message to agent）
- **状态缓存**：30s TTL，命中即返；miss 则同步查询
- 错误透传 + 区分"超时" vs "真实拒绝"
- **不做**：身份验签（Eigenflux 不提供）、消息持久化（Eigenflux 自己管）

### 9.2 集成调用点

| 调用点 | 用途 | 缓存策略 |
|---|---|---|
| Agent binding 创建 | 验证 Eigenflux Agent ID 存在 | 不缓存（一次性） |
| `escrow.lock` 前 | 查 buyer agent 状态 active？ | 30s TTL |
| `escrow.release` 前 | 同上 | 30s TTL |
| Withdraw 前 | 不查 Eigenflux（owner 操作，无关 agent 状态） | n/a |
| `escrow.locked` 通知 seller | Push message via Eigenflux | n/a |
| `escrow.released` / `refunded` 通知 | Push message | n/a |

### 9.3 Eigenflux 不可用时降级

- 缓存命中：放行
- 缓存失效：
  - lock ≤ $10 → 放行（容忍小额风险）
  - lock > $10 → 拒绝，返回 `failed_retryable` + `eigenflux_unavailable`
  - release：放行（已 locked 的钱必须能释放）
  - withdraw：owner 操作，不阻塞

### 9.4 Sandbox 联调（Phase A）

- 等 Eigenflux 提供 sandbox URL + 测试 ID
- Phase A 早期若 sandbox 未就绪，Chief 自建 mock service（+1w）

---

## 10. SDK 返回 Taxonomy（5 档）

> 来自 Phase 2 内部 stage `m4_n1__internal_expired` 揭示的契约。

| 档位 | 语义 | SDK 行为 | 例子 |
|---|---|---|---|
| `success` | 成功 | 继续业务流程 | escrow 创建成功 |
| `pending_approval` | 等 owner 审批 | **MUST NOT retry**；等 webhook 或 polling | first-payee 第一次交易 |
| `failed_retryable` | 可重试 | 指数退避，最多 3 次 | Eigenflux 暂不可用、Circle 临时错误 |
| `failed_terminal` | 不可重试 | 立即向上层报错 | quote expired、cap exceeded、credential revoked |
| `unknown` | 状态未知（网络超时等） | 通过 idempotency key 查询确认状态后再决定 | 请求超时但服务器可能已处理 |

**契约**：所有 `skill-server` 响应 body 包含 `code: <one of 5>` + `reason: <enum>` + `request_id: <uuid>`。SDK 实现严格按 code 路由行为。

**OpenClaw 双格式 / 内部 REST shape 一致**：MCP server 输出、OpenClaw native plugin 输出、内部 REST JSON body 三方字段名、枚举值、retry 规则**完全一致**。OpenClaw plugin 内部不管走哪条路径都看到同一份 code/reason/request_id 三元组。

---

## 11. 威胁模型

### 11.1 攻击场景与缓解

| 编号 | 攻击场景 | v1 缓解 |
|---|---|---|
| T1 | Attacker 知道 Eigenflux Agent ID，冒充该 agent 调用 Chief | Chief credential 校验拦截（不知道 secret 就过不了 HMAC）|
| T2 | Attacker 截获请求重放 | timestamp ±5min 窗口 + nonce Redis 去重 |
| T3 | Attacker 通过侧信道窃得 secret | 短 TTL（90 天）+ 旋转 + audit log + owner kill-switch |
| T4 | Buyer 与恶意 seller 串通伪造 quote 套现 | First-payee 审批挡第一次；anti-abuse 单规则挡批量；M5 raw 留痕；mainnet 头月人工 review ≥ $10 |
| T5 | Eigenflux 被攻破，恶意 agent 注入网络 | Chief 不依赖 Eigenflux 信任；credential 绑定时 owner 必须主动 paste ID 并审核 |
| T6 | Reconciliation 漂移 / Circle 错账 | 每日批对账 + > $10 触发全平台 freeze |
| T7 | Owner 账号被盗 | GitHub OAuth + 关键操作（withdraw / kill-switch 解除 / device-code grant）TOTP；audit log 邮件通知所有 credential 颁发 / 旋转 |
| T8 | 内鬼 platform admin 盗款 | Audit log 不可改 + 多人审批的 platform-level kill-switch + reconciliation diff > $10 强制曝光 |
| T9 | Device-code flow 被钓鱼（attacker 偷 user_code 引诱 owner 确认） | user_code 短 TTL（≤ 10 分钟）+ Console 显示 "你正在为哪个 binding 授权 + scope 列表" 让 owner 主动核对 + 同一 owner 同一时段并发 device flow 数限制 |

### 11.2 风险登记（继承 brainstorm §8.11）

- **R1**（Eigenflux ID 可冒名）→ §4 鉴权设计是核心缓解
- **R2**（撮合不验签）→ §7.2 first-payee + §7.3 anti-abuse + mainnet 头月人工 review
- **R3**（Eigenflux 状态 pull-only）→ §9.3 降级策略

---

## 12. 运维 SOP

### 12.1 关键指标

| 指标 | 阈值 | 告警 |
|---|---|---|
| `skill_server.auth.reject_rate` | > 5% / 5min | warning |
| `escrow.lock.error_rate` | > 1% / 5min | warning |
| `escrow.expired_auto_release.count` | > 50 / day | info |
| `reconciliation.diff_usdc` | > $10 | **critical**（freeze 平台） |
| `eigenflux.api.p99_latency_ms` | > 2000 | warning |
| `eigenflux.api.error_rate` | > 5% / 5min | warning |
| `circle.webhook.lag_seconds` | > 600 | warning |
| `first_payee_approval.pending.count` | > 20 | info（owner 可能在度假） |

### 12.2 关键 Runbook

> 实现期细化，当前列出标题：

- `RB-01: mainnet kill-switch 触发`
- `RB-02: Reconciliation diff > $10 处置`
- `RB-03: Eigenflux API 不可用`
- `RB-04: Circle Gateway webhook 中断`
- `RB-05: 可疑高额 lock 模式（mainnet 头月人工 review 流程）`
- `RB-06: Credential 大规模旋转（密钥泄漏假设）`
- `RB-07: Owner 账号被盗后续处置`

---

## 13. 待解决项 / TBD

按 Phase A 实现期可填上的预期：

- **Eigenflux REST 接口具体路径 / 参数 schema** — 等 sandbox 接入
- ~~**Onramp 厂商最终选择**~~ → **已锁定 Coinbase Onramp**（2026-05-06）
- ~~**Datadog vs Better Stack APM 选型**~~ → **已锁定 AMP + AMG**（OpenTelemetry + ADOT sidecar），2026-05-06
- **Owner Console UI 设计稿** — 设计同步进行
- **5 档 SDK return taxonomy 中 `unknown` 的具体触发条件枚举**
- **Anti-abuse 30% / 30 天 / 10 笔的具体阈值** — pilot 头月观察数据后微调
- ~~**framework adapter 选择**~~ → **作废**：v1 唯一目标 OpenClaw，无需 LangChain / Vercel adapter（2026-05-06）
- **OpenClaw plugin manifest 格式细节**（MCP 与 OpenClaw native 两路打包参数、版本号策略、依赖声明）— Phase A W2 调研 OpenClaw 文档后定
- **GitHub repo 结构 + release pipeline**（命名、目录、CI/CD 推 release）— Phase A W3 之前定

---

## 14. 评审重点

请重点 review：

1. **§4 鉴权 & 身份模型** — v1 唯一资金授权门，HMAC 签名格式 + credential lifecycle 是否覆盖 R1 全部攻击面
2. **§5.1 Escrow 状态机** — 24h auto-release 默认信任 seller 的产品决策是否已与团队对齐
3. **§7.3 Anti-abuse 阈值** — 30% / 30 天 / 10 笔的初始值是否合理
4. **§8.3 Reconciliation 失败处置** — > $10 即冻结全平台是否过严（v1 邀请制 5–20 dev，单笔上限 $100，日上限 $500，平台总流水预估单日 < $10K）
5. **§11 威胁模型 T1–T8** — 是否漏掉关键攻击面
6. **§13 TBD 列表** — 是否有应该现在就拍板而被推到实现期的项

评审通过后转入 `superpowers:writing-plans` 出实现计划。
