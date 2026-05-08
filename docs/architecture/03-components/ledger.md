# 03 — Component / `ledger` (+ `reconciler`)

## 这张图回答什么

**唯一的金钱状态机长什么样？timer 谁触发？事件流怎么落？**

`ledger` 是 Chief 的金钱真理源。任何"钱动了一下"的写入都必须经过它，且必须**事务性**：要么 ledger 写成功，要么完全不发生。`reconciler` 是它的下游守门员，每天比对外部托管真值。

## 图

```mermaid
graph TB
    subgraph callers["调用方（仅内部）"]
        WalletAPI[wallet-api]
        SkillSrv[skill-server]
    end

    subgraph ledger["ledger (ECS Fargate)"]
        InternalAPI["<b>Internal API</b><br/>credit / escrow CRUD"]
        EscrowSM["<b>Escrow State Machine</b><br/>LOCKED → RELEASED /<br/>REFUNDED / EXPIRED"]
        BalanceMgr["<b>Balance Manager</b><br/>available / locked<br/>事务级原子写"]
        CreditFlow["<b>Credit / Debit Service</b><br/>(deposit / withdraw 入口)"]
        EventStore["<b>Event Store</b><br/>(append-only events 表)"]
        TimerSvc["<b>Timer Service</b><br/>24h auto-release<br/>扫描 + 触发"]
        AntiAbuse["<b>Anti-abuse Engine</b><br/>单规则: reject_rate × volume<br/>→ freeze 写入"]
        M5Aggregator["<b>M5 Raw Aggregator</b><br/>per-binding reject_rate /<br/>volume / freeze 状态"]
    end

    subgraph reconciler["reconciler (ECS scheduled task)"]
        ReconJob["<b>Reconciliation Job</b><br/>每日 02:00 UTC"]
        DiffCalc["<b>Diff Calculator</b><br/>ledger total vs<br/>Circle delegation total"]
        FreezeCtl["<b>Platform Freeze Controller</b><br/>diff > $10 → 全平台<br/>lock/withdraw 冻结"]
    end

    subgraph data["数据"]
        PG[(PostgreSQL)]
        EB[EventBridge<br/>schedule]
    end

    subgraph external["外部"]
        Circle[Circle Gateway]
        SNS[SNS → Slack]
    end

    WalletAPI --> InternalAPI
    SkillSrv --> InternalAPI

    InternalAPI --> EscrowSM
    InternalAPI --> CreditFlow

    EscrowSM --> BalanceMgr
    CreditFlow --> BalanceMgr
    EscrowSM --> AntiAbuse
    EscrowSM --> EventStore
    BalanceMgr --> EventStore
    CreditFlow --> EventStore

    AntiAbuse --> M5Aggregator
    M5Aggregator --> PG
    EventStore --> PG
    BalanceMgr --> PG

    EB -->|"24h 扫描周期"| TimerSvc
    TimerSvc --> EscrowSM

    EB -->|"日 02:00 UTC"| ReconJob
    ReconJob --> DiffCalc
    DiffCalc -->|"读"| PG
    DiffCalc -->|"读"| Circle
    DiffCalc -->|"diff > $10"| FreezeCtl
    FreezeCtl -->|"写 platform_state"| PG
    FreezeCtl -->|"page on-call"| SNS

    classDef inbound fill:#FBF8F2,stroke:#A8590D;
    classDef sm fill:#fff,stroke:#A8590D,stroke-width:2px;
    classDef logic fill:#fff,stroke:#1A1A1A;
    classDef cross fill:#fff5e8,stroke:#8B6914;
    classDef ext fill:#f5f0e6,stroke:#8B6914;
    class InternalAPI inbound;
    class EscrowSM,BalanceMgr sm;
    class CreditFlow,TimerSvc,AntiAbuse,M5Aggregator,ReconJob,DiffCalc,FreezeCtl logic;
    class EventStore cross;
    class PG,EB,Circle,SNS ext;
```

## 关键说明

### Escrow 状态机 + Balance Manager 是同一个事务

`escrows` 状态变化和 `balances`（available / locked）的变化必须在**同一个 PG 事务**里完成。任何中间故障都不能让"钱锁了但 escrow 没记上"或反向。

```sql
BEGIN;
  UPDATE escrows SET state='LOCKED', locked_at=now() WHERE id=$1;
  UPDATE balances SET available = available - $2, locked = locked + $2 WHERE wallet_id=$3;
  INSERT INTO events (binding_id, event_type, ...) VALUES (...);
COMMIT;
```

### Event Store 是 append-only

`events` 表**永不更新**，仅追加。这是：
- M5 raw 聚合的输入源
- Audit log 的存储面
- Reconciliation 调查时的回放素材

任何"修复历史数据"操作必须新增反向事件，不能 UPDATE 旧记录。

### Timer Service 不是单独 cron

24h auto-release 用 EventBridge schedule 每分钟扫一次 `escrows WHERE state='LOCKED' AND expires_at < now()`，命中即触发状态转换 `LOCKED → EXPIRED → RELEASED`。

不用 PG 触发器、不用进程内 setTimeout —— 重启 / scale-out 都不能漏 timer。

### Anti-abuse 与 M5 Aggregator 实时性

Anti-abuse 单规则在每次 `escrow.RELEASED` / `REFUNDED` 终态后**同步**重算受影响 binding 的 `reject_rate`。命中阈值 → 直接写 `bindings.frozen_as_seller=true`。这是同步的、事务内的。

不做"批处理后冻结" —— 因为冻结晚一秒就可能再被锁一笔钱。

### Reconciler 的"门"

`reconciler` 不只是一个 monitoring 任务，它是 v1 的最后一道**主动停机闸门**：

```
diff 阈值       动作
≤ $0.01        log，过
≤ $10          warning，page Slack
> $10          写入 platform_state.frozen=true
              → wallet-api / skill-server 后续所有 lock / withdraw 直接拒绝
              → page on-call 立即介入
```

详见 ADR-008。

## 不在 `ledger` 里

- Onramp / withdraw 的外部调用（→ `wallet-api`）
- HMAC 鉴权（→ `skill-server`）
- Circle webhook 签名验证（→ `wallet-api`）
- M5 评分模型（v2，v1 仅 raw）
