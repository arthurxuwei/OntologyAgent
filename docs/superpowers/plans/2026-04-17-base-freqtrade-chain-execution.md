# Base Freqtrade Chain Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route one Freqtrade-originated trade intent into one Base Sepolia onchain execution path without making Freqtrade hold or settle chain funds.

**Architecture:** Add a narrow `emit_trade_intent` tool on the Freqtrade MCP side, a matching `chain_execute_trade_intent` tool on the chain MCP side, and a thin bridge tool in `agent/main.py` that validates the intent, forwards it to chain execution, and returns a normalized result. Keep V1 constrained to one pair mapping and one execution path so the system proves the boundary split before adding automation or more pairs.

**Tech Stack:** Python (`fastapi`, `pydantic`, MCP client/server), TypeScript (`zod`, MCP server, existing chain services), pytest/unittest, Node test runner

---

## File Map

- Modify: `freqtrade/mcp_server.py`
  Responsibility: expose a new MCP tool that emits normalized trade intent instead of placing a Freqtrade order.
- Create: `freqtrade/tests/test_mcp_server.py`
  Responsibility: cover the new Freqtrade MCP trade-intent tool directly, without depending on agent-side discovery.
- Modify: `agent/main.py`
  Responsibility: define Pydantic models for trade intent bridging, expose the bridge tool, and include new tool discovery aliases.
- Modify: `agent/tests/test_freqtrade_mcp_client.py`
  Responsibility: ensure the new agent-side bridge tool is discoverable when the Freqtrade intent tool is available.
- Modify: `agent/tests/test_main_api.py`
  Responsibility: cover the bridge helper contract and health/tool discovery surfaces that include the new execution route.
- Modify: `chain/src/domain/types.ts`
  Responsibility: add shared chain-side types for trade intent input and normalized execution result.
- Modify: `chain/src/config.ts`
  Responsibility: load one V1 pair mapping and execution endpoint configuration from env with safe defaults.
- Create: `chain/src/services/trade-intent-execution-service.ts`
  Responsibility: validate the supported pair, inspect balances, optionally short-circuit in mock mode, and return a normalized execution payload.
- Modify: `chain/src/mcp/tools.ts`
  Responsibility: register `chain_execute_trade_intent` and wire the new service into the runtime.
- Modify: `chain/test/mcp-server.test.ts`
  Responsibility: verify the new MCP tool is exposed and returns the expected normalized success/failure shapes.
- Modify: `README.md`
  Responsibility: document the new V1 bridge tool, env vars, and the fact that Base wallet funds remain in `chain` rather than being transferred into Freqtrade.

## Implementation Notes

- V1 pair support: hardcode one pair route, `ETH/USDC`, mapped to Base Sepolia WETH and Base Sepolia USDC.
- V1 supported side: `long` only. Reject `short` before chain execution.
- V1 execution mode: in `CHAIN_MOCK=true`, return a normalized mock success payload from the new service. In non-mock mode, return a structured `quote_unavailable` error until a concrete swap integration is chosen.
- This plan deliberately proves the bridge contract and operator-visible result model first. Real DEX quote + swap integration is a follow-up once the team selects the execution source.

### Task 1: Add Freqtrade Trade Intent Emission

**Files:**
- Modify: `freqtrade/mcp_server.py`
- Create: `freqtrade/tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing direct tool test in `freqtrade/tests/test_mcp_server.py`**

```python
import asyncio
import unittest

from freqtrade.mcp_server import emit_trade_intent


class EmitTradeIntentTests(unittest.TestCase):
    def test_emit_trade_intent_returns_normalized_v1_payload(self) -> None:
        payload = asyncio.run(
            emit_trade_intent(
                pair="ETH/USDC",
                side="long",
                stake_amount=1.0,
            )
        )

        self.assertEqual(payload["intent"]["pair"], "ETH/USDC")
        self.assertEqual(payload["intent"]["amountType"], "quote")
        self.assertEqual(payload["intent"]["side"], "long")
