import test from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";

import { loadConfig } from "../src/config.js";
import { createChainMcpServer, createChainRuntime } from "../src/mcp/tools.js";
import type { X402FetchService } from "../src/services/x402-fetch-service.js";

function createMockConfig() {
  return loadConfig({
    CHAIN_MOCK: "true",
    PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
    WHITELISTED_RECIPIENTS:
      "0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333",
  });
}

async function withClient(
  callback: (client: Client) => Promise<void>,
  overrides?: {
    x402FetchService?: X402FetchService;
  },
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
      "chain_get_transaction_receipt",
      "chain_get_wallet_state",
      "chain_sign_transfer",
      "chain_submit_execution",
      "chain_submit_user_operation",
      "chain_x402_fetch",
    ]);
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
