# 04 — Key Business Flows

按时间顺序：用户登录（Flow 0）↔ agent 初始化创建钱包（Flow 1，可在登录前发生）→ 用户 claim 钱包（Flow 2，登录后）→ plugin 取凭证（Flow 3）→ 花钱（Flow 4、5）→ 平台守门（Flow 6）。

每个 flow 一张时序图，专注一件事，重点高亮**异常 / 安全相关分支**。

> **归属模型**：v1 采用"**wallet first, owner later**"（来自原型 M2 叙事）—— wallet 由 agent 初始化时创建（无 owner），由用户登录后凭 claim code 认领。这与"owner-first"的 mainnet 安全直觉**有冲突**，相关风险在 Flow 1 / Flow 2 的"安全考虑"段落显式列出。

---

## Flow 0：用户 OAuth 登录

### 这张图回答什么

**用户从打开 Console 到拿到一个可信的 session 中间发生了什么？这个流程不动钱，纯认证。**

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户<br/>(浏览器侧)
    participant Browser
    participant Console as Owner Console<br/>(SPA, S3+CloudFront)
    participant WAPI as wallet-api
    participant GH as GitHub OAuth
    participant PG as Postgres

    User->>Browser: 打开 console.chief.app
    Browser->>Console: GET /
    Console-->>Browser: 引导登录

    User->>Browser: 点 "Sign in with GitHub"
    Browser->>WAPI: GET /oauth/github/redirect<br/>(产生 state + PKCE)
    WAPI->>PG: 临时存 oauth_state<br/>(防 CSRF)
    WAPI-->>Browser: 302 → GitHub authorize URL

    Browser->>GH: 授权页
    User->>GH: 同意
    GH-->>Browser: 302 → wallet-api callback?code=...&state=...

    Browser->>WAPI: GET /oauth/github/callback?code+state
    WAPI->>PG: 校验 oauth_state（一次性消耗）

    alt state mismatch / 已过期
        WAPI-->>Browser: 401 + 重定向回登录页
    else 校验通过
        WAPI->>GH: POST /token (换 access_token)
        GH-->>WAPI: { access_token, ... }
        WAPI->>GH: GET /user (拉 user info)
        GH-->>WAPI: { github_id, email, name }

        alt 首次登录
            WAPI->>PG: INSERT owners (github_id, email)
        else 已有 owner
            WAPI->>PG: SELECT 现有 owner
        end

        WAPI->>PG: INSERT events (owner.login)
        WAPI-->>Browser: 设置 session cookie<br/>(HttpOnly, Secure, SameSite=Lax)<br/>302 → /dashboard
    end
