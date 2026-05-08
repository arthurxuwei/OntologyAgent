# ADR-008 — Reconciliation：差异 > $10 自动 freeze 全平台

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：design.md §8，flows.md Flow 4，brainstorm §8.6

## Context

Chief 链下 ledger 是 Circle Gateway 真实托管的镜像。任何一处 bug / 内鬼 / 集成错误都可能让 ledger 和 Circle 偏离 → "钱不见了"在数字上无法追回，必须用流程兜住。

V1 是 mainnet money + 邀请制 5–20 dev，单笔上限 $100、日上限 $500、平台流水预估单日 < $10K。

## Decision

每日 02:00 UTC 跑 reconciler，按差异分级处置：

```
diff = Σ ledger.balances + Σ ledger.escrows.locked  -  Σ Circle.delegation_total

|diff| ≤ $0.01     → 忽略（rounding 容差）
|diff| ≤ $10       → warning，page Slack #chief-recon
|diff| > $10       → CRITICAL：
                     - UPDATE platform_state.frozen = true（PG 一行）
                     - skill-server / wallet-api 后续 lock + withdraw 全部拒绝
                     - release / refund 不阻塞（已 LOCKED 的钱必须能退）
                     - SNS → PagerDuty + Slack #chief-incidents
                     - 走 RB-02 runbook
                     - 解冻必须多人审批
```

### 为什么 $10

- **远低于 v1 单日流水预估** ($10K) → 极低误报概率
- **远高于 rounding tolerance** ($0.01) → 真有 bug 才触发
- **足以覆盖 1 笔上限交易**（$100 单笔）就算单笔丢失也立即看到
- 阈值由 pilot 头月观察实际 diff 分布后再调整（写入 §13 TBD）

## Consequences

### 正向

- 任何一处导致钱"消失"或"凭空多出"的 bug，最长 24h 被发现
- $10 阈值在 v1 流量下信号 / 噪声比合理
- 自动 freeze 不依赖人工反应速度，最快几秒内停掉新风险敞口
- release 不阻塞 —— 不会因为 freeze 把已锁的钱卡死

### 负向

- 误报会冻全平台（运维严重 incident）—— 缓解：阈值经过 pilot 头月校准 + RB-02 runbook 明确"先解冻 + 后调查"还是反向
- 解冻多人审批 → on-call 单人无法快速恢复 —— 这是设计意图（T8 内鬼防御 > MTTR）
- 如果 reconciler 自身 bug，v1 没有 reconciliation-of-reconciliation —— v1.1 加冗余对账（不同算法重算）

## 备选

- **diff > 1% 触发 freeze** —— 流水小 + 阈值随流量浮动 = 容易在低流水期假阴
- **diff > $100 触发 freeze** —— 单笔交易就能潜伏未发现，否决
- **不 freeze，仅告警** —— mainnet money 不能赌人工反应时间，否决
- **freeze 单 wallet 而非全平台** —— diff 出现时不知道是哪个 wallet 的偏差，单 wallet freeze 没法精准
