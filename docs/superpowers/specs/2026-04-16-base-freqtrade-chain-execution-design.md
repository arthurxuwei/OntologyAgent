## Overview

The current flow treats Base Sepolia assets and Freqtrade dry-run funds as separate systems. That is acceptable for observability, but it does not solve the missing execution path: Base test assets cannot be handed to Freqtrade for simulated or real trading because Freqtrade is not the chain-side asset holder in this architecture.

The design for this work is to stop treating Freqtrade as the wallet or settlement layer. Base wallets remain the source of funds. Freqtrade remains responsible for strategy, signal generation, and risk decisions. Chain-side execution remains in the `chain` service. The missing piece is a bridge that converts trading intent from Freqtrade-facing semantics into an executable chain request.

This preserves the repository's current boundary split:

- `freqtrade` owns strategy and trading intent
- `chain` owns Base execution, signing, and settlement
- `agent` or a thin bridge orchestrates the handoff and result reporting

## Problem Statement

The blocked step is not literally transferring Base testnet assets into Freqtrade. The real issue is that the system has no formal path for using Base wallet funds to satisfy a Freqtrade-originated trading decision.

Today:

- `chain` exposes Base wallet and transfer tools
- `freqtrade` exposes trading and dry-run tools
- `sync_dry_run_wallet` only adjusts simulated capital, not executable chain funds

This means Base test assets can exist in the chain wallet while Freqtrade can still only operate in CEX-style or dry-run semantics. The system needs a supported path where Freqtrade issues a trade decision and `chain` executes it directly against Base liquidity.

## Goals

- Allow Base Sepolia wallet funds to be used for Freqtrade-originated trades
- Keep chain execution outside of Freqtrade
- Preserve clear ownership boundaries between strategy and settlement
- Make failures diagnosable by stage rather than surfacing as a generic trade failure
- Ship a narrow V1 that proves the architecture with one pair, one execution route, and one wallet

## Non-Goals

- Making Freqtrade directly own or custody Base assets
- Replacing the existing dry-run wallet flow
- Supporting multiple DEXes or aggregators in V1
- Supporting full autonomous strategy execution in V1
- Building complete chain-side portfolio accounting in V1
- Adding broad token-pair support before one path is proven

## Considered Approaches

### Approach 1: Recommended

Keep Freqtrade as the strategy engine and add a bridge that converts trading intent into a `chain` execution request.

Pros:

- Matches current service boundaries
- Avoids duplicating wallet, gas, nonce, allowance, and quote logic in Freqtrade
- Keeps all chain-specific concerns in one place
- Easiest to reason about operationally

Cons:

- Requires a new intent schema and an orchestration step

### Approach 2: Direct DEX integration inside Freqtrade

Teach Freqtrade to quote, approve, and execute directly on Base.

Pros:

- Superficially shorter flow

Cons:

- Breaks the current service boundary
- Pulls chain mechanics into Freqtrade
- Makes failures harder to localize
- Increases future maintenance cost

### Approach 3: Stay dry-run only and mirror chain funds informally

Continue using `sync_dry_run_wallet` and treat Base balances as an external reference.

Pros:

- Smallest code change

Cons:

- Does not solve the actual missing path
- Still cannot execute trades with Base wallet funds

## Chosen Design

The system should implement a formal intent-to-execution bridge.

Flow ownership:

- `freqtrade`: decide what to trade
- bridge in `agent` or adjacent orchestration layer: normalize and hand off the decision
- `chain`: validate, quote, approve if needed, execute, and report the chain result

The bridge is the only new architectural element. It is intentionally thin and should not contain strategy logic or chain-specific execution logic beyond request translation and response normalization.

## Architecture

### 1. Trading Intent Model

Introduce a chain-oriented trade intent payload as the handoff contract between the strategy side and the execution side.

Suggested minimum fields:

- `intentId`
- `strategy`
- `pair`
- `side`
- `baseChain`
- `sellToken`
- `buyToken`
- `amount`
- `amountType`
- `limitPrice`
- `maxSlippageBps`
- `reason`
- `createdAt`

Design notes:

- `pair` remains the strategy-facing identifier such as `ETH/USDC`
- `sellToken` and `buyToken` are the concrete Base token contract addresses used by `chain`
- `amountType` allows a clean distinction between base-asset amount and quote-asset amount if needed later, but V1 can still support a single convention
- `intentId` gives the system a stable correlation key for retries, logs, and UI status

### 2. Pair and Token Mapping

The bridge needs a deterministic mapping from a strategy pair to Base token metadata.

V1 should support exactly one pair mapping. Example:

- strategy pair: `ETH/USDC`
- Base chain tokens: Base Sepolia WETH and Base Sepolia USDC contract addresses

If a mapping does not exist, the bridge rejects the trade before any chain call is attempted.

### 3. Freqtrade Changes

Freqtrade-side semantics should change from "execute trade" to "emit executable intent" for the chain route.

For V1, the least disruptive option is to add a new MCP tool or a narrow variant of an existing force-entry path that returns a normalized `trade_intent` instead of trying to complete the trade itself.

Responsibilities:

- validate strategy-side trade arguments
- encode the intended pair, side, and size
- include strategy and reason metadata
- return a normalized response for the bridge

