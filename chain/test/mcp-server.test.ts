import test from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";

import { loadConfig } from "../src/config.js";
import { createChainMcpServer, createChainRuntime } from "../src/mcp/tools.js";
import { TradeIntentExecutionService } from "../src/services/trade-intent-execution-service.js";
import type { X402FetchService } from "../src/services/x402-fetch-service.js";

type RuntimeOverrides = NonNullable<Parameters<typeof createChainRuntime>[1]>;

function createMockConfig() {
  return loadConfig({
    CHAIN_MOCK: "true",
    CHAIN_MOCK_USDC_BALANCE: "321.123456",
    PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
    WHITELISTED_RECIPIENTS:
      "0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333",
  });
}

async function withClient(
  callback: (client: Client) => Promise<void>,
  overrides?: RuntimeOverrides,
) {
  const runtime = createChainRuntime(createMockConfig(), overrides);
  const client = new Client({
    name: "chain-mcp-test-client",
    version: "1.0.0",
  });
  const transport = new StreamableHTTPClientTransport(new URL("http://chain-mcp:8091/mcp"), {
    fetch: async (input, init) => {
      const server = createChainMcpServer(runtime);
      const request = new Request(input, init);
      const webTransport = new WebStandardStreamableHTTPServerTransport({
        sessionIdGenerator: undefined,
        enableJsonResponse: true,
      });

      await server.connect(webTransport);
      const response = await webTransport.handleRequest(request);
      await webTransport.close();
      await server.close();
      return response;
    },
  });

  await client.connect(transport);

  try {
    await callback(client);
  } finally {
    await client.close();
  }
}

test("chain MCP exposes the expected tool names", async () => {
  await withClient(async (client) => {
    const response = await client.listTools();
    const toolNames = response.tools.map((tool) => tool.name).sort();
    assert.deepEqual(toolNames, [
      "chain_execute_trade_intent",
      "chain_get_transaction_receipt",
      "chain_get_user_operation_status",
      "chain_get_wallet_state",
      "chain_sign_transfer",
      "chain_submit_execution",
      "chain_submit_user_operation",
      "chain_x402_fetch",
    ]);
  });
});

test("chain runtime exposes trade intent execution results in mock mode for the supported pair", async () => {
  const tradeIntentExecutionService = new TradeIntentExecutionService(createMockConfig());
  const result = await tradeIntentExecutionService.execute({
    intentId: "intent-123",
    pair: "ETH/USDC",
    side: "long",
    amount: "10",
    amountType: "quote",
    maxSlippageBps: 50,
    reason: "test mock buy",
  });

  assert.deepEqual(result, {
    intentId: "intent-123",
    pair: "ETH/USDC",
    side: "long",
    status: "submitted",
    mode: "mock",
    sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    buyToken: "0x4200000000000000000000000000000000000006",
    sellAmount: "10",
    buyAmount: "0.0005",
    txHash: "0xmock_trade_intent-123",
    failureStage: null,
    failureReason: null,
  });
});

test("chain runtime returns structured trade intent rejection in network mode", async () => {
  const tradeIntentExecutionService = new TradeIntentExecutionService(
    loadConfig({
      PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
    }),
  );
  const result = await tradeIntentExecutionService.execute({
    intentId: "intent-network",
    pair: "ETH/USDC",
    side: "long",
    amount: "10",
    amountType: "quote",
    maxSlippageBps: 50,
  });

  assert.deepEqual(result, {
    intentId: "intent-network",
    pair: "ETH/USDC",
    side: "long",
    status: "rejected",
    mode: "network",
    sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    buyToken: "0x4200000000000000000000000000000000000006",
    sellAmount: "10",
    buyAmount: null,
    txHash: null,
    failureStage: "quote",
    failureReason: "network execution source is not configured yet",
  });
});

test("chain runtime rejects trade intents for unsupported pairs", async () => {
  const tradeIntentExecutionService = new TradeIntentExecutionService(createMockConfig());
  const result = await tradeIntentExecutionService.execute({
    intentId: "intent-pair",
    pair: "BTC/USDC",
    side: "long",
    amount: "10",
    amountType: "quote",
    maxSlippageBps: 50,
  });

  assert.deepEqual(result, {
    intentId: "intent-pair",
    pair: "BTC/USDC",
    side: "long",
    status: "rejected",
    mode: "mock",
    sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    buyToken: "0x4200000000000000000000000000000000000006",
    sellAmount: "10",
    buyAmount: null,
    txHash: null,
    failureStage: "intent",
    failureReason: "unsupported pair: BTC/USDC",
  });
});