```

- [ ] **Step 2: Run the direct tool test to verify it fails**

Run: `pytest freqtrade/tests/test_mcp_server.py::EmitTradeIntentTests::test_emit_trade_intent_returns_normalized_v1_payload -v`
Expected: FAIL because `emit_trade_intent` does not exist yet.

- [ ] **Step 3: Add the new MCP tool in `freqtrade/mcp_server.py`**

```python
@mcp.tool()
async def emit_trade_intent(
    pair: str,
    side: Literal["long", "short"] = "long",
    stake_amount: float = 0,
    order_type: Literal["market", "limit"] = "market",
    price: float | None = None,
    strategy: str | None = None,
    max_slippage_bps: int = 100,
    reason: str = "agent_requested_trade",
) -> dict[str, Any]:
    if side != "long":
        raise FreqtradeRestError("emit_trade_intent only supports long side in V1")
    if stake_amount <= 0:
        raise FreqtradeRestError("stake_amount must be greater than 0")

    return {
        "summary": f"Trade intent prepared for {pair}",
        "intent": {
            "intentId": f"intent-{pair.replace('/', '-').lower()}-{int(stake_amount * 1000)}",
            "strategy": strategy or FREQTRADE_STRATEGY_NAME,
            "pair": pair,
            "side": side,
            "amount": stake_amount,
            "amountType": "quote",
            "orderType": order_type,
            "limitPrice": price,
            "maxSlippageBps": max_slippage_bps,
            "reason": reason,
        },
    }
```

Also add the missing constant near the top of the file:

```python
FREQTRADE_STRATEGY_NAME = os.getenv("FREQTRADE_STRATEGY_NAME", "SimpleAgentStrategy")
```

- [ ] **Step 4: Run the direct tool test again**

Run: `pytest freqtrade/tests/test_mcp_server.py::EmitTradeIntentTests::test_emit_trade_intent_returns_normalized_v1_payload -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freqtrade/tests/test_mcp_server.py freqtrade/mcp_server.py
git commit -m "feat: add freqtrade trade intent tool"
```

### Task 2: Add Chain Trade Intent Types and Mock Execution Service

**Files:**
- Modify: `chain/src/domain/types.ts`
- Modify: `chain/src/config.ts`
- Create: `chain/src/services/trade-intent-execution-service.ts`
- Test: `chain/test/mcp-server.test.ts`

- [ ] **Step 1: Write the failing chain MCP tests**

Add these tests to `chain/test/mcp-server.test.ts`:

```ts
test("chain MCP exposes chain_execute_trade_intent", async () => {
  await withClient(async (client) => {
    const response = await client.listTools();
    const toolNames = response.tools.map((tool) => tool.name).sort();
    assert.equal(toolNames.includes("chain_execute_trade_intent"), true);
  });
});

test("chain_execute_trade_intent returns normalized mock execution for the supported pair", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_execute_trade_intent",
      arguments: {
        intentId: "intent-eth-usdc-1",
        pair: "ETH/USDC",
        side: "long",
        amount: "1.0",
        amountType: "quote",
        maxSlippageBps: 100,
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.intentId, "intent-eth-usdc-1");
    assert.equal(content.status, "submitted");
    assert.equal(content.pair, "ETH/USDC");
    assert.equal(content.mode, "mock");
  });
});
```

- [ ] **Step 2: Run the chain tests to verify they fail**

Run: `npm test -- --test-name-pattern="chain_execute_trade_intent|chain MCP exposes chain_execute_trade_intent"`
Workdir: `/Users/freedom/cc/OntologyAgent/chain`
Expected: FAIL because the tool is not registered.

- [ ] **Step 3: Add the new chain-side types in `chain/src/domain/types.ts`**

```ts
export type TradeIntentCommand = {
  intentId: string;
  pair: string;
  side: "long" | "short";
  amount: string;
  amountType: "quote" | "base";
  maxSlippageBps: number;
  strategy?: string;
  orderType?: "market" | "limit";
  limitPrice?: string | null;
  reason?: string;
};

