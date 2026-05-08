# 04 — Key Business Flows

按 v1 真实使用顺序排：先把 plugin 装上 + 拿到凭证（Flow 1），然后才是花钱（Flow 2、3），最后是平台守门（Flow 4）。

每个 flow 一张时序图，专注一件事，重点高亮**异常 / 安全相关分支**。

---

## Flow 1：OpenClaw plugin 首次登录（OAuth device-code）

### 这张图回答什么

**用户从"装好 plugin"到"plugin 拿到凭证开始花钱"经过了哪些跳？凭证怎么不经手 owner 就到 plugin？**

```mermaid
sequenceDiagram
    autonumber
    actor User as OpenClaw 用户<br/>(同时是 Owner)
    participant Plugin as OpenClaw plugin<br/>(在用户机器)
    participant Browser as 用户浏览器
    participant Console as Owner Console
    participant WAPI as wallet-api
    participant PG as Postgres
    participant EFX as Eigenflux

    Note over User,Plugin: 前提：Owner 已在 Console 创建 wallet + 绑定 Eigenflux Agent ID

    User->>Plugin: openclaw plugin agent-wallet login
    Plugin->>WAPI: POST /v1/oauth/device/authorize
    WAPI->>PG: insert device_flow_session<br/>(device_code, user_code, expires_in_10min)
    WAPI-->>Plugin: { device_code, user_code: "ABCD-1234",<br/>verification_uri, interval: 5s }

    Plugin->>User: 终端显示<br/>"打开 https://chief.app/device<br/>输入 ABCD-1234"
    Plugin->>WAPI: 开始轮询 POST /v1/oauth/device/token<br/>(每 5s)
    WAPI-->>Plugin: { code: "pending" }

    User->>Browser: 打开 verification_uri
    Browser->>Console: GET /device
    Console->>WAPI: 当前 session（GitHub OAuth + cookie）
    WAPI-->>Console: owner 已登录
    User->>Console: 输入 user_code "ABCD-1234"
    Console->>WAPI: POST /v1/oauth/device/grant<br/>{ user_code, binding_id, scopes }

    Note over WAPI,PG: 关键校验：<br/>- user_code 未过期<br/>- 同一 owner 当前并发 device flow ≤ 3<br/>- binding 属于该 owner

    WAPI->>PG: 生成 (key_id, secret)<br/>argon2id hash 入库<br/>更新 device_flow_session
    WAPI-->>Console: 授权成功

    Plugin->>WAPI: 下次轮询<br/>POST /v1/oauth/device/token
    WAPI-->>Plugin: { code: "success", key_id, secret }
    Plugin->>Plugin: 写入 OS keychain<br/>(secret 永不再上链路)

    Note over Plugin: 后续所有请求<br/>用 (key_id, secret) 走 §4.3 HMAC 签名
```

### 关键安全点

- **secret 在响应里只出现一次**，写入 OS keychain 后从内存清掉；DB 仅有 argon2id hash
- **user_code 短 TTL（≤ 10min）+ 并发限制（≤ 3）**——T9 钓鱼攻击的双重防御
- **Console 在 grant 页必须显示**："你正在授权 binding `<eigenflux_agent_id>` (display name: ...) 在 wallet `<id>` 下使用 scope `<lock,release,...>`"，让 owner 主动核对，不能仅靠 user_code 匹配

### 失败分支
- 用户超时未在浏览器输入 user_code → device_code 过期，plugin 收到 `failed_terminal` + `reason=device_code_expired`
- Owner 在 Console 拒绝 → 同 `failed_terminal` + `reason=owner_denied`
- Plugin 短时间内多次发起 device flow → `failed_retryable` + `reason=too_many_concurrent_flows`

---

## Flow 2：A2A Escrow Happy Path（N1 → N5）

### 这张图回答什么

**一笔完整的 A2A 支付里，钱什么时候动、谁触发、ledger 写了几次？**

合并 N1–N5，但聚焦"钱"的视角，不画 Eigenflux 网络上的消息细节。

