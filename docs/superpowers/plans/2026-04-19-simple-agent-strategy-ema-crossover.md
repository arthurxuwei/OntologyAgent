# SimpleAgentStrategy EMA Crossover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `SimpleAgentStrategy` into a minimal EMA 9/21 crossover strategy and align the Freqtrade config to the `ETH/USDC` pair so `evaluate_trade_signal` can return real signals for the default setup.

**Architecture:** Keep the existing `SimpleAgentStrategy` class and upgrade only its indicator and signal logic. Add one focused strategy test module that verifies crossover behavior with deterministic dataframes, then align `freqtrade/config/config.json` to the same `ETH/USDC` signal target already used by the read-only signal flow.

**Tech Stack:** Python (`pandas`, `freqtrade.strategy.IStrategy`, unittest), JSON config, existing Freqtrade MCP signal-evaluation path

---

## File Map

- Modify: `freqtrade/strategies/SimpleAgentStrategy.py`
  Responsibility: implement EMA 9/21 indicators and crossover-based entry/exit signals.
- Create: `freqtrade/tests/test_simple_agent_strategy.py`
  Responsibility: verify bullish crossover, bearish crossover, and no-crossover cases against the real strategy logic.
- Modify: `freqtrade/config/config.json`
  Responsibility: align stake currency and pair whitelist to `ETH/USDC`.
- Modify: `freqtrade/tests/test_mcp_server.py`
  Responsibility: add or adjust one signal-evaluation test so the real default strategy path is exercised under the new config assumptions.
- Modify: `README.md`
  Responsibility: document the default strategy and pair alignment at a minimal level.

## Implementation Notes

- V1 timeframe stays `5m`
- Fast EMA length: `9`
- Slow EMA length: `21`
- Entry rule:
  - current `ema_fast > ema_slow`
  - previous `ema_fast <= ema_slow`
- Exit rule:
  - current `ema_fast < ema_slow`
  - previous `ema_fast >= ema_slow`
- V1 does not add filters, shorts, or parameterization
- Config alignment:
  - `stake_currency = "USDC"`
  - `pair_whitelist = ["ETH/USDC"]`

### Task 1: Implement EMA 9/21 Signals in `SimpleAgentStrategy`

**Files:**
- Modify: `freqtrade/strategies/SimpleAgentStrategy.py`
- Create: `freqtrade/tests/test_simple_agent_strategy.py`

- [ ] **Step 1: Write the failing strategy tests in `freqtrade/tests/test_simple_agent_strategy.py`**

Create this file:

```python
import unittest

import pandas as pd

from freqtrade.strategies.SimpleAgentStrategy import SimpleAgentStrategy


def _build_dataframe(close_values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(close_values), freq="5min"),
            "open": close_values,
            "high": close_values,
            "low": close_values,
            "close": close_values,
            "volume": [1.0] * len(close_values),
        }
    )


class SimpleAgentStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = SimpleAgentStrategy(config={})
        self.metadata = {"pair": "ETH/USDC"}

    def test_populate_entry_trend_sets_enter_long_on_bullish_crossover(self) -> None:
        dataframe = _build_dataframe([10] * 25 + [9, 8, 7, 20, 25])
        dataframe = self.strategy.populate_indicators(dataframe, self.metadata)
        dataframe = self.strategy.populate_entry_trend(dataframe, self.metadata)

        self.assertEqual(int(dataframe.iloc[-1]["enter_long"]), 1)
        self.assertEqual(dataframe.iloc[-1]["enter_tag"], "ema9_cross_above_ema21")

    def test_populate_exit_trend_sets_exit_long_on_bearish_crossover(self) -> None:
        dataframe = _build_dataframe([20] * 25 + [25, 24, 23, 10, 5])
        dataframe = self.strategy.populate_indicators(dataframe, self.metadata)
        dataframe = self.strategy.populate_exit_trend(dataframe, self.metadata)

        self.assertEqual(int(dataframe.iloc[-1]["exit_long"]), 1)
        self.assertEqual(dataframe.iloc[-1]["exit_tag"], "ema9_cross_below_ema21")

    def test_populate_trends_leave_signals_unset_without_crossover(self) -> None:
        dataframe = _build_dataframe([10] * 30)
        dataframe = self.strategy.populate_indicators(dataframe, self.metadata)
        dataframe = self.strategy.populate_entry_trend(dataframe, self.metadata)
        dataframe = self.strategy.populate_exit_trend(dataframe, self.metadata)

        self.assertEqual(int(dataframe.iloc[-1]["enter_long"]), 0)
        self.assertEqual(int(dataframe.iloc[-1]["exit_long"]), 0)
```

