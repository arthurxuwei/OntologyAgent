# ADR-007 — M5 信用系统 v1 = Raw 版（埋点 + reject_rate × volume + 单规则 anti-abuse）

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：brainstorm §3 / §8.11，design.md §7.3

## Context

投资人原型（`localhost:10000`）M5 模块标星 ★，作为护城河故事。完整版（评分模型 + verified 徽章 + 抗博弈）大约 5–7 工程师周。完全推 v2 会丢失数据底座 + demo 故事承接。

同时 v1 用户是邀请制 5–20 人 → 信誉系统**没有真实流水可训**。

## Decision

**v1 实现 raw 版**：

1. **事件埋点**：每笔 escrow 终态（RELEASED / REFUNDED）写不可变 `events` 行，含 `wallet_id` / `event_type` / `amount` / `reject_reason`（结构化）
2. **基础聚合**：per-binding 实时维护 `reject_rate`、`total_volume`、`total_count`
3. **公开查询**：`GET /v1/credit/{eigenflux_agent_id}` 返回上述三个数 + `freeze_state`
4. **单规则 anti-abuse**："One rule, no algorithm"

```
N_total   = 该 wallet 作为 seller 30 天内 RELEASED + REFUNDED 总笔数
N_reject  = 其中 buyer 主动 reject 触发 REFUNDED 的笔数（不含 24h 超时）
reject_rate = N_reject / N_total

触发 freeze 当：N_total ≥ 10  AND  reject_rate > 30%
```

### 不做（明确推 v2）

- 评分模型 / 加权 / 衰减
- Verified 徽章
- 抗博弈算法（多账号 sybil 检测、串通识别等）
- 跨平台信用合并

## Consequences

### 正向

- 工程量 ~3 周（vs 完整版 5–7 周），符合 v1 窗口
- 数据底座为 v2 真正信誉系统铺好（事件流随 v1 上线就开始累积）
- 单规则可解释，争议时容易和用户讲清
- 不假装"我们有信誉算法"，pitch deck 里也不冒充护城河上线

### 负向

- 邀请制阶段流量小，reject_rate 信号噪声大
- 30% / 30 天 / 10 笔阈值是经验初始值，v1 头月需要观察后微调
- 复杂攻击（buyer 自演自洗、多账号）单规则挡不住 —— pilot 头月人工 review 兜底（design §11 R2）

## 备选

- **完整版（含评分）** —— 5–7 周，吃掉一个全职工程师 v1 全程，否决
- **完全推 v2** —— 丢 demo 故事 + 不留数据底座，否决
- **沿用 Eigenflux 信誉服务** —— Eigenflux 团队明确不做信誉（ADR-002）