test("chain runtime rejects trade intents for unsupported sides", async () => {
  const tradeIntentExecutionService = new TradeIntentExecutionService(createMockConfig());
  const result = await tradeIntentExecutionService.execute({
    intentId: "intent-side",
    pair: "ETH/USDC",
    side: "short",
    amount: "10",
    amountType: "quote",
    maxSlippageBps: 50,
  });

  assert.deepEqual(result, {
    intentId: "intent-side",
    pair: "ETH/USDC",
    side: "short",
    status: "rejected",
    mode: "mock",
    sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    buyToken: "0x4200000000000000000000000000000000000006",
    sellAmount: "10",
    buyAmount: null,
    txHash: null,
    failureStage: "intent",
    failureReason: "unsupported side: short",
  });
});

test("chain runtime rejects trade intents for unsupported amount types", async () => {
  const tradeIntentExecutionService = new TradeIntentExecutionService(createMockConfig());
  const result = await tradeIntentExecutionService.execute({
    intentId: "intent-amount-type",
    pair: "ETH/USDC",
    side: "long",
    amount: "10",
    amountType: "base",
    maxSlippageBps: 50,
  });

  assert.deepEqual(result, {
    intentId: "intent-amount-type",
    pair: "ETH/USDC",
    side: "long",
    status: "rejected",
    mode: "mock",
    sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    buyToken: "0x4200000000000000000000000000000000000006",
    sellAmount: "10",
    buyAmount: null,
    txHash: null,
    failureStage: "intent",
    failureReason: "unsupported amountType: base",
  });
});

test("chain_execute_trade_intent returns submitted trade intent result through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_execute_trade_intent",
      arguments: {
        intentId: "intent-123",
        pair: "ETH/USDC",
        side: "long",
        amount: "10",
        amountType: "quote",
        maxSlippageBps: 50,
        reason: "test mock buy",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.deepEqual(content, {
      intentId: "intent-123",
      pair: "ETH/USDC",
      side: "long",
      status: "submitted",
      mode: "mock",
      sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
      buyToken: "0x4200000000000000000000000000000000000006",
      sellAmount: "10",
      buyAmount: "0.0005",
      txHash: "0xmock_trade_intent-123",
      failureStage: null,
      failureReason: null,
    });
  });
});

test("chain_execute_trade_intent rejects limit orders through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_execute_trade_intent",
      arguments: {
        intentId: "intent-limit",
        pair: "ETH/USDC",
        amount: "10",
        orderType: "limit",
        limitPrice: "2000",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.deepEqual(content, {
      intentId: "intent-limit",
      pair: "ETH/USDC",
      side: "long",
      status: "rejected",
      mode: "mock",
      sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
      buyToken: "0x4200000000000000000000000000000000000006",
      sellAmount: "10",
      buyAmount: null,
      txHash: null,
      failureStage: "intent",
      failureReason: "limit orders are unsupported in v1",
    });
  });
});

test("chain_execute_trade_intent rejects limitPrice for market orders through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_execute_trade_intent",
      arguments: {
        intentId: "intent-market-limit-price",
        pair: "ETH/USDC",
        amount: "10",
        orderType: "market",
        limitPrice: "2000",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.deepEqual(content, {
      intentId: "intent-market-limit-price",
      pair: "ETH/USDC",
      side: "long",
      status: "rejected",
      mode: "mock",
      sellToken: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
      buyToken: "0x4200000000000000000000000000000000000006",
      sellAmount: "10",
      buyAmount: null,
      txHash: null,
      failureStage: "intent",
      failureReason: "limitPrice is unsupported for market orders in v1",
    });
  });
});

test("chain_get_wallet_state returns the configured mock wallet balance", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_get_wallet_state",
      arguments: {},
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.wallet?.mockChain, true);
    assert.equal(content.wallet?.balanceEth, "1.0");
    assert.equal(content.wallet?.usdcBalanceAtomic, "321123456");
    assert.equal(content.wallet?.usdcBalance, "321.123456");
    assert.equal(content.policy?.dailyLimitUsdcAtomic, "2000000");
  });
});

test("chain_sign_transfer returns signed transfer result through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_sign_transfer",
      arguments: {
        to: "0x2222222222222222222222222222222222222222",
        amountEth: "0.01",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.transfer?.to, "0x2222222222222222222222222222222222222222");
    assert.equal(content.settlement?.kind, "signed");
  });
});

test("chain_submit_execution returns submitted execution through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_submit_execution",
      arguments: {
        to: "0x3333333333333333333333333333333333333333",
        valueEth: "0.001",
        data: "0x",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.execution?.to, "0x3333333333333333333333333333333333333333");
    assert.equal(content.settlement?.kind, "submitted");
  });
});

test("chain_submit_user_operation works in mock mode through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_submit_user_operation",
      arguments: {
        target: "0x3333333333333333333333333333333333333333",
        maxCostEth: "0.01",
        raw: {
          sender: "0x123",
        },
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.match(content.userOperation?.userOpHash, /^0xmock_userop_/);
  });
});

