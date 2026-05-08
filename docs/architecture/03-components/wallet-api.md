# 03 — Component / `wallet-api`

## 这张图回答什么

**`wallet-api` 内部如何把"owner 治理 + agent 鉴权 + 外部资金接入"这三件事的边界划清楚？**

`wallet-api` 同时服务三种调用方（Owner Console、skill-server 内部、外部 webhook），是 Chief 域内最"杂"的 service。这张图给它内部模块化。

## 图

```mermaid
graph TB
    subgraph callers["调用方"]
        Console[Owner Console]
        SkillSrv[skill-server<br/>(内部)]
        CIR_WH[Circle webhook]
        CB_WH[Coinbase webhook<br/>(仅 UI 进度展示)]
        Plugin[OpenClaw plugin<br/>(仅走 device-code)]
    end

    subgraph wallet_api["wallet-api (ECS Fargate)"]
        OwnerHTTP["<b>Owner HTTP API</b><br/>OAuth + TOTP + session"]
        InternalHTTP["<b>Internal HTTP API</b><br/>(skill-server 调，<br/>VPC 内 mTLS / IAM)"]
        WebhookHTTP["<b>Webhook HTTP</b><br/>外部回调入口"]
        DeviceCodeAPI["<b>Device-code API</b><br/>authorize / token / grant"]

        OAuthGH["<b>GitHub OAuth Adapter</b>"]
        TOTPSvc["<b>TOTP Service</b>"]
        SessionMgr["<b>Session Manager</b>"]

        WalletMgr["<b>Wallet Manager</b><br/>create / disable /<br/>caps 配置"]
        BindingMgr["<b>Agent Binding Manager</b><br/>绑定 / 解绑 /<br/>Eigenflux 验证"]
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

    OwnerHTTP --> OAuthGH
    OwnerHTTP --> TOTPSvc
    OwnerHTTP --> SessionMgr
    OwnerHTTP --> WalletMgr
    OwnerHTTP --> BindingMgr
    OwnerHTTP --> CredMgr
    OwnerHTTP --> ApprovalSvc
    OwnerHTTP --> WithdrawSvc
    OwnerHTTP --> OnrampSvc

    InternalHTTP --> ApprovalSvc
    InternalHTTP --> CredMgr

    WebhookHTTP --> DepositMgr
    DeviceCodeAPI --> CredMgr
    DeviceCodeAPI --> SessionMgr

    WalletMgr --> CIRAdapter
    BindingMgr --> EFXAdapter
    OnrampSvc --> CBAdapter
    WithdrawSvc -->|"USDC 转账"| Ledger
    DepositMgr -->|"credit"| Ledger

    WalletMgr --> AuditLog
    BindingMgr --> AuditLog
    CredMgr --> AuditLog
    ApprovalSvc --> AuditLog
    WithdrawSvc --> AuditLog
    DepositMgr --> AuditLog
    DeviceCodeAPI --> AuditLog

    AuditLog --> PG
    WalletMgr --> PG
    BindingMgr --> PG
    CredMgr --> PG
    ApprovalSvc --> PG

    CredMgr -.读.-> SM
    CIRAdapter -.读.-> SM
    OAuthGH -.读.-> SM

    classDef inbound fill:#FBF8F2,stroke:#A8590D;
    classDef domain fill:#fff,stroke:#1A1A1A;
    classDef adapter fill:#e8e2d4,stroke:#1A1A1A;
    classDef cross fill:#fff5e8,stroke:#8B6914;
    class OwnerHTTP,InternalHTTP,WebhookHTTP,DeviceCodeAPI inbound;
    class OAuthGH,TOTPSvc,SessionMgr,WalletMgr,BindingMgr,CredMgr,ApprovalSvc,WithdrawSvc,OnrampSvc,DepositMgr domain;
    class EFXAdapter,CIRAdapter,CBAdapter adapter;
    class AuditLog cross;
```

## 关键说明

### 三个入站平面，鉴权策略各不相同

| 平面 | 调用方 | 鉴权 |
|---|---|---|
| `Owner HTTP API` | 浏览器 / Owner Console | GitHub OAuth session cookie + 关键操作 TOTP |
| `Internal HTTP API` | `skill-server`（VPC 内） | IAM role + mTLS / 短期签名 token，不允许公网访问 |
| `Webhook HTTP` | Circle / Coinbase | 验厂商签名（Circle webhook signature / Coinbase HMAC） |
| `Device-code API` | OpenClaw plugin（公网） | `authorize` 公开；`token` 凭 device_code 轮询；`grant` 仅 owner session |

### Audit Logger 是横切关注点

任何**改变状态**的操作都必须经 Audit Logger 写入 `events` 表。这是 §11 威胁模型 T7 / T8 的硬要求 —— 即便内鬼 admin，也得在审计流水里留下不可改的痕迹。

### 几个关键的"不要"

- **Owner HTTP API 不能直接读 secret 明文**：secret 仅在颁发瞬间从 CredMgr 输出一次，立即写入响应；DB 仅存 argon2id hash
- **Wallet Manager 不能跳过 Circle Adapter 直接写 PG**：`circle_wallet_id` 必须先在 Circle 那边创建成功才能落库
- **First-payee Approval Service 不能由 skill-server 直接 grant**：skill-server 只能查询 / 触发 pending，approve / reject 必须 owner 走 Console

### 与 ledger 的边界

`wallet-api` 不直接维护 escrow 状态机。所有 ledger-touching 操作（withdraw 落 credit_lock / debit、deposit credit）都由 `wallet-api` 调用 ledger 内部 API 完成 —— ledger 是**唯一的金钱状态机**。

## 不在 `wallet-api` 里

- Escrow 创建 / 释放 / 退款的状态机（→ `ledger`）
- 24h auto-release timer 的实际触发（→ `ledger` 内部 + EventBridge）
- 对账（→ `reconciler`）
