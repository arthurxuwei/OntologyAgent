# Agent Autonomy P0 Design

## Summary

This spec defines the P0 design needed to turn the current repository from a tool-capable agent stack into a continuously running autonomous runtime for two business loops:

- trading loop: observation, decision, dry-run execution, confirmation, ledger update
- on-chain execution loop: observation, intent generation, testnet or mock execution, confirmation, reconciliation

P0 targets full closed-loop automation without human step-by-step intervention, but limits funds to dry-run or simulated trading capital and testnet or mock chain environments. The goal is not advanced strategy quality yet. The goal is stable, visible, recoverable autonomous operation.

## Current Context

The repository already includes three main capability lines:

- `agent`: Python agent runtime, interactive sessions, local tools, and an early autonomy loop
- `chain`: TypeScript MCP server for wallet state, signing, execution, UserOperation, and x402 buyer flows
- `freqtrade`: MCP server for trading status, positions, performance, and dry-run trading control

The current autonomy path already supports periodic status reads and protective guard decisions, but it is still a guard loop rather than a complete autonomous execution loop. The main gaps are not additional tools. The main gaps are workflow state, durable task memory, execution orchestration, confirmation, reconciliation, and failure handling.

## Scope

### Included In P0

- continuous autonomous runtime driven by periodic ticks
- trading closed loop in `freqtrade` dry-run mode
- full on-chain closed loop in testnet or mock environments
- shared state model for observation, intent, execution, confirmation, and failure
- execution safety through policy checks, cooldowns, and circuit breakers
- runtime visibility through status endpoints and ledger inspection

### Explicitly Excluded From P0

- real-fund autonomous execution
- MPC, TEE, multisig, or institutional custody architecture
- automatic strategy self-optimization or parameter tuning
- multi-agent distributed supervisor architecture
- event-bus-first rearchitecture of the whole system

## Design Goals

- allow the system to run continuously without a person manually pushing each step
- ensure every action has durable state before and after side effects
- prevent duplicate execution when ticks overlap, restart, or recover from failure
- distinguish successful tool invocation from confirmed business completion
- keep the design close to the current repository structure so P0 can be delivered incrementally

## Recommended Approach

Three framing options were considered:

1. minimal closed-loop supervisor
2. dual autonomous agents coordinated by a supervisor
3. event-bus-first workflow runtime

The recommended P0 approach is the minimal closed-loop supervisor. It maps cleanly onto the existing `agent/autonomy.py` pattern, keeps the first milestone focused on continuity and control, and still leaves room to evolve later into a split-agent or event-driven architecture.

## Target Architecture

P0 uses one supervisor runtime and six core modules.

### 1. Observation Collector

Purpose:
- collect standardized observations from `freqtrade` and `chain`

Responsibilities:
- fetch trading budget, bot state, open trades, performance summary, and other dry-run signals
- fetch wallet state, relevant balances, and chain execution status in mock or testnet environments
- normalize raw tool responses into a stable internal observation format

Outputs:
- `TradingObservation`
- `ChainObservation`
- a combined `RuntimeObservation`

### 2. Intent Planner

Purpose:
- transform observations into explicit executable intents

Responsibilities:
- decide whether the current state warrants a trade intent, chain intent, or no action
- separate reasoning from execution so the system can track why an action exists

Outputs:
- `TradeIntent`
- `ChainIntent`
- `NoopIntent`

Each intent must include at least:

- `intentId`
- `intentType`
- `createdAt`
- `action`
- `parameters`
- `reason`
- `confidence`
- `expiry`
- `riskTags`

### 3. Policy Guard

Purpose:
- turn safety rules into hard gates instead of soft suggestions

Responsibilities:
- allow, deny, cooldown, or trip a circuit breaker before execution starts
- validate environment boundaries such as dry-run only for trading and mock or testnet only for chain execution
- reject actions that exceed configured thresholds, violate whitelists, or repeat too frequently

Outputs:
- `PolicyDecision` with one of: `allow`, `deny`, `cooldown`, `trip_circuit`

### 4. Execution Orchestrator

Purpose:
- advance approved intents through stepwise workflows until they are confirmed or failed

Trading workflow:
- observe current trading state
- create trade intent
- run policy checks
- submit trading action
- query order outcome
- update budget and position ledger
- close or fail the execution

Chain workflow:
- observe current chain state
- create chain intent
- run policy checks
- validate parameters and environment
- sign or construct execution payload
- submit transaction or operation
- wait for receipt or confirmation
- reconcile balances or emitted events
- close or fail the execution

### 5. Ledger Store

Purpose:
- persist autonomy state across ticks and restarts

The ledger must store:

- latest normalized observation
- active intents
- active executions
- recent execution history
- last successful confirmations by action type
- failure counters by tool and action type
- cooldown windows
- circuit breaker state
- current runtime mode and last tick timestamps

The ledger is the source of truth for continuity. The next tick must read from ledger and real-time observations instead of assuming a clean slate.

### 6. Autonomy Runtime

Purpose:
- schedule ticks, restore unfinished work, protect against overlap, and expose runtime status

Responsibilities:
- run one supervisor-controlled loop
- resume unfinished executions if they are recoverable
- avoid concurrent duplicate handling of the same intent
- pause or degrade safely when guardrails trip

## Shared State Model

Every autonomous action should move through a single shared state machine:

`observed -> planned -> approved_by_policy -> executing -> confirmed -> reconciled -> closed`

Terminal failure path:

`observed -> planned -> approved_by_policy -> executing -> failed`

Special runtime states may also include:

- `cooldown`
- `paused`
- `circuit_open`

This state machine is shared across trading and chain execution so the runtime can reason about progress, recovery, and visibility consistently.

## Capability Gaps To Close

The repository currently needs these capabilities to support P0.

### Cross-Cutting Gaps

1. unified task state machine
2. durable ledger for active and historical runtime state
3. explicit intent layer between observation and execution
4. execution orchestration for multi-step actions
5. confirmation and reconciliation as first-class workflow stages
6. retry and idempotency controls
7. circuit breaker and safe degradation behavior
8. stronger runtime observability

### Trading Loop Gaps

1. standardized trading observations instead of ad hoc context assembly
2. structured decision output containing action, size, pair, rationale, confidence, expiry, and risk tags
3. order lifecycle tracking through accepted, partial, filled, cancelled, or rejected states
4. position and budget ledger updates before and after execution
5. action throttling through cooldowns and duplicate suppression
6. hard trading risk controls such as max size, drawdown stop, and missing-signal pause

### Chain Execution Loop Gaps

1. explicit modeling of transfer, x402 payment, contract execution, and UserOperation actions
2. pre-execution validation for environment, balances, allowed methods, and concurrency
3. post-submit confirmation using receipts or equivalent confirmation proofs
4. chain-side idempotency linking `intentId`, `executionId`, and chain identifiers such as `txHash`
5. error classification across construct, sign, submit, revert, timeout, and reconcile stages
6. clear completion rules for testnet and mock environments so local success is not mistaken for business completion

## Priority Order

### Must-Have P0 Foundations

1. unified task state machine
2. expanded ledger store
3. intent layer
4. execution orchestrator
5. confirmation and reconciliation module

### Second-Wave P0 Capabilities

6. retry and idempotency
7. circuit breaker and degradation paths
8. enhanced status surfaces and observability

### Later-Phase Capabilities

9. automatic strategy review and tuning
10. multi-agent division of responsibility
11. real-fund custody-grade controls

## Runtime Data Flow

Each tick should follow this standard sequence:

`collect observation -> derive intent -> policy check -> execute workflow -> confirm result -> reconcile state -> persist ledger -> schedule next tick`

When a step fails:

`classify failure -> update ledger -> retry or pause -> expose status`

This sequence is the core of the P0 autonomy design. A loop that only calls tools is not sufficient. The runtime must carry the action through confirmation or explicit failure.

## Failure Handling

P0 requires explicit failure classes so continuous operation stays controlled.

Required classes:

- observation failure
- planning failure
- policy denial
- execution submission failure
- order not accepted or rejected
- chain confirmation timeout
- chain revert
- reconciliation mismatch
- repeated tool failure

Required responses:

- mark the exact failed stage in ledger
- increment scoped failure counters
- retry only when the action is safe to retry
- open a circuit breaker when repeated failure crosses threshold
- degrade to observation-only mode when execution safety is uncertain

## Idempotency Requirements

P0 must prevent duplicate side effects caused by repeated ticks, recovery, or partially completed workflows.

Requirements:

- every planned action gets a stable `intentId`
- every execution attempt gets a stable `executionId`
- execution artifacts such as order identifiers or `txHash` are bound to the execution record
- the runtime must check for an existing active execution before creating a new one for the same intent
- recovery logic must continue or close the existing execution instead of replaying the side effect blindly

## Observability Requirements

At any time the system should expose:

- whether autonomy is enabled and running
- the current active intent and execution stage
- the latest successful trade action
- the latest successful chain action
- the latest failure and its stage
- current cooldowns and circuit breaker state
- recent execution history for debugging and audit

The design does not require a sophisticated dashboard in P0, but it does require status surfaces detailed enough to answer what is running, what failed, and whether the runtime is safe to continue.

## Acceptance Criteria

P0 is considered complete when all of the following are true.

1. Continuous runtime
- the supervisor can run for multiple ticks without manual step-by-step intervention
- each tick can observe state, choose work, and advance the workflow

2. Trading loop closed
- the system can generate structured trade intents from normalized observations
- it can perform dry-run trading actions
- it can confirm resulting order state and update budget or position ledger state

3. Chain loop closed
- the system can generate structured chain intents in mock or testnet environments
- it can complete sign, submit, confirm, and reconcile stages
- each chain execution ends in `closed` or `failed`, never in an undefined limbo state

4. Recoverable failures
- expected tool and confirmation failures do not permanently stall the runtime
- the system can retry, pause, or trip a circuit breaker according to policy

5. Visible state
- operators can inspect runtime state and understand what the autonomy loop is doing now and what happened most recently

6. Idempotent execution
- duplicate ticks or restart recovery do not create duplicate business side effects for the same intent

## Out-of-Scope Risks To Revisit After P0

- production custody and key protection for real funds
- advanced autonomous portfolio management quality
- distributed multi-agent coordination
- broad event-driven workflow infrastructure

These are important, but they should not block the first milestone of getting the process truly closed-loop and continuously running.

## Final Recommendation

Do not prioritize adding more tools first. Prioritize turning the existing tool surface into a controlled autonomous runtime with these foundational pieces:

- state machine
- ledger
- intent
- orchestrator
- confirmation and reconciliation
- retry and circuit breaker behavior

Once these are in place, the repository can move from guarded tool use to actual autonomous operation for dry-run trading and testnet or mock chain execution.
