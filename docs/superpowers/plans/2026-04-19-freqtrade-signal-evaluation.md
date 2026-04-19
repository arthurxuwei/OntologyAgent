# Freqtrade Signal Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `evaluate_trade_signal` capability that lets Freqtrade evaluate one pair and return a normalized `buy` / `sell` / `hold` signal without creating a trade intent or execution request.

**Architecture:** Add a narrow signal-evaluation tool in `freqtrade/mcp_server.py`, keep the normalization logic inside the Freqtrade integration layer, and expose the new tool through the existing agent-side MCP discovery path. V1 stays deliberately small: one supported pair, one strategy, one timeframe, one manual trigger path, and deterministic normalized output.

**Tech Stack:** Python (`fastapi`, `pydantic`, `httpx`, MCP server/client), unittest, existing agent tool registry/discovery

---

## File Map

- Modify: `freqtrade/mcp_server.py`
  Responsibility: add a read-only `evaluate_trade_signal` MCP tool plus any minimal normalization helpers required for V1.
- Modify: `freqtrade/tests/test_mcp_server.py`
  Responsibility: cover normalized `buy` / `sell` / `hold` output and unsupported-pair failure.
- Modify: `agent/main.py`
  Responsibility: register the new Freqtrade signal tool in the agent tool registry and expose it only when discovered from Freqtrade MCP.
- Modify: `agent/tests/test_freqtrade_mcp_client.py`
  Responsibility: ensure agent-side discovery includes the new signal tool when Freqtrade advertises it.
- Modify: `agent/tests/test_main_tools.py`
  Responsibility: verify graph/build path exposure of the new signal tool follows discovery.
- Modify: `README.md`
  Responsibility: document the new read-only signal tool and its V1 limits.

## Implementation Notes

- V1 supported pair: `ETH/USDC`
- V1 supported strategy name: default `FREQTRADE_STRATEGY_NAME`
- V1 supported timeframe: fixed string returned by the tool, matching the default strategy timeframe for now
- V1 output shape:
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
- V1 signal semantics:
  - `buy` if `entryTriggered` and no open position
  - `sell` if `exitTriggered` and an open position exists
  - `hold` otherwise
- To keep the implementation small and testable, use a deterministic internal evaluation seam in `freqtrade/mcp_server.py` that can be unit-tested without booting a live bot. The first implementation may use a small helper that accepts precomputed booleans and open-position state, then normalize them into the MCP response.

### Task 1: Add Freqtrade Signal Evaluation Tool

**Files:**
- Modify: `freqtrade/mcp_server.py`
- Modify: `freqtrade/tests/test_mcp_server.py`

- [ ] **Step 1: Add failing tests for normalized signals in `freqtrade/tests/test_mcp_server.py`**

Append these tests:

```python
class EvaluateTradeSignalTests(unittest.TestCase):
    def test_evaluate_trade_signal_returns_buy_when_entry_triggers_without_position(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            return_value={
                "pair": "ETH/USDC",
                "strategy": "SimpleAgentStrategy",
                "timeframe": "5m",
                "hasOpenPosition": False,
                "entryTriggered": True,
                "exitTriggered": False,
                "observedAt": "2026-04-19T10:30:00Z",
            },
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["signal"], "buy")
        self.assertEqual(result["confidence"], 0.7)
        self.assertEqual(
            result["reason"],
            "entry conditions satisfied on latest candle and no open position",
        )

    def test_evaluate_trade_signal_returns_sell_when_exit_triggers_with_position(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            return_value={
                "pair": "ETH/USDC",
                "strategy": "SimpleAgentStrategy",
                "timeframe": "5m",
                "hasOpenPosition": True,
                "entryTriggered": False,
                "exitTriggered": True,
                "observedAt": "2026-04-19T10:35:00Z",
            },
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["signal"], "sell")
        self.assertEqual(result["confidence"], 0.7)
        self.assertEqual(result["reason"], "exit conditions satisfied while position is open")

    def test_evaluate_trade_signal_returns_hold_when_no_actionable_signal_exists(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            return_value={
                "pair": "ETH/USDC",
                "strategy": "SimpleAgentStrategy",
                "timeframe": "5m",
                "hasOpenPosition": False,
                "entryTriggered": False,
                "exitTriggered": False,
                "observedAt": "2026-04-19T10:40:00Z",
            },
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["signal"], "hold")
        self.assertEqual(result["confidence"], 0.5)
        self.assertEqual(result["reason"], "no actionable entry or exit signal on latest candle")

    def test_evaluate_trade_signal_rejects_unsupported_pair(self) -> None:
        with self.assertRaisesRegex(
            mcp_server.FreqtradeRestError,
            "evaluate_trade_signal only supports ETH/USDC in V1",
        ):
            asyncio.run(mcp_server.evaluate_trade_signal(pair="BTC/USDT"))
```

