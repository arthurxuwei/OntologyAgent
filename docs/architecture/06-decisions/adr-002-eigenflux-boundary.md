# ADR-002 — Eigenflux 边界：仅网络 / 身份 / 撮合 / 消息层，钱完全归 Chief

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队 + Eigenflux 团队对齐
- **相关文档**：brainstorm §8.2，design.md §9

## Context

Chief 与 Eigenflux 网络合作运营。两边都涉及 agent 身份、消息、可能的资金。需要明确划分谁负责什么，避免双方系统对同一件事各自有不同事实（"双源真理"灾难）。

## Decision

**Eigenflux** 是**纯消息 / 身份 / 撮合层**：
- 颁发 Agent ID（全生命周期稳定，不轮换）
- 维护 agent 状态（active / inactive / revoked，仅 pull 查询）
- 提供撮合 + 服务目录
- 提供 agent ↔ agent 双向消息接口
- **不碰钱、不做信誉、不参与争议仲裁**

**Chief** 承担**全部金钱栈**：
- Circle Gateway 真实托管
- ledger 链下结算（escrow / credit / 24h timer）
- 风险承担（出钱出问题 Chief 兜底）
- 信用信号（M5 raw 完全归 Chief）
- 争议仲裁（Chief 是最终裁定方）

## A2A N1–N5 步骤归属

| 步骤 | 走哪边 |
|---|---|
| N1 Quote | Eigenflux 撮合 + 服务目录 |
| N2 Lock | **Chief ledger** |
| N3 Verify | Eigenflux 消息 + 读 Chief ledger 状态 |
| N4 Deliver | Eigenflux 消息 + Chief 24h timer |
| N5 Release | **Chief ledger** |

## Consequences

### 正向

- 两边职责零重叠，对账逻辑只看 Chief 一处
- Eigenflux 加新功能不会回头打破我们的钱模型
- v1 风险评估简单：所有金钱风险都在 Chief 自家代码里

### 负向

- Eigenflux 撮合不验签（R.Q3）→ Chief 在 N2 Lock 时无法验真 quote 真伪 → 所有伪造 / 串通风险压到 Chief 的 reject path + anti-abuse 单规则上
- Eigenflux 状态仅 pull → Chief 必须缓存 + 自建 owner kill-switch，不能依赖 Eigenflux 撤销通知

## 备选

- **Eigenflux 也做结算 / 信誉** —— Eigenflux 团队明确不做（v1）
- **Chief 把账本搬到 Eigenflux** —— 失去监管 / 风险承担清晰度，否决