Freqtrade should not:

- hold private keys
- calculate gas behavior
- manage allowances
- submit chain transactions

### 4. Chain Changes

`chain` needs a higher-level execution entry point that accepts a normalized trade intent and performs the Base-side execution flow.

Suggested V1 responsibilities:

- validate chain and token mapping
- inspect wallet balances
- fetch quote from the chosen execution source
- ensure allowance if required
- submit the swap or execution transaction
- normalize the result into a structured response

Suggested result shape:

- `intentId`
- `status`
- `txHash`
- `sellToken`
- `buyToken`
- `sellAmount`
- `buyAmount`
- `averagePrice`
- `gasUsed`
- `failureStage`
- `failureReason`

### 5. Agent or Bridge Changes

The bridge should live where orchestration already belongs. In this repository, that most naturally means `agent`, unless a very small dedicated helper module is introduced for separation.

Responsibilities:

- request a trade intent from the Freqtrade side
- validate that the intent is chain-routable
- call the `chain` execution tool
- record and expose structured execution outcomes
- surface stage-specific errors in API and operator views

The bridge should not decide trading strategy and should not embed DEX logic.

## Execution Flow

Each trade follows the same staged path:

1. Freqtrade produces `trade_intent`
2. The bridge validates the intent and pair mapping
3. `chain` checks wallet balances and token metadata
4. `chain` requests a quote from the configured execution source
5. `chain` performs `approve` if needed
6. `chain` submits the onchain execution
7. The bridge records and returns the normalized outcome

This design intentionally redefines the original problem. Base assets are not transferred into Freqtrade. They stay in the Base wallet and are consumed directly by `chain` after Freqtrade issues intent.

## Failure Handling

Failures must be explicit and stage-specific.

### Intent Failure

Examples:

- pair is unsupported
- requested side is unsupported in V1
- amount is malformed

Behavior:

- reject before any chain call
- return a structured validation error

### Funding Failure

Examples:

- insufficient ETH or USDC
- missing gas balance for the transaction path

Behavior:

- return current balance, required amount, and blocked asset

### Quote Failure

Examples:

- no liquidity
- execution source unavailable
- quote outside acceptable slippage limits

Behavior:

- return quote-stage failure without submitting a transaction

### Approval Failure

Examples:

- approve transaction reverted
- allowance did not reach the required state

Behavior:

- stop before swap submission
- preserve approval transaction metadata when available

### Execution Failure

Examples:

- transaction reverted
- gas too low
- slippage exceeded after submission

Behavior:

- return `txHash` if available
- preserve revert or RPC reason where possible

### Settlement Sync Failure

Examples:

- chain transaction succeeded but the bridge failed to record or expose the outcome

Behavior:

- mark separately from execution failure
- never report this as "trade not sent"

## V1 Scope

V1 should be intentionally narrow.

Included in V1:

- one Base Sepolia wallet
- one supported pair mapping
- one supported direction or one small bidirectional pair path if implementation cost is negligible
- one execution source
- one Freqtrade-originated manual trigger path
- structured result reporting with transaction metadata and failure stage

Explicitly excluded from V1:

- broad pair support
- multiple wallets
- full autonomous execution loops
- complex position management
- complete portfolio and PnL reconciliation
- informal dry-run wallet syncing presented as real execution

## Recommended V1 Sequence

1. Define the `trade_intent` schema
2. Add the single supported pair-to-token mapping
3. Add a Freqtrade MCP path that emits normalized intent
4. Add a `chain` MCP path that executes normalized intent
5. Add bridge orchestration and operator-facing result reporting in `agent`
6. Run one Base Sepolia end-to-end execution with small test funds
7. Only after that, evaluate whether strategy automation should call the same route

## Testing Strategy

The implementation should be verified at three levels.

### Contract Tests

- Freqtrade intent output matches the expected schema
- unsupported pairs fail before execution
- chain execution results normalize success and failure consistently

### Integration Tests

- bridge requests intent and routes it to chain execution
- insufficient funds fail at the funding stage
- missing quote fails at the quote stage
- successful execution returns a normalized receipt payload

### Environment Validation

- Base Sepolia wallet holds the required test assets
- selected execution source is reachable from the `chain` runtime
- supported token addresses and decimals are configured correctly

## Operational Notes

- `sync_dry_run_wallet` can remain for simulation and bookkeeping, but it must not be presented as the chain execution path
- health and debug views should display whether the system is configured for dry-run only or chain-routable execution
- logs should always include `intentId`, pair, side, execution stage, and chain transaction identifiers when present

## Open Decisions For Implementation Planning

These do not block the design, but they must be chosen before implementation begins:

- which single execution source V1 will use
- whether V1 supports one direction only or a minimal two-way pair
- whether the bridge logic lives directly in `agent/main.py` or in a small helper module
- which exact MCP tool names will represent "emit intent" and "execute intent"

## Summary

The correct fix is not to transfer Base testnet funds into Freqtrade. The correct fix is to make Base wallets the settlement account, keep Freqtrade as the strategy source, and add a formal bridge that converts Freqtrade trade intent into chain execution. That approach is the smallest design that solves the current blocked flow without collapsing service boundaries.