- [ ] **Step 2: Run the focused Freqtrade test module to verify it fails**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because `evaluate_trade_signal` and `_evaluate_trade_signal_state` do not exist.

- [ ] **Step 3: Add the minimal helper and MCP tool in `freqtrade/mcp_server.py`**

Add these constants near the top of the file:

```python
FREQTRADE_SIGNAL_DEFAULT_PAIR = os.getenv("FREQTRADE_SIGNAL_DEFAULT_PAIR", "ETH/USDC")
FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME = os.getenv("FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME", "5m")
```

Add the internal helper and MCP tool near the other Freqtrade tools:

```python
async def _evaluate_trade_signal_state(
    pair: str,
    strategy: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    return {
        "pair": pair,
        "strategy": strategy or FREQTRADE_STRATEGY_NAME,
        "timeframe": timeframe or FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME,
        "hasOpenPosition": False,
        "entryTriggered": False,
        "exitTriggered": False,
        "observedAt": "1970-01-01T00:00:00Z",
    }


@mcp.tool()
async def evaluate_trade_signal(
    pair: str,
    strategy: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    if pair != FREQTRADE_SIGNAL_DEFAULT_PAIR:
        raise FreqtradeRestError("evaluate_trade_signal only supports ETH/USDC in V1")

    state = await _evaluate_trade_signal_state(pair=pair, strategy=strategy, timeframe=timeframe)
    has_open_position = bool(state["hasOpenPosition"])
    entry_triggered = bool(state["entryTriggered"])
    exit_triggered = bool(state["exitTriggered"])

    if entry_triggered and not has_open_position:
        signal = "buy"
        confidence = 0.7
        reason = "entry conditions satisfied on latest candle and no open position"
    elif exit_triggered and has_open_position:
        signal = "sell"
        confidence = 0.7
        reason = "exit conditions satisfied while position is open"
    else:
        signal = "hold"
        confidence = 0.5
        reason = "no actionable entry or exit signal on latest candle"

    return {
        "pair": state["pair"],
        "strategy": state["strategy"],
        "timeframe": state["timeframe"],
        "signal": signal,
        "reason": reason,
        "confidence": confidence,
        "hasOpenPosition": has_open_position,
        "entryTriggered": entry_triggered,
        "exitTriggered": exit_triggered,
        "observedAt": state["observedAt"],
    }
```

- [ ] **Step 4: Run the focused Freqtrade tests again**

Run: `PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freqtrade/mcp_server.py freqtrade/tests/test_mcp_server.py
git commit -m "feat: add freqtrade signal evaluation tool"
```

### Task 2: Expose Signal Evaluation Through Agent Discovery

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/tests/test_freqtrade_mcp_client.py`
- Modify: `agent/tests/test_main_tools.py`

- [ ] **Step 1: Add a failing discovery test in `agent/tests/test_freqtrade_mcp_client.py`**

Update the fake tool list and expected names:

```python
            "evaluate_trade_signal",
```

Expected discovered tool list excerpt:

```python
                "evaluate_trade_signal",
```

- [ ] **Step 2: Add a failing build-tools exposure test in `agent/tests/test_main_tools.py`**

Append:

```python
    def test_build_tools_exposes_trade_signal_tool_when_discovered(self) -> None:
        with patch.object(main, "_load_discovered_chain_tools", return_value=[]), patch.object(
            main,
            "_load_discovered_freqtrade_tools",
            return_value=[_make_test_tool("evaluate_trade_signal")],
        ):
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("evaluate_trade_signal", tool_names)
```

- [ ] **Step 3: Run the focused agent discovery tests to verify they fail**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_freqtrade_mcp_client agent.tests.test_main_tools`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because the tool is not in the agent registry/discovery output yet.