- [ ] **Step 2: Run the strategy tests to verify they fail**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_simple_agent_strategy`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because `SimpleAgentStrategy` does not yet compute EMA indicators or crossover tags.

- [ ] **Step 3: Implement the minimal strategy logic in `freqtrade/strategies/SimpleAgentStrategy.py`**

Replace the file contents with:

```python
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class SimpleAgentStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 21
    minimal_roi = {"0": 0.10}
    stoploss = -0.10

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = dataframe["close"].ewm(span=9, adjust=False).mean()
        dataframe["ema_slow"] = dataframe["close"].ewm(span=21, adjust=False).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = "ema9_cross_above_ema21"

        bullish_cross = (dataframe["ema_fast"] > dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1)
        )
        dataframe.loc[bullish_cross, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = "ema9_cross_below_ema21"

        bearish_cross = (dataframe["ema_fast"] < dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1)
        )
        dataframe.loc[bearish_cross, "exit_long"] = 1
        return dataframe
```

- [ ] **Step 4: Run the strategy tests again**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_simple_agent_strategy`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freqtrade/strategies/SimpleAgentStrategy.py freqtrade/tests/test_simple_agent_strategy.py
git commit -m "feat: add ema crossover simple strategy"
```

### Task 2: Align Freqtrade Config to `ETH/USDC`

**Files:**
- Modify: `freqtrade/config/config.json`

- [ ] **Step 1: Add a failing config assertion test in `freqtrade/tests/test_mcp_server.py`**

Append:

```python
class FreqtradeConfigAlignmentTests(unittest.TestCase):
    def test_read_config_aligns_default_pair_to_eth_usdc(self) -> None:
        config = mcp_server.read_config()

        self.assertEqual(config["stake_currency"], "USDC")
        self.assertEqual(config["exchange"]["pair_whitelist"], ["ETH/USDC"])
```

- [ ] **Step 2: Run the focused config assertion test to verify it fails**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server.FreqtradeConfigAlignmentTests`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because the config still uses `USDT` and multiple pairs.

- [ ] **Step 3: Update `freqtrade/config/config.json`**

Apply this exact JSON change:

```json
  "stake_currency": "USDC",
```

and replace the whitelist with:

```json
    "pair_whitelist": [
      "ETH/USDC"
    ],
```

- [ ] **Step 4: Run the focused config test again**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server.FreqtradeConfigAlignmentTests`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freqtrade/config/config.json freqtrade/tests/test_mcp_server.py
git commit -m "chore: align freqtrade config to eth usdc"
```

### Task 3: Verify `evaluate_trade_signal` with the Real Default Strategy

**Files:**
- Modify: `freqtrade/tests/test_mcp_server.py`

- [ ] **Step 1: Add a failing real-strategy evaluation test**

Append:

```python
    def test_evaluate_trade_signal_state_uses_real_simple_agent_strategy(self) -> None:
        with patch.object(
            mcp_server.rest_client,
            "get",
            new=AsyncMock(return_value=[]),
        ):
            state = asyncio.run(
                mcp_server._evaluate_trade_signal_state(
                    pair="ETH/USDC",
                    strategy="SimpleAgentStrategy",
                )
            )

        self.assertEqual(state["strategy"], "SimpleAgentStrategy")
        self.assertEqual(state["timeframe"], "5m")
        self.assertIn("entryTriggered", state)
        self.assertIn("exitTriggered", state)
```