export type TradeIntentExecutionResult = {
  intentId: string;
  pair: string;
  side: "long" | "short";
  status: "submitted" | "rejected";
  mode: "mock" | "network";
  sellToken: string;
  buyToken: string;
  sellAmount: string;
  buyAmount: string;
  txHash: string | null;
  failureStage: string | null;
  failureReason: string | null;
};
```

- [ ] **Step 4: Extend chain config in `chain/src/config.ts`**

Add a narrow execution section:

```ts
  execution: {
    bundlerRpcUrl?: string;
    tradeIntentPair: string;
    tradeIntentSellToken: string;
    tradeIntentBuyToken: string;
  };
```

And populate it in `loadConfig()`:

```ts
    execution: {
      bundlerRpcUrl: env.BUNDLER_RPC_URL,
      tradeIntentPair: env.TRADE_INTENT_PAIR ?? "ETH/USDC",
      tradeIntentSellToken:
        env.TRADE_INTENT_SELL_TOKEN ?? (env.X402_USDC_ASSET_ADDRESS ?? DEFAULT_BASE_SEPOLIA_USDC),
      tradeIntentBuyToken:
        env.TRADE_INTENT_BUY_TOKEN ?? "0x4200000000000000000000000000000000000006",
    },
```

- [ ] **Step 5: Create `chain/src/services/trade-intent-execution-service.ts` with the minimal V1 behavior**

```ts
import type { AppConfig } from "../config.js";
import type { TradeIntentCommand, TradeIntentExecutionResult } from "../domain/types.js";

export class TradeIntentExecutionService {
  constructor(private readonly config: AppConfig) {}

  async execute(command: TradeIntentCommand): Promise<TradeIntentExecutionResult> {
    if (command.pair !== this.config.execution.tradeIntentPair) {
      return this.reject(command, "intent", `unsupported pair: ${command.pair}`);
    }
    if (command.side !== "long") {
      return this.reject(command, "intent", "only long side is supported in V1");
    }

    if (this.config.network.mockChain) {
      return {
        intentId: command.intentId,
        pair: command.pair,
        side: command.side,
        status: "submitted",
        mode: "mock",
        sellToken: this.config.execution.tradeIntentSellToken,
        buyToken: this.config.execution.tradeIntentBuyToken,
        sellAmount: command.amount,
        buyAmount: "0.0005",
        txHash: `0xmock_trade_${command.intentId}`,
        failureStage: null,
        failureReason: null,
      };
    }

    return this.reject(command, "quote", "network execution source is not configured yet");
  }

  private reject(
    command: TradeIntentCommand,
    failureStage: string,
    failureReason: string,
  ): TradeIntentExecutionResult {
    return {
      intentId: command.intentId,
      pair: command.pair,
      side: command.side,
      status: "rejected",
      mode: this.config.network.mockChain ? "mock" : "network",
      sellToken: this.config.execution.tradeIntentSellToken,
      buyToken: this.config.execution.tradeIntentBuyToken,
      sellAmount: command.amount,
      buyAmount: "0",
      txHash: null,
      failureStage,
      failureReason,
    };
  }
}
```

- [ ] **Step 6: Run the focused chain tests again**

Run: `npm test -- --test-name-pattern="chain_execute_trade_intent|chain MCP exposes chain_execute_trade_intent"`
Workdir: `/Users/freedom/cc/OntologyAgent/chain`
Expected: still FAIL because the MCP tool is not wired into the runtime yet.

- [ ] **Step 7: Commit**

```bash
git add chain/src/domain/types.ts chain/src/config.ts chain/src/services/trade-intent-execution-service.ts chain/test/mcp-server.test.ts
git commit -m "feat: add chain trade intent execution primitives"
```

### Task 3: Register `chain_execute_trade_intent` in the Chain MCP Server

**Files:**
- Modify: `chain/src/mcp/tools.ts`
- Test: `chain/test/mcp-server.test.ts`

- [ ] **Step 1: Update the runtime shape in `chain/src/mcp/tools.ts`**

Add the import and runtime field:

```ts
import { TradeIntentExecutionService } from "../services/trade-intent-execution-service.js";