```mermaid
sequenceDiagram
    autonumber
    participant BAgent as Buyer Agent<br/>(在 OpenClaw)
    participant SAgent as Seller Agent<br/>(在 OpenClaw)
    participant EFX as Eigenflux
    participant BPlugin as Buyer Plugin
    participant SPlugin as Seller Plugin
    participant SS as skill-server
    participant LDG as ledger
    participant PG as Postgres

    Note over BAgent,SAgent: N1 Quote — 走 Eigenflux，不动钱
    BAgent->>EFX: 发起服务请求 + 报价
    EFX->>SAgent: 撮合 + 转报价
    SAgent->>EFX: 接受
    EFX-->>BAgent: 撮合成功 + quote_id

    Note over BAgent,SS: N2 Lock — Chief 锁钱
    BAgent->>BPlugin: 触发支付
    BPlugin->>SS: POST /v1/escrow/lock<br/>(HMAC, amount, seller_binding, quote_id)
    SS->>LDG: 创建 escrow
    LDG->>PG: BEGIN<br/>insert escrows (LOCKED)<br/>update balances (available--, locked++)<br/>insert events (escrow.locked)<br/>COMMIT
    LDG-->>SS: { id, expires_at: now+24h }
    SS-->>BPlugin: { code: "success", escrow_id }
    BPlugin->>EFX: 通过 Eigenflux 通知 seller "已锁"

    Note over SAgent,SPlugin: N3 Verify — seller 直接查 Chief
    SAgent->>SPlugin: 查 escrow 状态
    SPlugin->>SS: GET /v1/escrow/{id}
    SS-->>SPlugin: { state: LOCKED, amount, ... }
    SAgent->>SAgent: 验证后开始干活

    Note over SAgent,SPlugin: N4 Deliver — 走 Eigenflux + 可选 Chief 落地
    SAgent->>EFX: 交付声明
    SPlugin->>SS: POST /v1/escrow/{id}/deliver-claim
    SS->>LDG: 写 events (escrow.delivered)
    EFX->>BAgent: 收到交付声明

    Note over BAgent,SS: N5 Release — Chief 放钱
    BAgent->>BPlugin: 验收通过
    BPlugin->>SS: POST /v1/escrow/{id}/release
    SS->>LDG: 释放
    LDG->>PG: BEGIN<br/>update escrows (RELEASED)<br/>update balances buyer (locked--)<br/>update balances seller (available++)<br/>insert events (escrow.released)<br/>COMMIT
    LDG-->>SS: ok
    SS-->>BPlugin: { code: "success" }
```

### 关键观察

- **钱真的动只有两次**：N2 LOCKED 和 N5 RELEASED 在 ledger 各 1 个事务
- **N3 Verify 不走消息**：seller 主动查我们的公开状态接口，不需要 Eigenflux 转发
- **每一次 ledger 写都同时插 event**：M5 raw + audit log 的输入源都来自这里

### 异常分支（同图省略）

- N5 不来 → 24h 后 `escrow.expires_at` 触发 timer，状态 `LOCKED → EXPIRED → RELEASED`（默认信任 seller）
- Buyer N4 主动 reject → `LOCKED → REFUNDED`，buyer 余额回到 available；写 `event=escrow.rejected` 含 reason，进 M5 raw 的 reject_rate 分子

---

## Flow 3：First-payee 审批 Gate

### 这张图回答什么

**buyer 第一次给某个 seller 转钱时，怎么把 owner 拉进来确认？plugin 怎么知道"在等"和"被批了"？**