```

### 关键点

- **state 参数 + PKCE** 强制启用，防 CSRF + 中间人换 code
- **oauth_state 一次性消耗**：DB 行配 unique index，回调时 DELETE RETURNING
- **session cookie**：HttpOnly + Secure + SameSite=Lax；TTL 14 天，Owner 操作里所有"金钱关键路径"额外要 TOTP（withdraw / kill-switch / device-code grant）
- **本流不创建 wallet 也不绑 agent**：这两件事走 Flow 1 / Flow 2

### 失败 / 异常分支

- GitHub 拒绝授权 → 302 回登录页
- state mismatch → 401，记 `event=auth.state_mismatch` 并告警（潜在 CSRF）
- GitHub /user API 超时 → `failed_retryable`，引导用户重试
- 同一 GitHub 账号短时间多次失败登录 → 临时 lockout 5 分钟

---

## Flow 1：Agent 初始化创建钱包（wallet first）

### 这张图回答什么

**OpenClaw plugin 第一次启动时，它如何在 Chief 这边为自己的 Eigenflux Agent ID 准备一个"无主"钱包？claim code 怎么生成？怎么交付给真正的用户？**

```mermaid
sequenceDiagram
    autonumber
    actor User as OpenClaw 用户
    participant OC as OpenClaw 运行时
    participant Plugin as Chief plugin<br/>(在用户机器)
    participant WAPI as wallet-api
    participant EFX as Eigenflux REST
    participant CIR as Circle Gateway
    participant PG as Postgres

    User->>OC: openclaw plugin install <chief-repo>
    OC->>Plugin: 加载 manifest + 初始化
    Plugin->>OC: 读取 OpenClaw 当前关联的 Eigenflux Agent ID
    OC-->>Plugin: { eigenflux_agent_id }

    Plugin->>WAPI: POST /v1/wallets/init<br/>{ eigenflux_agent_id }<br/>(无需 Chief credential)

    WAPI->>EFX: GET /agents/{eigenflux_agent_id}<br/>(eigenflux-client，带 30s 缓存)

    alt Agent ID 不存在 / 状态非 active
        EFX-->>WAPI: 404 / state=revoked
        WAPI-->>Plugin: { code: failed_terminal,<br/>reason: invalid_agent_id }
        Plugin-->>User: 报错，停止初始化
    else Agent ID active
        EFX-->>WAPI: { state: active, ... }

        WAPI->>PG: SELECT wallets WHERE eigenflux_agent_id=$1
        alt 已存在 wallet（任何 owner_id 状态）
            WAPI-->>Plugin: { code: failed_terminal,<br/>reason: wallet_already_exists }
            Plugin-->>User: 提示 "该 Agent ID 已有钱包，<br/>若是你的请走 Flow 2 claim"
        else 全新
            WAPI->>CIR: createWallet (Circle Web3 Services)
            CIR-->>WAPI: { circle_wallet_id, address }

            WAPI->>WAPI: 生成 claim_code<br/>(32 bytes random base32, TTL 24h)
            WAPI->>PG: BEGIN<br/>INSERT wallets (owner_id=NULL,<br/>eigenflux_agent_id, circle_wallet_id,<br/>address, state='unclaimed',<br/>默认 caps: $100/$500/$5000)<br/>INSERT wallet_claim_codes<br/>(wallet_id, code_hash=argon2id(claim_code),<br/>expires_at=now()+24h, used=false)<br/>INSERT events (wallet.created.unclaimed)<br/>COMMIT

            WAPI-->>Plugin: { code: success,<br/>wallet_id, address, claim_code,<br/>verification_uri: "console.chief.app/claim",<br/>expires_in: 86400 }

            Plugin-->>User: 终端展示<br/>"钱包已创建：<address><br/>30 秒内打开 verification_uri<br/>登录并输入 claim code: ABCD-EFGH-IJKL"
        end
    end

    Note over User,WAPI: 此时 wallet.owner_id=NULL，<br/>state='unclaimed'，<br/>不能 lock / withdraw（被 §7.1 caps 拒绝）
