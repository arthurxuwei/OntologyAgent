## Overview

The repository now supports a narrow bridge from Freqtrade-originated trade intent into Base chain execution. What it still lacks is a clean way to ask Freqtrade for a strategy signal without immediately turning that signal into an order request. This work adds that missing read-only layer.

The goal is to let operators or the agent manually trigger a strategy evaluation for one pair and receive a normalized signal such as `buy`, `sell`, or `hold`. The result should explain why the signal was chosen and provide enough context for the caller to decide whether to stop at inspection or continue into the existing `emit_trade_intent -> chain_execute_trade_intent` flow later.

## Problem Statement

Today the system can:

- inspect Freqtrade status and dry-run state
- manually force entry or exit actions
- emit a normalized trade intent for the chain bridge

It cannot yet do one important intermediate step: ask Freqtrade whether the active strategy believes the current market state warrants entry, exit, or no action.

Without that capability:

- operators cannot inspect strategy output independently of execution
- the agent cannot consume a read-only signal before deciding whether to escalate to trade intent generation
- signal quality and execution quality remain coupled together, which makes debugging and rollout harder

## Goals

- Add a read-only Freqtrade signal evaluation tool
- Keep signal evaluation separate from execution and trade intent emission
- Return a normalized `buy` / `sell` / `hold` output for a single pair
- Include enough context to explain the signal result to operators and downstream callers
- Keep V1 constrained to one pair, one strategy, one timeframe, and one manual trigger path

## Non-Goals

- Automatic signal polling or scheduling
- Multi-pair scanning in V1
- Autonomous signal-to-execution chaining in V1
- Full strategy backtesting or performance analytics in this feature
- Multi-strategy routing or model selection
- Re-implementing Freqtrade strategy logic in `agent`

## Considered Approaches

### Approach 1: Recommended

Add a read-only signal evaluation MCP tool in `freqtrade` that evaluates one pair and returns a normalized signal payload.

Pros:

- Keeps signal generation inside Freqtrade where strategy logic already belongs
- Cleanly separates signal evaluation from execution
- Easiest path to later reuse in the agent and autonomy workflows

Cons:

- Requires a new strategy-facing evaluation seam in the Freqtrade integration layer

### Approach 2: Merge signal evaluation into `emit_trade_intent`

Make Freqtrade emit a trade intent only when a signal exists, and derive signal meaning from that response.

Pros:

- Fewer tools

Cons:

- Couples read-only signal inspection to execution-oriented semantics
- Makes it harder to inspect `hold` cases
- Reduces observability when debugging strategy behavior

### Approach 3: Infer signals from status and open-trade state

Use dry-run status, open trades, and summary endpoints to guess whether Freqtrade is bullish, bearish, or neutral.

Pros:

- Smallest implementation surface

Cons:

- Not a real strategy signal
- Produces misleading conclusions when there is no current position change
- Breaks down once position state and market state diverge

## Chosen Design

Add a new read-only MCP tool in `freqtrade` named `evaluate_trade_signal`. It will evaluate the current strategy for one supported pair and return a normalized signal payload.

The output is not an order and not a trade intent. It is an inspection result that downstream consumers may later use to decide whether to request an execution-oriented action.

## Architecture

### 1. Signal Evaluation Boundary

Signal evaluation belongs on the Freqtrade side. Neither `agent` nor `chain` should compute or infer technical strategy conditions. Their role is to consume the result, not to recreate it.

V1 ownership:

- `freqtrade`: evaluate strategy conditions and return normalized signal data
- `agent`: discover and expose the signal tool for operator use
- `chain`: unchanged for this feature

### 2. Signal Source

The signal must come from the active Freqtrade strategy logic, not from position state inference.

V1 should evaluate the latest available candle for one pair and determine:

- whether entry conditions are currently triggered
- whether exit conditions are currently triggered
- whether an open position exists for the pair
- the final normalized signal

Recommended V1 decision rule:

- if entry is triggered and there is no open position: `buy`
- if exit is triggered and there is an open position: `sell`
- otherwise: `hold`

This keeps the read-only signal consistent with later intent generation without forcing the two layers to be the same tool.

### 3. MCP Interface

Add a new MCP tool:

- `evaluate_trade_signal`

Suggested V1 input:

- `pair`
- optional `strategy`
- optional `timeframe`

V1 may still enforce fixed defaults internally, but the payload shape should be future-compatible.

Suggested V1 output:

- `pair`
- `strategy`
- `timeframe`
- `signal`
- `reason`
- `confidence`
- `hasOpenPosition`
- `entryTriggered`
- `exitTriggered`
- `observedAt`

Signal values are restricted to:

- `buy`
- `sell`
- `hold`

### 4. Confidence Semantics

V1 should not attempt to produce model-grade probabilities. If the strategy has no native numeric confidence, return a simple heuristic value that is explicit and stable.

Recommended V1 convention:

- `0.7` for a clear actionable signal (`buy` or `sell`)
- `0.5` for `hold`

The value exists to support UX and downstream thresholding later, not to claim statistical precision.

### 5. Reason Semantics

The tool must always return a short human-readable reason. The reason should explain the normalized decision, not just the raw booleans.

Examples:

- `entry conditions satisfied on latest candle and no open position`
- `exit conditions satisfied while position is open`
- `no actionable entry or exit signal on latest candle`

This ensures the result is understandable in logs, CLI output, and future UI surfaces.

## V1 Scope

Included in V1:

- one manual trigger path
- one pair: `ETH/USDC`
- one strategy: current default strategy
- one timeframe: current strategy default timeframe
- one normalized signal response

Explicitly excluded from V1:

- scanning multiple pairs in one call
- autonomous looping
- direct chaining from signal to trade intent
- multi-strategy comparison
- backtest-style analytics or chart snapshots

## Failure Handling

Failures should remain read-only and diagnosable.

### Unsupported Pair

If the caller requests an unsupported pair, the tool should reject the request before strategy evaluation.

### Strategy Resolution Failure

If the requested or default strategy cannot be resolved, return a structured error explaining that the strategy is unavailable.

### Market Data Failure

If the latest candle data cannot be loaded or is stale, return a structured error rather than fabricating a `hold` signal.

### Evaluation Failure

If strategy evaluation throws unexpectedly, surface the failure as an evaluation error with enough detail for operator debugging.

## Integration Notes

This feature should not automatically alter the existing execution bridge, but it should be designed to compose with it later.

Expected next-step composition after V1:

1. `evaluate_trade_signal(pair)`
2. if `signal == buy`, optionally call `emit_trade_intent(pair, ...)`
3. route resulting intent into `chain_execute_trade_intent`

That sequence should remain an orchestration choice in `agent`, not an implicit side effect of signal evaluation.

## Testing Strategy

V1 should include focused tests for the normalized signal contract.

Minimum cases:

- entry triggered with no open position -> `buy`
- exit triggered with open position -> `sell`
- no actionable trigger -> `hold`
- unsupported pair -> structured failure

If the underlying strategy evaluation seam requires fakes or stubs, prefer testing the normalization layer with deterministic inputs rather than trying to stand up a full trading runtime in unit tests.

## Summary

The right V1 is a read-only `evaluate_trade_signal` capability inside `freqtrade`. It should evaluate one pair using real strategy logic, return a normalized `buy` / `sell` / `hold` result with explanation and position context, and stay clearly separate from execution-oriented tools. That gives the system a safe inspection layer before any future automatic intent generation or chain execution.
