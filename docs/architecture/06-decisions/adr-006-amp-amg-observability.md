# ADR-006 — Observability Stack = AMP + AMG（OpenTelemetry + ADOT sidecar）

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：design.md §2.3, §12

## Context

v1 是 mainnet money 产品，必须有可观测性 + 可告警。3 人小队 / B-pilot 流量 / 已 AWS-native 的语境下选型。候选：

- **Datadog**：全家桶 APM，成熟，**~$300+/mo**
- **Better Stack**：log + uptime 强，APM 较弱，**~$50/mo**
- **AMP + AMG**（Amazon Managed Prometheus / Grafana）：标准 OTel，**~$80/mo**
- **自建 Prometheus + Grafana** —— 3 人小队不值得自维护

## Decision

**AMP + AMG**，应用层用 **OpenTelemetry SDK** instrument，每个 ECS task 内置 **ADOT sidecar** 推送指标。

### 各角色

| 资产 | 选型 | 备注 |
|---|---|---|
| 应用 instrumentation | OpenTelemetry SDK | day 1 装好，trace 可后开启 |
| Metrics 采集 | ADOT collector sidecar | AWS 官方维护 |
| Metrics 存储 | AMP（Amazon Managed Prometheus） | 按量付费 ~$0.30/M samples |
| Dashboard | AMG（Amazon Managed Grafana） | $9/editor/月 |
| Logs | CloudWatch Logs | 已在 §2.3 |
| Traces | 暂不开（OTel SDK 已就绪，按需启 X-Ray 或 Tempo） | v1 不强求 |
| Alerting | AMP 内置 Alertmanager → SNS → Slack | 5 个核心 alert 上线前必须真实跑通 |

### 上线 Gate（必须真实跑通到 Slack 才能 mainnet）

1. `reconciliation.diff_usdc > $10`
2. `skill_server.auth.reject_rate > 5% / 5min`
3. `escrow.lock.error_rate > 1% / 5min`
4. `eigenflux.api.error_rate > 5% / 5min`
5. `circle.webhook.lag_seconds > 600`

## Consequences

### 正向

- 月成本 ~$80（vs Datadog ~$300+）
- 标准 OTel，零厂商锁定 —— 未来切到 Tempo / X-Ray / 任何后端不改代码
- AWS native，IAM / VPC 全打通
- ADOT 是 AWS 官方 OTel 发行版，不需要自己跟 OTel 上游

### 负向

- 比 Datadog UX 粗糙（开箱模板少，要自己写 Grafana dashboard）
- 没有 APM 自动追踪 —— 需手动加 span，但 3 人小队反而是好事
- ADOT sidecar 增加每个 task 的资源占用（~64MB / 0.05 vCPU）

## 备选

- **Datadog**：v2 团队 ≥ 10 人 + 多服务 + 预算松后再考虑
- **Better Stack**：APM 弱，对 mainnet 排障不够
- **裸 CloudWatch Metrics**：dashboard / 告警 UX 太弱，不适合 incident 场景
