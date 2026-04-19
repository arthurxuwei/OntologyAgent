## Overview

The repository now has a working read-only `evaluate_trade_signal` flow, but the default `SimpleAgentStrategy` still produces no actionable signals because it never sets entry or exit conditions. This work upgrades that default strategy into a minimal real signal producer using an EMA 9/21 crossover.

The feature also needs one configuration alignment step. The strategy and signal path are being evaluated for `ETH/USDC`, while the current Freqtrade config still uses `USDT` and a pair whitelist of `BTC/USDT` and `ETH/USDT`. V1 should align the Freqtrade-side configuration to the same `ETH/USDC` target used by the signal-evaluation and chain-intent work.

## Problem Statement

Today:

- `evaluate_trade_signal` is operational and returns a normalized signal payload
- `SimpleAgentStrategy` always returns no signal because `populate_entry_trend()` and `populate_exit_trend()` set everything to zero
- Freqtrade config and pairlist still target `USDT` pairs instead of `ETH/USDC`

That means the signal-evaluation pipeline works mechanically, but the default strategy does not yet emit real buy/sell conditions and the configured trading universe is misaligned with the intended pair.

## Goals

- Make `SimpleAgentStrategy` produce real entry and exit signals using EMA 9/21 crossovers
- Keep the strategy small and readable
- Align the Freqtrade config to the V1 signal pair `ETH/USDC`
- Preserve the existing `evaluate_trade_signal` contract so the new strategy output flows through without further API redesign
- Add focused tests that prove the strategy generates crossover signals and that signal evaluation can surface them correctly

## Non-Goals

- Strategy parameter optimization
- Multi-pair scanning
- Trend or volume filters in V1
- Risk overlays such as cooldowns or market regime filtering
- Autonomous chaining from signal to trade intent
- Broad exchange or execution reconfiguration beyond what is needed to align the configured pair

## Considered Approaches

### Approach 1: Recommended

Implement a pure EMA 9/21 crossover in `SimpleAgentStrategy` and align the config to `ETH/USDC`.

Pros:

- Smallest strategy change that produces real signals
- Easy to explain and test
- Naturally fits the existing `evaluate_trade_signal` path

Cons:

- Susceptible to noisy crossover signals in choppy markets

### Approach 2: EMA crossover plus trend filter

Add EMA 9/21 crossover and a second filter such as price above EMA 50 for long entries.

Pros:

- Cleaner signals

Cons:

- More moving parts in V1
- Harder to debug whether “no signal” is due to crossover or filter logic

### Approach 3: Demonstration-only signal rules

Inject simple deterministic rules not based on standard technical indicators.

Pros:

- Quick to produce visible signals

Cons:

- Less representative of actual strategy behavior
- Weak foundation for later trade-intent or chain execution use

## Chosen Design

Implement a pure EMA 9/21 crossover in `SimpleAgentStrategy` on the existing 5-minute timeframe.

Signal rules:

- `buy` condition: current candle has `EMA9 > EMA21` and previous candle had `EMA9 <= EMA21`
- `sell` condition: current candle has `EMA9 < EMA21` and previous candle had `EMA9 >= EMA21`
- otherwise: no action

This keeps the strategy small, gives `evaluate_trade_signal` real input, and avoids bundling additional filters into the first live signal-producing strategy.

## Architecture

### 1. Strategy Logic

`SimpleAgentStrategy` remains the default strategy class. V1 only changes its internal signal logic.

Implementation shape:

- `populate_indicators()` computes fast and slow EMAs
- `populate_entry_trend()` flags long entry on EMA 9 crossing above EMA 21
- `populate_exit_trend()` flags long exit on EMA 9 crossing below EMA 21

Recommended indicator column names:

- `ema_fast`
- `ema_slow`

### 2. Timeframe

The strategy should keep the current `5m` timeframe for V1.

Reasons:

- no additional config churn
- matches the current signal-evaluation default
- keeps smoke tests and operator expectations stable

### 3. Pair and Stake-Currency Alignment

The Freqtrade config should be aligned with the signal target pair.

V1 alignment:

- `stake_currency`: move from `USDT` to `USDC`
- `pair_whitelist`: reduce to `ETH/USDC`

This keeps the configured trading universe consistent with:

- `evaluate_trade_signal` V1 support
- the strategy tests and smoke tests
- the earlier bridge design that uses `ETH/USDC` semantics

### 4. Signal Flow

No new MCP contract is needed. The existing flow becomes useful as soon as the strategy produces real crossover signals:

1. `SimpleAgentStrategy` calculates EMAs and sets `enter_long` / `exit_long`
2. `evaluate_trade_signal` reads those conditions through the existing strategy-evaluation seam
3. `agent` exposes the normalized result to operators

The `evaluate_trade_signal` response contract remains unchanged.

## V1 Scope

Included in V1:

- EMA 9/21 indicator calculation
- crossover-based entry and exit signals
- config alignment to `ETH/USDC`
- focused strategy tests
- signal-evaluation verification through the existing read-only flow

Explicitly excluded from V1:

- trend confirmation filters
- multiple supported pairs
- shorting
- strategy hyperparameters and tuning
- PnL optimization

## Failure Handling

Failure cases should be explicit rather than silently producing misleading `hold` results.

### Pair Misalignment

If config and signal tooling disagree on the pair, tests or smoke verification should fail clearly. The implementation should not preserve mixed `USDT` and `USDC` assumptions.

### Insufficient Candle History

EMA crossover logic depends on prior values. Unit tests and evaluation helpers should provide enough candle history to exercise both the current and previous candle comparison. V1 should not pretend to detect a crossover from a single isolated candle.

### Missing Market Data in Runtime

If live runtime data for the configured pair is unavailable, `evaluate_trade_signal` should continue to surface evaluation errors rather than fabricate a signal.

## Testing Strategy

The feature should be verified at two levels.

### Strategy Tests

Add focused tests for `SimpleAgentStrategy` that build small deterministic dataframes and assert:

- bullish crossover sets `enter_long = 1`
- bearish crossover sets `exit_long = 1`
- no crossover leaves entry and exit unset

These tests should exercise actual EMA values over multiple rows, not patched booleans.

### Signal Evaluation Verification

Add or update tests so the `evaluate_trade_signal` helper can exercise the standard `populate_*` strategy path using the real `SimpleAgentStrategy`.

Smoke verification should confirm that:

- the tool still returns a normalized payload
- the payload is now driven by a real crossover strategy instead of an always-idle default

## Recommended Rollout Order

1. Align Freqtrade config to `ETH/USDC`
2. Implement EMA 9/21 crossover in `SimpleAgentStrategy`
3. Add strategy unit tests for crossover detection
4. Verify `evaluate_trade_signal` returns valid normalized output against the updated strategy
5. Only after that, consider whether signal results should feed later intent-generation workflows

## Summary

The right next step is to turn `SimpleAgentStrategy` into a minimal real strategy using EMA 9/21 crossovers and align the Freqtrade config to `ETH/USDC`. That gives the existing read-only signal pipeline meaningful input without expanding scope into filters, optimization, or execution coupling.