test("chain_get_transaction_receipt returns mock success receipt status through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_get_transaction_receipt",
      arguments: {
        txHash: "0xexec123",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.txHash, "0xexec123");
    assert.equal(content.found, true);
    assert.equal(content.finalized, true);
    assert.equal(content.success, true);
    assert.equal(content.status, "success");
    assert.equal(content.mode, "mock");
  });
});

test("chain_get_transaction_receipt returns pending when receipt is missing", async () => {
  await withClient(
    async (client) => {
      const response = await client.callTool({
        name: "chain_get_transaction_receipt",
        arguments: {
          txHash: "0xpending123",
        },
      });

      assert.notEqual(response.isError, true);
      const content = response.structuredContent as Record<string, any>;
      assert.equal(content.txHash, "0xpending123");
      assert.equal(content.found, false);
      assert.equal(content.finalized, false);
      assert.equal(content.success, false);
      assert.equal(content.status, "pending");
      assert.equal(content.receipt, null);
      assert.equal(content.mode, "network");
    },
    {
      transactionReceiptService: {
        execute: async (txHash: string) => ({
          txHash,
          found: false,
          finalized: false,
          success: false,
          status: "pending" as const,
          blockNumber: null,
          receipt: null,
          mode: "network" as const,
        }),
      } as RuntimeOverrides["transactionReceiptService"],
    },
  );
});

test("chain_get_user_operation_status returns mock success status through MCP", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_get_user_operation_status",
      arguments: {
        userOpHash: "0xmock_userop_123",
      },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.userOpHash, "0xmock_userop_123");
    assert.equal(content.found, true);
    assert.equal(content.finalized, true);
    assert.equal(content.success, true);
    assert.equal(content.status, "success");
    assert.equal(content.mode, "mock");
  });
});

test("chain_get_user_operation_status returns failed terminal status", async () => {
  await withClient(
    async (client) => {
      const response = await client.callTool({
        name: "chain_get_user_operation_status",
        arguments: {
          userOpHash: "0xfailed_userop_123",
        },
      });

      assert.notEqual(response.isError, true);
      const content = response.structuredContent as Record<string, any>;
      assert.equal(content.userOpHash, "0xfailed_userop_123");
      assert.equal(content.found, true);
      assert.equal(content.finalized, true);
      assert.equal(content.success, false);
      assert.equal(content.status, "failed");
      assert.equal(content.txHash, "0xfailedtx123");
      assert.deepEqual(content.receipt, {
        userOpHash: "0xfailed_userop_123",
        transactionHash: "0xfailedtx123",
        blockNumber: 99,
        status: 0,
      });
      assert.equal(content.mode, "network");
    },
    {
      userOperationStatusService: {
        execute: async (userOpHash: string) => ({
          userOpHash,
          found: true,
          finalized: true,
          success: false,
          status: "failed" as const,
          txHash: "0xfailedtx123",
          receipt: {
            userOpHash,
            transactionHash: "0xfailedtx123",
            blockNumber: 99,
            status: 0,
          },
          mode: "network" as const,
        }),
      } as unknown as RuntimeOverrides["userOperationStatusService"],
    },
  );
});

test("chain_x402_fetch returns x402 result through MCP", async () => {
  await withClient(
    async (client) => {
      const response = await client.callTool({
        name: "chain_x402_fetch",
        arguments: {
          url: "http://x402-seller:8000/x402/demo-resource",
          method: "GET",
        },
      });

      assert.notEqual(response.isError, true);
      const content = response.structuredContent as Record<string, any>;
      assert.equal(content.payment?.selected?.network, "eip155:84532");
      assert.equal(content.upstream?.status, 200);
    },
    {
      x402FetchService: {
        execute: async () => ({
          upstream: {
            status: 200,
            contentType: "application/json",
            payload: { ok: true },
          },
          payment: {
            requiredVersion: 2,
            selected: {
              scheme: "exact",
              network: "eip155:84532",
              asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
              amount: "10000",
              payTo: "0x3333333333333333333333333333333333333333",
              maxTimeoutSeconds: 300,
              extra: { name: "USDC", version: "2" },
            },
            response: {
              success: true,
              transaction: "0xsettled",
              network: "eip155:84532",
            },
          },
          decision: {
            action: "x402-fetch" as const,
            normalizedTo: "0x3333333333333333333333333333333333333333",
            network: "eip155:84532",
            asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            amountAtomic: "10000",
            allowed: true as const,
          },
          policy: {
            dayKey: "2026-03-26",
            spentTodayWei: "0",
            dailyLimitWei: "2000000000000000000",
            spentTodayUsdcAtomic: "10000",
            dailyLimitUsdcAtomic: "2000000",
          },
        }),
      } as unknown as X402FetchService,
    },
  );
});
