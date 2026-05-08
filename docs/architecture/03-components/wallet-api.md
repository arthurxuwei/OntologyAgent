# 03 — Component / `wallet-api`

## 这张图回答什么

**`wallet-api` 内部如何把"wallet init / claim / owner 治理 / agent 鉴权 / 外部资金接入"这五件事的边界划清楚？**

`wallet-api` 同时服务四种调用方（OpenClaw plugin 公开端点、Owner Console、skill-server 内部、外部 webhook），是 Chief 域内最"杂"的 service。这张图给它内部模块化。

## 图

```mermaid
graph TB
    subgraph callers["调用方"]
        Console[Owner Console]
        SkillSrv[skill-server<br/>(内部)]
        CIR_WH[Circle webhook]
        CB_WH[Coinbase webhook<br/>(仅 UI 进度展示)]
        Plugin[OpenClaw plugin<br/>(wallet init + device-code)]
    end

    subgraph wallet_api["wallet-api (ECS Fargate)"]
        OwnerHTTP["<b>Owner HTTP API</b><br/>OAuth + TOTP + session"]
        InternalHTTP["<b>Internal HTTP API</b><br/>(skill-server 调，<br/>VPC 内 mTLS / IAM)"]
        WebhookHTTP["<b>Webhook HTTP</b><br/>外部回调入口"]
        DeviceCodeAPI["<b>Device-code API</b><br/>authorize / token / grant"]
        PublicInitAPI["<b>Wallet Init API</b><br/>(公开，无 Chief credential)<br/>POST /v1/wallets/init"]

        OAuthGH["<b>GitHub OAuth Adapter</b>"]
        TOTPSvc["<b>TOTP Service</b>"]
        SessionMgr["<b>Session Manager</b>"]

        InitSvc["<b>Wallet Init Service</b><br/>Flow 1: Eigenflux 验证 +<br/>Circle createWallet +<br/>claim_code 生成"]
        ClaimSvc["<b>Claim Service</b><br/>Flow 2: argon2id verify +<br/>owner_id 挂上 + 邮件通知"]
        WalletMgr["<b>Wallet Manager</b><br/>列出 / disable /<br/>caps 配置 / reverse-claim"]
        CredMgr["<b>Credential Manager</b><br/>颁发 / rotate / revoke<br/>+ argon2id hash"]
        ApprovalSvc["<b>First-payee Approval Service</b><br/>pending → approved/rejected"]
        WithdrawSvc["<b>Withdraw Service</b><br/>request / TOTP confirm /<br/>broadcast"]
        OnrampSvc["<b>Onramp Service</b><br/>Coinbase session 发起 /<br/>状态查询"]
        DepositMgr["<b>Deposit Pipeline</b><br/>(消费 Circle webhook)"]

        EFXAdapter["<b>Eigenflux Adapter</b><br/>(packages/eigenflux-client)"]
        CIRAdapter["<b>Circle Adapter</b><br/>(packages/circle-custody)"]
        CBAdapter["<b>Coinbase Onramp Adapter</b>"]

        AuditLog["<b>Audit Logger</b><br/>(写 events 表)"]
    end

    subgraph data["数据"]
        PG[(PostgreSQL)]
        Ledger[ledger]
        SM[Secrets Manager]
    end

    Console --> OwnerHTTP
    SkillSrv --> InternalHTTP
    CIR_WH --> WebhookHTTP
    CB_WH --> WebhookHTTP
    Plugin --> DeviceCodeAPI
    Plugin --> PublicInitAPI

    OwnerHTTP --> OAuthGH
    OwnerHTTP --> TOTPSvc
    OwnerHTTP --> SessionMgr
    OwnerHTTP --> WalletMgr
    OwnerHTTP --> ClaimSvc
    OwnerHTTP --> CredMgr
    OwnerHTTP --> ApprovalSvc
    OwnerHTTP --> WithdrawSvc
    OwnerHTTP --> OnrampSvc

    PublicInitAPI --> InitSvc
    InitSvc --> EFXAdapter
    InitSvc --> CIRAdapter
    InitSvc --> AuditLog
    ClaimSvc --> AuditLog

    InternalHTTP --> ApprovalSvc
    InternalHTTP --> CredMgr

    WebhookHTTP --> DepositMgr
    DeviceCodeAPI --> CredMgr
    DeviceCodeAPI --> SessionMgr

    WalletMgr --> CIRAdapter
    OnrampSvc --> CBAdapter
    WithdrawSvc -->|"USDC 转账"| Ledger
    DepositMgr -->|"credit"| Ledger

    WalletMgr --> AuditLog
    CredMgr --> AuditLog
    ApprovalSvc --> AuditLog
    WithdrawSvc --> AuditLog
    DepositMgr --> AuditLog
    DeviceCodeAPI --> AuditLog

    AuditLog --> PG
    InitSvc --> PG
    ClaimSvc --> PG
    WalletMgr --> PG
    CredMgr --> PG
    ApprovalSvc --> PG

    CredMgr -.读.-> SM
    CIRAdapter -.读.-> SM
    OAuthGH -.读.-> SM

    classDef inbound fill:#FBF8F2,stroke:#A8590D;
    classDef domain fill:#fff,stroke:#1A1A1A;
    classDef adapter fill:#e8e2d4,stroke:#1A1A1A;
    classDef cross fill:#fff5e8,stroke:#8B6914;
    class OwnerHTTP,InternalHTTP,WebhookHTTP,DeviceCodeAPI,PublicInitAPI inbound;
    class OAuthGH,TOTPSvc,SessionMgr,InitSvc,ClaimSvc,WalletMgr,CredMgr,ApprovalSvc,WithdrawSvc,OnrampSvc,DepositMgr domain;
    class EFXAdapter,CIRAdapter,CBAdapter adapter;
    class AuditLog cross;
```