type ChainRuntime = {
  walletStateService: WalletStateService;
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  tradeIntentExecutionService: TradeIntentExecutionService;
  transactionReceiptService: TransactionReceiptService;
  userOperationStatusService: UserOperationStatusService;
  userOperationService: UserOperationService;
  x402FetchService: X402FetchService;
};
```

- [ ] **Step 2: Instantiate the service in `createChainRuntime()`**

```ts
  return {
    walletStateService: new WalletStateService(config, policyGuard, networkClient, signer),
    signTransferService: new SignTransferService(sender, policyGuard, settlementService),
    executionService: new ExecutionService(sender, policyGuard, settlementService),
    tradeIntentExecutionService: new TradeIntentExecutionService(config),
    transactionReceiptService:
      overrides?.transactionReceiptService ?? new TransactionReceiptService(config, networkClient),
```

- [ ] **Step 3: Register the MCP tool in `createChainMcpServer()`**

```ts
  server.registerTool(
    "chain_execute_trade_intent",
    {
      description: "Validate and execute one supported trade intent using Base wallet funds.",
      inputSchema: {
        intentId: z.string().describe("Stable trade intent identifier"),
        pair: z.string().describe("Strategy pair, for example ETH/USDC"),
        side: z.enum(["long", "short"]).default("long"),
        amount: z.string().describe("Amount as a decimal string"),
        amountType: z.enum(["quote", "base"]).default("quote"),
        maxSlippageBps: z.number().int().nonnegative().default(100),
        strategy: z.string().optional(),
        orderType: z.enum(["market", "limit"]).default("market"),
        limitPrice: z.string().optional(),
        reason: z.string().optional(),
      },
    },
    async (args) => runTool(() => runtime.tradeIntentExecutionService.execute(args)),
  );
```

- [ ] **Step 4: Run the focused chain tests**

Run: `npm test -- --test-name-pattern="chain_execute_trade_intent|chain MCP exposes chain_execute_trade_intent"`
Workdir: `/Users/freedom/cc/OntologyAgent/chain`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chain/src/mcp/tools.ts chain/test/mcp-server.test.ts
git commit -m "feat: expose chain trade intent execution tool"
```

### Task 4: Add the Agent Bridge Tool

**Files:**
- Modify: `agent/main.py`
- Test: `agent/tests/test_freqtrade_mcp_client.py`
- Test: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add a failing agent test for bridge invocation**

Append to `agent/tests/test_main_api.py`:

```python
    def test_execute_freqtrade_trade_intent_routes_freqtrade_intent_to_chain_execution(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class FakeFreqtradeClient:
            async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
                calls.append((tool_name, arguments))
                return {
                    "intent": {
                        "intentId": "intent-eth-usdc-1000",
                        "pair": "ETH/USDC",
                        "side": "long",
                        "amount": 1.0,
                        "amountType": "quote",
                        "maxSlippageBps": 100,
                    }
                }

        class FakeChainClient:
            async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
                calls.append((tool_name, arguments))
                return {
                    "intentId": arguments["intentId"],
                    "status": "submitted",
                    "txHash": "0xmock_trade_intent",
                }

        with (
            patch.object(main, "get_freqtrade_mcp_client", return_value=FakeFreqtradeClient()),
            patch.object(main, "get_chain_mcp_client", return_value=FakeChainClient()),
        ):
            result = asyncio.run(
                main.execute_freqtrade_trade_intent_tool(
                    pair="ETH/USDC",
                    stakeAmount=1.0,
                    side="long",
                )
            )

        self.assertEqual(result["result"]["status"], "submitted")
        self.assertEqual(calls[0][0], "emit_trade_intent")
        self.assertEqual(calls[1][0], "chain_execute_trade_intent")
```

- [ ] **Step 2: Run the new agent test to verify it fails**

Run: `pytest agent/tests/test_main_api.py::MainApiTests::test_execute_freqtrade_trade_intent_routes_freqtrade_intent_to_chain_execution -v`
Expected: FAIL because `execute_freqtrade_trade_intent_tool` does not exist.

- [ ] **Step 3: Add the bridge models and helper in `agent/main.py`**

Add these Pydantic models near the existing Freqtrade intent models:

```python
class ExecuteFreqtradeTradeIntentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pair: str = Field(description="交易对，例如 ETH/USDC")
    side: Literal["long", "short"] = Field(default="long")
    stakeAmount: float = Field(gt=0, description="策略侧输入金额")
    maxSlippageBps: int = Field(default=100, ge=0, le=10_000)
    orderType: Literal["market", "limit"] = Field(default="market")
    price: Optional[float] = Field(default=None)
    reason: str = Field(default="agent_requested_trade")
```

Add the bridge coroutine near the other Freqtrade helpers:

```python
async def execute_freqtrade_trade_intent_tool(
    pair: str,
    stakeAmount: float,
    side: str = "long",
    maxSlippageBps: int = 100,
    orderType: str = "market",
    price: Optional[float] = None,
    reason: str = "agent_requested_trade",
) -> dict[str, Any]:
    intent = ExecuteFreqtradeTradeIntentIntent(
        pair=pair,
        side=side,
        stakeAmount=stakeAmount,
        maxSlippageBps=maxSlippageBps,
        orderType=orderType,
        price=price,
        reason=reason,
    )
    freqtrade_payload = await get_freqtrade_mcp_client().call_tool(
        "emit_trade_intent",
        {
            "pair": intent.pair,
            "side": intent.side,
            "stake_amount": intent.stakeAmount,
            "max_slippage_bps": intent.maxSlippageBps,
            "order_type": intent.orderType,
            "price": intent.price,
            "reason": intent.reason,
        },
    )
    trade_intent = _unwrap_mcp_result("emit_trade_intent", freqtrade_payload).get("intent", {})
    result = await call_chain_tool(
        "chain_execute_trade_intent",
        {
            "intentId": trade_intent["intentId"],
            "pair": trade_intent["pair"],
            "side": trade_intent["side"],
            "amount": str(trade_intent["amount"]),
            "amountType": trade_intent["amountType"],
            "maxSlippageBps": trade_intent["maxSlippageBps"],
            "strategy": trade_intent.get("strategy"),
            "orderType": trade_intent.get("orderType", "market"),
            "limitPrice": (
                str(trade_intent["limitPrice"])
                if trade_intent.get("limitPrice") is not None
                else None
            ),
            "reason": trade_intent.get("reason"),
        },
    )
    return {
        "tool": "execute_freqtrade_trade_intent",
        "tradeIntent": trade_intent,
        "result": result["result"],
    }
```

- [ ] **Step 4: Register the new tool in `FREQTRADE_TOOL_REGISTRY` and discovery**

Add this entry:

```python
    "execute_freqtrade_trade_intent": {
        "description": "让 Freqtrade 生成交易意图，再由 chain 使用 Base 钱包执行。",
        "args_schema": ExecuteFreqtradeTradeIntentIntent,
        "coroutine": execute_freqtrade_trade_intent_tool,
    },
```

And teach discovery to expose it when `emit_trade_intent` is available from Freqtrade MCP:

```python
    if "emit_trade_intent" in available_tools:
        discovered_tools.append(build_freqtrade_tool("execute_freqtrade_trade_intent"))
```

- [ ] **Step 5: Extend the discovery test fixture in `agent/tests/test_freqtrade_mcp_client.py`**

Update the fake tool list and assertion:

```python
            "emit_trade_intent",
```

Expected tool list excerpt:

```python
                "execute_freqtrade_trade_intent",
```

- [ ] **Step 6: Run the focused Python tests**

Run: `pytest agent/tests/test_freqtrade_mcp_client.py agent/tests/test_main_api.py -k "trade_intent or execute_freqtrade_trade_intent" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/main.py agent/tests/test_freqtrade_mcp_client.py agent/tests/test_main_api.py
git commit -m "feat: bridge freqtrade trade intents to chain execution"
```

### Task 5: Document the V1 Bridge and Operator Constraints

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the bridge tool to the README tool list**

Insert into the Freqtrade/agent capability section:

```md
- `execute_freqtrade_trade_intent`（让 `freqtrade` 生成交易意图，再由 `chain` 使用 Base 钱包执行）
```

- [ ] **Step 2: Add the new chain env vars to the README**

Insert under `### chain`:

```md
- `TRADE_INTENT_PAIR`：V1 支持的唯一策略交易对，默认 `ETH/USDC`
- `TRADE_INTENT_SELL_TOKEN`：V1 卖出 token 地址，默认 Base Sepolia USDC
- `TRADE_INTENT_BUY_TOKEN`：V1 买入 token 地址，默认 Base Sepolia WETH
```

- [ ] **Step 3: Add a short operator note clarifying custody and scope**

```md
### Base 链上交易意图桥接（V1）

- Base 钱包资金仍由 `chain` 持有和执行，**不会**转入 `freqtrade`
- `freqtrade` 只负责生成交易意图
- `chain_execute_trade_intent` 在 `CHAIN_MOCK=true` 时返回 mock 成交结果
- 非 mock 模式下，若未接入具体 DEX / 聚合器，工具会返回结构化拒绝结果而不是伪装成成交
```

- [ ] **Step 4: Verify the README changes**

Run: `grep -n "execute_freqtrade_trade_intent\|TRADE_INTENT_PAIR\|Base 链上交易意图桥接" README.md`
Expected: all three matches appear once in the updated sections.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document base trade intent bridge"
```

### Task 6: End-to-End Verification

**Files:**
- Modify if needed: `docker-compose.yml`
- Verify: `freqtrade/mcp_server.py`, `agent/main.py`, `chain/src/mcp/tools.ts`

- [ ] **Step 1: Run the complete Python test suite for affected agent and freqtrade modules**

Run: `pytest agent/tests/test_freqtrade_mcp_client.py agent/tests/test_main_api.py -v`
Expected: PASS.

- [ ] **Step 2: Run the complete chain test suite**

Run: `npm test`
Workdir: `/Users/freedom/cc/OntologyAgent/chain`
Expected: PASS.

- [ ] **Step 3: Run chain typecheck**

Run: `npm run typecheck`
Workdir: `/Users/freedom/cc/OntologyAgent/chain`
Expected: PASS.

- [ ] **Step 4: Bring up the stack and inspect health**

Run: `docker compose up -d --build`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: `agent`, `chain`, and `freqtrade` containers start successfully.

- [ ] **Step 5: Exercise the new bridge tool through the agent container**

Run:

```bash
docker compose exec -T agent python - <<'PY'
import asyncio
import json
from main import execute_freqtrade_trade_intent_tool

async def main() -> None:
    result = await execute_freqtrade_trade_intent_tool(
        pair="ETH/USDC",
        stakeAmount=1.0,
        side="long",
    )
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY
```

Expected in mock mode: JSON with `tool="execute_freqtrade_trade_intent"`, `result.status="submitted"`, and a mock `txHash`.

- [ ] **Step 6: Commit the final integrated change**

```bash
git add freqtrade/mcp_server.py agent/main.py agent/tests/test_freqtrade_mcp_client.py agent/tests/test_main_api.py chain/src/domain/types.ts chain/src/config.ts chain/src/services/trade-intent-execution-service.ts chain/src/mcp/tools.ts chain/test/mcp-server.test.ts README.md
git commit -m "feat: bridge freqtrade intents to base chain execution"
```

## Self-Review

- Spec coverage:
  - trade intent schema: Task 2 and Task 4
  - pair mapping and one supported route: Task 2
  - Freqtrade emits intent, not trade: Task 1
  - chain executes the normalized intent: Task 2 and Task 3
  - bridge orchestration in agent: Task 4
  - testing and operator documentation: Task 5 and Task 6
- Placeholder scan: no `TODO`, `TBD`, or undefined “later” tasks remain.
- Type consistency:
  - Freqtrade emits `intentId`, `pair`, `side`, `amount`, `amountType`, `maxSlippageBps`
  - agent bridge forwards the same names to `chain_execute_trade_intent`
  - chain service returns `status`, `txHash`, `failureStage`, `failureReason`