- [ ] **Step 2: Run the focused real-strategy evaluation test**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server.EvaluateTradeSignalTests.test_evaluate_trade_signal_state_uses_real_simple_agent_strategy`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS after Task 1 strategy work is in place. If this fails, fix the strategy or evaluation seam before proceeding.

- [ ] **Step 3: Add a regression check for normalized `hold` output with the real default strategy path**

Append:

```python
    def test_evaluate_trade_signal_returns_normalized_payload_for_real_default_strategy(self) -> None:
        with patch.object(
            mcp_server.rest_client,
            "get",
            new=AsyncMock(return_value=[]),
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["pair"], "ETH/USDC")
        self.assertEqual(result["strategy"], "SimpleAgentStrategy")
        self.assertEqual(result["timeframe"], "5m")
        self.assertIn(result["signal"], {"buy", "sell", "hold"})
        self.assertIn("reason", result)
```

- [ ] **Step 4: Run the full Freqtrade MCP test suite**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server freqtrade.tests.test_simple_agent_strategy`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freqtrade/tests/test_mcp_server.py freqtrade/tests/test_simple_agent_strategy.py
git commit -m "test: verify ema strategy signal evaluation"
```

### Task 4: Document the Default Strategy Alignment

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a short note about the default crossover strategy**

Insert near the Freqtrade section:

```md
### Default Freqtrade Strategy（V1）

- 默认策略 `SimpleAgentStrategy` 使用 `5m` 周期上的 `EMA 9/21` 金叉死叉
- 默认信号目标交易对为 `ETH/USDC`
- `evaluate_trade_signal` 会基于该默认策略返回 `buy` / `sell` / `hold`
```

- [ ] **Step 2: Verify the README update**

Run: `rg -n "Default Freqtrade Strategy|EMA 9/21|ETH/USDC" README.md`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: all three matches appear in the new section.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: describe default ema crossover strategy"
```

### Task 5: End-to-End Verification

**Files:**
- Verify only: strategy, config, tests, README

- [ ] **Step 1: Run all affected Python tests**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server freqtrade.tests.test_simple_agent_strategy && PYTHONPATH=agent python3 -m unittest agent.tests.test_freqtrade_mcp_client agent.tests.test_main_tools`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 2: Rebuild the relevant services**

Run: `docker compose up -d --build agent freqtrade`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: `agent` and `freqtrade` start successfully.

- [ ] **Step 3: Exercise the real default signal path**

Run:

```bash
docker compose exec -T agent python - <<'PY'
import asyncio
import json
from main import evaluate_trade_signal_tool

async def main() -> None:
    result = await evaluate_trade_signal_tool(pair="ETH/USDC")
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY
```

Expected: JSON payload for `ETH/USDC` with `strategy="SimpleAgentStrategy"`, `timeframe="5m"`, and normalized signal fields. The signal may be `buy`, `sell`, or `hold` depending on current runtime evaluation.

- [ ] **Step 4: Commit the final verified state**

```bash
git add freqtrade/strategies/SimpleAgentStrategy.py freqtrade/tests/test_simple_agent_strategy.py freqtrade/config/config.json freqtrade/tests/test_mcp_server.py README.md
git commit -m "feat: add ema crossover default freqtrade strategy"
```

## Self-Review

- Spec coverage:
  - EMA 9/21 strategy logic: Task 1
  - `ETH/USDC` config alignment: Task 2
  - signal-evaluation verification with the real strategy path: Task 3
  - README documentation: Task 4
  - container-level smoke verification: Task 5
- Placeholder scan: no `TODO`, `TBD`, or undefined future work remains.
- Type consistency:
  - strategy columns are consistently `ema_fast`, `ema_slow`, `enter_long`, `exit_long`
  - real signal output still flows through `evaluate_trade_signal` without changing its response contract