```

### 关键点

- **wallet 出生即归属 Eigenflux Agent ID（不是 owner）**：`wallets.eigenflux_agent_id` UNIQUE 约束保证一个 Eigenflux ID 只能有一个钱包
- **owner_id 可空 + state='unclaimed'**：未 claim 的钱包不能 lock / withdraw（skill-server / wallet-api 在所有花钱路径检查 `state='claimed'`）
- **claim_code 只在响应里出现一次**：DB 仅存 argon2id hash；明文随响应返回，立即在 plugin 端展示给用户后从内存清掉
- **claim_code TTL 24h + 一次性**：超时或被 claim 后失效；plugin 可重发 init 但若已存在 wallet 会被拒
- **本接口无 Chief credential auth**：因为 plugin 在初始化时还没有 credential —— 只能靠 Eigenflux ID 公开识别 + 一次性写约束兜底

### 安全考虑（v1 已知风险）

> **R10（新增）：Eigenflux Agent ID 公开 + 无认证 init = claim_code 抢跑攻击**
>
> 攻击者若知道某个 Eigenflux Agent ID 且**先于合法用户**调 `POST /v1/wallets/init`，能拿到该 ID 对应的 claim_code，再在 Flow 2 用自己的 OAuth session claim 走该钱包。
>
> v1 缓解：
> - Eigenflux Agent ID 在 Eigenflux 网络内是相对受控信息（不公开 list）
> - `wallets.eigenflux_agent_id` UNIQUE → 合法用户晚一步会被告知 "wallet_already_exists"，立即可发现异常
> - claim_code 24h TTL + 一次性 → 攻击窗口有限
> - mainnet 头一个月 Owner Console 在 Flow 2 claim 成功后强制邮件通知 + 24h 内 owner 可"反 claim"（需要 GitHub OAuth + TOTP 双因子）
>
> 长期解法（v1.1+）：
> - Eigenflux 加密码学认证后，wallet init 要求 Eigenflux 签名 envelope
> - 或：init 时用 Eigenflux 推送 webhook 替代 plugin 主动调，把信任锚交给 Eigenflux 网络
>
> 已写入 design.md §11 威胁模型 T10（待补）。

### 失败 / 异常分支

- Eigenflux 不可用 → `failed_retryable`，plugin 退避重试 3 次后停
- Circle createWallet 失败 → `failed_retryable`；entity secret 错误转 `failed_terminal` + page on-call
- 同 Agent ID 已有钱包 → `failed_terminal` + `reason=wallet_already_exists`，引导走 Flow 2

---

## Flow 2：用户登录后 claim 钱包

### 这张图回答什么

**用户走完 Flow 0（拿到 session）+ Flow 1（在 plugin 终端拿到 claim_code）后，怎么把"无主钱包"挂到自己 owner 名下？**

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户<br/>(已登录 session)
    participant Browser
    participant Console as Owner Console
    participant WAPI as wallet-api
    participant PG as Postgres

    Note over User,Browser: 前提：Flow 0 已完成（session cookie 在）<br/>Flow 1 已完成（plugin 终端有 claim_code）

    User->>Browser: 打开 console.chief.app/claim
    Browser->>Console: GET /claim
    Console-->>User: 显示 claim 表单

    User->>Console: 粘贴 claim_code "ABCD-EFGH-IJKL"
    Console->>WAPI: POST /v1/wallets/claim<br/>{ claim_code }<br/>(session cookie)

    WAPI->>PG: SELECT wallet_claim_codes wcc<br/>JOIN wallets w ON wcc.wallet_id=w.id<br/>WHERE argon2id_verify(wcc.code_hash, $1)<br/>AND wcc.used=false<br/>AND wcc.expires_at > now()<br/>FOR UPDATE

    alt claim_code 无效 / 已用 / 过期
        WAPI-->>Console: { code: failed_terminal,<br/>reason: invalid_claim_code }
        Console-->>User: 错误提示
    else wallet 已经 claimed
        Note over WAPI,PG: 不应到这里（claim_code 一次性），<br/>但兜底：拒绝
        WAPI-->>Console: { code: failed_terminal,<br/>reason: wallet_already_claimed }
    else 全部通过
        WAPI->>PG: BEGIN<br/>UPDATE wallets SET<br/>  owner_id = $session_owner_id,<br/>  state = 'claimed',<br/>  claimed_at = now()<br/>WHERE id=$wallet_id<br/>UPDATE wallet_claim_codes SET<br/>  used = true, used_at = now()<br/>WHERE id=$wcc_id<br/>INSERT events (wallet.claimed,<br/>  binding_id, owner_id)<br/>COMMIT

        WAPI->>WAPI: 邮件通知 owner<br/>"你刚 claim 了 wallet <address>"<br/>(异常时反向 dispute 入口)
        WAPI-->>Console: { code: success,<br/>wallet_id, address, eigenflux_agent_id }
        Console-->>User: 跳到 dashboard，<br/>显示新归属的钱包
    end

    Note over User: 此时 wallet 可以 lock / receive deposit / onramp。<br/>但 plugin 还没 credential —— 走 Flow 3 取。
```

### 关键点

- **`FOR UPDATE` 防并发抢 claim**：两次同时尝试同一 claim_code 时第二次会序列化等待第一次结束，然后看到 `used=true` 而拒绝
- **session 必须存在**：未登录直接 `401`
- **claim 一次性**：成功后 `used=true`，再用同 code 不可
- **claim 后 wallet 可用**：`state='claimed'` 是 lock / withdraw 的前置条件
- **邮件通知 + 24h 反 claim 窗口**（mainnet 头月）：合法用户若发现钱包被他人 claim 走，可在 24h 内通过 GitHub OAuth + TOTP 强制反转所有权（这是 R10 攻击的兜底）