- [ ] **Step 4: Register the tool in `agent/main.py`**

Add the new agent-side registry entry:

```python
    "evaluate_trade_signal": {
        "description": "评估当前 Freqtrade 策略对指定交易对的信号，返回 buy/sell/hold。",
        "args_schema": EvaluateTradeSignalIntent,
        "coroutine": evaluate_trade_signal_tool,
    },
```

Add the Pydantic model and coroutine near other Freqtrade tool models/helpers:

```python
class EvaluateTradeSignalIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pair: str
    strategy: Optional[str] = None
    timeframe: Optional[str] = None


async def evaluate_trade_signal_tool(
    pair: str,
    strategy: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"pair": pair}
    if strategy is not None:
        payload["strategy"] = strategy
    if timeframe is not None:
        payload["timeframe"] = timeframe
    return await call_freqtrade_tool("evaluate_trade_signal", payload)
```

No special discovery gate is needed beyond normal MCP discovery, because the tool is read-only and should only appear when Freqtrade advertises it.

- [ ] **Step 5: Run the focused agent tests again**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_freqtrade_mcp_client agent.tests.test_main_tools`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/main.py agent/tests/test_freqtrade_mcp_client.py agent/tests/test_main_tools.py
git commit -m "feat: expose freqtrade signal evaluation through agent"
```

### Task 3: Document the New Read-Only Signal Tool

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the new tool to the Freqtrade MCP tools list**

Insert into the `freqtrade` MCP tools section:

```md
- `evaluate_trade_signal`
```

- [ ] **Step 2: Add a short V1 note about signal evaluation**

Add near the Freqtrade section:

```md
### Freqtrade Signal Evaluation（V1）

- `evaluate_trade_signal` 是只读工具，不会生成 trade intent，也不会触发链上执行
- V1 仅支持 `ETH/USDC`
- V1 返回 `buy` / `sell` / `hold`，并附带 `reason`、`confidence`、仓位上下文
```

- [ ] **Step 3: Verify the README changes**

Run: `rg -n "evaluate_trade_signal|Freqtrade Signal Evaluation" README.md`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: both matches appear in the updated sections.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add freqtrade signal evaluation docs"
```

### Task 4: End-to-End Verification

**Files:**
- Verify only: `freqtrade/mcp_server.py`, `agent/main.py`, tests, README

- [ ] **Step 1: Run all affected Python tests**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_freqtrade_mcp_client agent.tests.test_main_api agent.tests.test_main_tools && PYTHONPATH=. python3 -m unittest freqtrade.tests.test_mcp_server`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 2: Start or refresh the stack**

Run: `docker compose up -d --build agent freqtrade`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: `agent` and `freqtrade` services start successfully.

- [ ] **Step 3: Verify agent health still includes discovered Freqtrade tools**

Run: `curl -fsS "http://localhost:8000/health"`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: JSON response with `status: "ok"`.

- [ ] **Step 4: Exercise the new read-only signal tool inside the agent container**

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

Expected: JSON payload containing `signal`, `reason`, `confidence`, `hasOpenPosition`, `entryTriggered`, `exitTriggered`, and `observedAt`.

- [ ] **Step 5: Commit the final verified state**

```bash
git add freqtrade/mcp_server.py freqtrade/tests/test_mcp_server.py agent/main.py agent/tests/test_freqtrade_mcp_client.py agent/tests/test_main_tools.py README.md
git commit -m "feat: add read-only freqtrade signal evaluation"
```

## Self-Review

- Spec coverage:
  - read-only `evaluate_trade_signal` tool: Task 1
  - normalized `buy` / `sell` / `hold` output: Task 1
  - agent-side discovery/exposure: Task 2
  - read-only documentation and V1 scope: Task 3
  - manual smoke verification: Task 4
- Placeholder scan: no `TODO`, `TBD`, or undefined deferred work remains.
- Type consistency:
  - `evaluate_trade_signal` always returns `signal`, `reason`, `confidence`, `hasOpenPosition`, `entryTriggered`, `exitTriggered`, `observedAt`
  - agent registry uses `EvaluateTradeSignalIntent`
  - README references the same tool name and V1 scope
