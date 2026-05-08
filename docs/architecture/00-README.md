# Chief Agent Wallet — 架构文档（C4 模型）

> **状态**：v1 设计阶段
> **日期**：2026-05-06
> **作用对象**：Chief Agent Wallet v1（B-pilot：Base mainnet + Circle Gateway 真实托管，邀请制 5–20 个 OpenClaw 用户，3 人 × 3.5 月）
> **来源 spec**：`docs/superpowers/specs/2026-05-05-agent-wallet-v1-brainstorm.md` + `docs/superpowers/specs/2026-05-05-agent-wallet-v1-design.md`

## 阅读顺序

1. [01-context.md](01-context.md) — 系统上下文：我们是谁，跟谁交互
2. [02-container.md](02-container.md) — 容器图：系统内部由哪些服务 / 数据库 / 队列组成
3. [03-components/](03-components/) — 组件图：每个服务内部的关键组件
4. [04-flows.md](04-flows.md) — 关键业务流程时序图
5. [05-deployment.md](05-deployment.md) — AWS 部署拓扑
6. [06-decisions/](06-decisions/) — 架构决策记录（ADR）

## 命名约定

- **Chief**：本系统对外品牌（产品名 = Agent Wallet）
- **OpenClaw**：v1 唯一目标终端 agent 运行环境
- **Eigenflux**：网络/身份/撮合/消息层（外部）
- **Owner**：人类用户，通过 Web Console 管理一个或多个钱包 + 绑定 agent
- **Agent**：Eigenflux 网络上的代理，绑定 Chief wallet 后可花钱
- **Binding**：Eigenflux Agent ID ↔ Chief wallet 的一对一映射
- **Credential**：Chief 颁发给 binding 的 (key_id, secret)，HMAC 签名授权
