# 01 — System Context

## 这张图回答什么

**Chief 这个系统在世界上和谁交互？**

零层视角，不展开内部。两类人 + 四个外部系统。重点看每条边的方向 / 介质 / 触发主体，回答"谁主动找谁、为了什么"。

## 图

```mermaid
graph TB
    Owner["👤 Owner<br/>(钱包归属人，<br/>Web 浏览器使用)"]
    User["👤 OpenClaw User<br/>(运行 OpenClaw 的<br/>终端用户 / agent 代理人)"]

    Chief["<b>Chief Agent Wallet</b><br/>钱包 + 账本 + 信用 + 风控<br/>基础设施"]

    Eigenflux["🌐 Eigenflux Network<br/>身份 / 撮合 / 消息<br/>(外部)"]
    Circle["🏦 Circle Gateway<br/>USDC 真实托管<br/>(外部)"]
    Coinbase["💳 Coinbase Onramp<br/>信用卡 → USDC<br/>(外部)"]
    Base["⛓ Base Mainnet<br/>USDC / x402<br/>(外部)"]
    GitHub["🔐 GitHub OAuth<br/>(外部)"]

    Owner -->|"管理钱包 / 绑定 agent / 审批<br/>(HTTPS, GitHub OAuth + TOTP)"| Chief
    User -->|"通过 OpenClaw plugin 让 agent 花钱<br/>(HMAC-signed REST/MCP)"| Chief
    User -->|"信用卡入金<br/>(Coinbase widget)"| Coinbase

    Chief -->|"鉴权"| GitHub
    Chief -->|"查询 agent 状态 / 推送通知<br/>(REST, pull + push)"| Eigenflux
    Chief -->|"创建钱包 / 余额查询 / delegation"| Circle
    Chief -->|"创建 onramp session<br/>(REST)"| Coinbase
    Chief -->|"USDC 转账 / x402 settlement<br/>(JSON-RPC + chain libs)"| Base

    Coinbase -->|"USDC 直接打到<br/>Circle Gateway 地址"| Circle
    Circle -->|"deposit webhook"| Chief
    Eigenflux -.->|"撮合后推 webhook 通知<br/>OpenClaw 安装本 plugin"| User

    classDef external fill:#f5f0e6,stroke:#8B6914,stroke-width:1px;
    classDef chief fill:#FBF8F2,stroke:#A8590D,stroke-width:2px;
    classDef person fill:#fff,stroke:#1A1A1A,stroke-width:1px;
    class Eigenflux,Circle,Coinbase,Base,GitHub external;
    class Chief chief;
    class Owner,User person;
```

## 边读图边说

- **Owner ↔ Chief**：人类钱包归属人，浏览器进 Web Console。GitHub OAuth 登录 + 关键操作 TOTP。
- **OpenClaw User ↔ Chief**：终端用户在自己的 OpenClaw 里运行 agent，agent 通过我们的 plugin 花钱；所有调用走 HMAC 签名（§04-flows）。
- **Owner = OpenClaw User？** v1 默认情况下两者**是同一个人**（自服务）。架构上分开是因为他们的会话 / 鉴权 / 设备完全不同。
- **Chief ↔ Eigenflux**：身份验证 / agent 状态 / 撮合都在 Eigenflux 这一侧。**Eigenflux 不碰钱**，只做网络层。
- **Chief ↔ Circle Gateway**：唯一真实资金托管处。Chief 自己的 ledger 是**链下镜像**，对账永远以 Circle 为准。
- **Coinbase → Circle**：onramp 资金路径**不经过 Chief 服务**，Coinbase 直接把 USDC 打到 Circle Gateway 地址，Chief 通过 Circle webhook 感知。
- **Eigenflux ⇢ User（虚线）**：可选触发路径 —— Eigenflux 可推 webhook 给 OpenClaw 实例建议安装我们的 plugin，但安装行为由用户在 OpenClaw 内确认。

## 不在这一层

- 内部服务拆分（→ [02-container.md](02-container.md)）
- Owner 和 OpenClaw User 是不是同一人的多账号语义（→ ADR-002）
- 资金真实流转 vs ledger 镜像的对账细节（→ [04-flows.md](04-flows.md) 流 4）