```mermaid
sequenceDiagram
    autonumber
    participant BAgent as Buyer Agent
    participant BPlugin as Buyer Plugin
    participant SS as skill-server
    participant WAPI as wallet-api
    participant PG as Postgres
    participant Console as Owner Console
    actor Owner

    BAgent->>BPlugin: 想给 seller_binding=X 锁 $50
    BPlugin->>SS: POST /v1/escrow/lock
    SS->>PG: 查 first_payee_approvals<br/>(buyer, seller=X)
    Note over SS,PG: 该 (buyer, seller) pair<br/>未审批过

    SS->>WAPI: POST /internal/first-payee/request<br/>{ buyer, seller, amount }
    WAPI->>PG: insert first_payee_approvals<br/>(state=pending, expires_in_7d)
    WAPI-->>Owner: 邮件 + Console 通知

    SS-->>BPlugin: { code: "pending_approval",<br/>request_id, reason: "first_payee" }
    BPlugin->>BAgent: 显示 "首次给 X 付款，<br/>等 owner 在 Console 批准"

    Note over BPlugin: SDK 强约束：<br/>不能 retry！只能轮询查询接口

    Owner->>Console: 看到待审批
    Console->>WAPI: POST /v1/first-payee-approvals/{id}<br/>{ decision: approved }
    WAPI->>PG: update state=approved
    WAPI->>WAPI: audit log

    BPlugin->>SS: 轮询 GET /v1/escrow/lock/status?request_id
    SS->>PG: 查 approval 已 approved<br/>+ 自动重新尝试 lock
    SS-->>BPlugin: { code: "success", escrow_id }

    Note over Owner: 该 (buyer, seller) pair<br/>未来不再触发审批
```

### 关键点

- **`pending_approval` 不是错误**：plugin 必须把它当成"等待中"而不是"失败" —— 这是 5-tier taxonomy 的灵魂
- **Owner 拒绝路径**：`state=rejected` → plugin 轮询拿到 `failed_terminal` + `reason=first_payee_rejected`
- **7 天未决定 → expired**：plugin 拿到 `failed_terminal` + `reason=approval_expired`
- **批准后该 pair 终生免审批**：除非 owner 主动撤销

---

## Flow 4：Reconciliation Diff > $10 → 平台 Freeze

### 这张图回答什么

**当 ledger 账面和 Circle 真实托管对不上时，系统怎么自动停下来防止扩散？**

```mermaid
sequenceDiagram
    autonumber
    participant EB as EventBridge<br/>每日 02:00 UTC
    participant Recon as reconciler
    participant PG as Postgres
    participant CIR as Circle Gateway
    participant SNS
    participant Slack
    participant SS as skill-server
    participant WAPI as wallet-api
    actor OnCall as On-call

    EB->>Recon: 触发 reconciliation_run
    Recon->>PG: 读 sum(balances.available + balances.locked)
    Recon->>CIR: GET delegation totals (per wallet)

    Note over Recon: 计算 diff = ledger_total - circle_total

    alt diff ≤ $0.01
        Recon->>PG: 写 reconciliation_runs (status=ok)
    else $0.01 < diff ≤ $10
        Recon->>PG: 写 reconciliation_runs (status=warning, diff)
        Recon->>SNS: warning 告警
        SNS->>Slack: post #chief-recon
    else diff > $10
        Recon->>PG: 写 reconciliation_runs (status=critical, diff)
        Recon->>PG: <b>UPDATE platform_state<br/>SET frozen=true</b>
        Recon->>SNS: page critical
        SNS->>Slack: post #chief-incidents
        SNS->>OnCall: PagerDuty / SMS
    end

    Note over SS,WAPI: 后续所有请求

    SS->>PG: 鉴权前检查 platform_state
    Note over SS: frozen=true
    SS-->>SS: 拒绝所有 lock + withdraw<br/>code: failed_terminal<br/>reason: platform_frozen

    WAPI->>PG: 同样检查
    WAPI-->>WAPI: owner 操作里 withdraw 拒绝

    OnCall->>WAPI: 调查 + 走 RB-02 runbook<br/>定位 diff 来源
    OnCall->>WAPI: 修复后<br/>POST /platform/unfreeze<br/>(多人审批)
    WAPI->>PG: UPDATE platform_state<br/>SET frozen=false
```

### 关键点

- **freeze 是平台级，不是单 wallet 级**：因为 diff 不知道源头是哪个 wallet 之前
- **release 不冻结**：已经 LOCKED 的 escrow 必须能正常 release，否则 buyer 钱被卡
- **解冻必须多人审批**：`POST /platform/unfreeze` 是 admin 端点，不能单人操作（T8 内鬼防御）
- **`reconciliation_runs` 表是历史**：每次跑结果都留痕，便于回放调查

详见 [ADR-008](06-decisions/adr-008-reconciliation-freeze-threshold.md)。