### 失败 / 异常分支

- claim_code 无效 / 已过期 → `failed_terminal`
- claim_code 已被使用 → `failed_terminal`，提示用户该钱包已归属他人，触发 dispute 流程
- 数据库行锁等待超时 → `failed_retryable`

---

## Flow 3：OpenClaw plugin 拿凭证（OAuth device-code）

### 这张图回答什么

**钱包已 claim 后，plugin 怎么拿到 HMAC credential 开始花钱？凭证怎么不经手 owner 就到 plugin？**

```mermaid
sequenceDiagram
    autonumber
    actor User as OpenClaw 用户<br/>(同时是 Owner)
    participant Plugin as Chief plugin
    participant Browser as 用户浏览器
    participant Console as Owner Console
    participant WAPI as wallet-api
    participant PG as Postgres

    Note over User,Plugin: 前提：Flow 0 + Flow 1 + Flow 2 已完成<br/>（owner 已登录 + wallet 已 claim）

    User->>Plugin: openclaw plugin agent-wallet login
    Plugin->>WAPI: POST /v1/oauth/device/authorize<br/>{ eigenflux_agent_id }
    WAPI->>PG: 查 binding（owner_id, wallet_id 已就位）
    WAPI->>PG: insert device_flow_session<br/>(device_code, user_code, expires_in_10min)
    WAPI-->>Plugin: { device_code, user_code: "ABCD-1234",<br/>verification_uri, interval: 5s }

    Plugin->>User: 终端显示<br/>"打开 https://console.chief.app/device<br/>输入 ABCD-1234"
    Plugin->>WAPI: 开始轮询 POST /v1/oauth/device/token<br/>(每 5s)
    WAPI-->>Plugin: { code: pending }

    User->>Browser: 打开 verification_uri
    Browser->>Console: GET /device
    Console->>WAPI: 当前 session（已通过 Flow 0）
    User->>Console: 输入 user_code
    Console->>WAPI: POST /v1/oauth/device/grant<br/>{ user_code, scopes: [lock, release, ...] }

    Note over WAPI,PG: 关键校验：<br/>- user_code 未过期<br/>- 同一 owner 当前并发 device flow ≤ 3<br/>- binding 属于该 owner（Flow 2 已挂上 owner_id）

    WAPI->>PG: 生成 (key_id, secret)<br/>argon2id hash 入库<br/>更新 device_flow_session
    WAPI-->>Console: 授权成功

    Plugin->>WAPI: 下次轮询<br/>POST /v1/oauth/device/token
    WAPI-->>Plugin: { code: success, key_id, secret }
    Plugin->>Plugin: 写入 OS keychain<br/>(secret 永不再上链路)

    Note over Plugin: 后续所有请求<br/>用 (key_id, secret) 走 §4.3 HMAC 签名
```

### 关键安全点

- **secret 在响应里只出现一次**，写入 OS keychain 后从内存清掉；DB 仅有 argon2id hash
- **user_code 短 TTL（≤ 10min）+ 并发限制（≤ 3）**——T9 钓鱼攻击的双重防御
- **Console 在 grant 页必须显示**："你正在授权 binding `<eigenflux_agent_id>` (display name: ...) 在 wallet `<id>` 下使用 scope `<lock,release,...>`"，让 owner 主动核对，不能仅靠 user_code 匹配
- **未 claim 的 wallet 不能 grant**：device flow 拒绝 `wallet.state != 'claimed'`

### 失败分支
- 用户超时未在浏览器输入 user_code → device_code 过期，plugin 收到 `failed_terminal` + `reason=device_code_expired`
- Owner 在 Console 拒绝 → 同 `failed_terminal` + `reason=owner_denied`
- Plugin 短时间内多次发起 device flow → `failed_retryable` + `reason=too_many_concurrent_flows`

---

## Flow 4：A2A Escrow Happy Path（N1 → N5）

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

## Flow 5：First-payee 审批 Gate

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

## Flow 6：Reconciliation Diff > $10 → 平台 Freeze

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
