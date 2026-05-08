# ADR-001 — 路线 B'：开新 repo `chief/`，从旧 repo 提炼

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：brainstorm §8.5

## Context

旧 repo `OntologyAgent/` 同时包含：
- 我们要保留的 `ledger`、`chain` 链上能力、x402 buyer
- 我们要淘汰的 `agent`（LangGraph 大脑）、`freqtrade`、autonomy loop

继续在旧 repo 演进 vs 开新 repo `chief/` 提炼复用资产，存在选择。

## Decision

**开新 repo `chief/`**，从旧 repo 搬运可复用资产到新结构：

| 旧位置 | 新位置 |
|---|---|
| `ledger/` | `services/ledger/` |
| `chain/` x402 buyer | `packages/x402-buyer/` |
| `chain/` Circle wallet provisioning | `packages/circle-custody/` |
| `chain/` 风控 caps | `packages/risk-policy/` |
| `route_payment_intent` | `packages/payment-router/` |
| `agent/` / `freqtrade/` / `autonomy/` | **不搬，旧 repo 归档** |

## Consequences

### 正向

- 新 repo 没有"曾经我们也做过 agent"的命名 / 历史污染
- 服务边界从 day 1 清晰（packages vs services 显式区分）
- v1.1 不需要再做改名 / 历史迁移
- CI/CD / IaC / dev env 围绕新结构从零搭，比改造旧的更快

### 负向

- 开头 2–3 周搬运 + 重打包，没有产品交付
- 旧 repo 里某些隐式依赖可能被遗漏，需要双 repo diff 校验
- 旧 repo 里的提交历史不带过来（搬过来的代码看 git blame 会指向 commit "initial import from OntologyAgent"）

## 备选

- **路线 A'：在现有 repo 内增量演进** —— 否决：v1.1 改名 / 迁移成本和 B' 一次性成本相当，B' 长期更好
- **路线 C'：双 repo 过渡** —— 否决：3 人小队维护成本翻倍
- **从 0 重写**（连 ledger 都不搬）—— 否决：放弃 70% 已实现资产，明显错误