## 关键说明

### 五个入站平面，鉴权策略各不相同

| 平面 | 调用方 | 鉴权 |
|---|---|---|
| `Owner HTTP API` | 浏览器 / Owner Console | GitHub OAuth session cookie + 关键操作 TOTP |
| `Internal HTTP API` | `skill-server`（VPC 内） | IAM role + mTLS / 短期签名 token，不允许公网访问 |
| `Webhook HTTP` | Circle / Coinbase | 验厂商签名（Circle webhook signature / Coinbase HMAC） |
| `Device-code API` | OpenClaw plugin（公网） | `authorize` 公开；`token` 凭 device_code 轮询；`grant` 仅 owner session |
| `Wallet Init API` | OpenClaw plugin（公网） | **完全公开**，仅入参 Eigenflux Agent ID；防滥用靠 `wallets.eigenflux_agent_id` UNIQUE + per-IP rate limit + Eigenflux 状态校验。详见 design.md §11 T10 |

### Audit Logger 是横切关注点

任何**改变状态**的操作都必须经 Audit Logger 写入 `events` 表。这是 §11 威胁模型 T7 / T8 的硬要求 —— 即便内鬼 admin，也得在审计流水里留下不可改的痕迹。

### 几个关键的"不要"

- **Owner HTTP API 不能直接读 secret 明文**：secret 仅在颁发瞬间从 CredMgr 输出一次，立即写入响应；DB 仅存 argon2id hash
- **claim_code 同上**：仅 InitSvc 输出一次进 Flow 1 响应；DB 仅 argon2id hash
- **InitSvc 不能跳过 Eigenflux 校验**：必须先验 Eigenflux Agent ID 真实存在 + state=active 才能创建 wallet
- **InitSvc 不能跳过 Circle Adapter 直接写 PG**：`circle_wallet_id` 必须先在 Circle 那边创建成功才能落库
- **ClaimSvc 不能在 wallet `state != 'unclaimed'` 时改 owner_id**：保护已 claim 的钱包不被覆盖
- **First-payee Approval Service 不能由 skill-server 直接 grant**：skill-server 只能查询 / 触发 pending，approve / reject 必须 owner 走 Console

### 与 ledger 的边界

`wallet-api` 不直接维护 escrow 状态机。所有 ledger-touching 操作（withdraw 落 credit_lock / debit、deposit credit）都由 `wallet-api` 调用 ledger 内部 API 完成 —— ledger 是**唯一的金钱状态机**。

## 不在 `wallet-api` 里

- Escrow 创建 / 释放 / 退款的状态机（→ `ledger`）
- 24h auto-release timer 的实际触发（→ `ledger` 内部 + EventBridge）
- 对账（→ `reconciler`）
