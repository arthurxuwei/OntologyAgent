import test from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";

import { loadConfig } from "../src/config.js";
import { createCircleMcpServer, createCircleRuntime } from "../src/mcp/circle-tools.js";

type RuntimeOverrides = NonNullable<Parameters<typeof createCircleRuntime>[1]>;

function createMockConfig() {
  return loadConfig({
    CHAIN_MOCK: "true",
    PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
  });
}

async function withClient(
  callback: (client: Client) => Promise<void>,
  overrides?: RuntimeOverrides,
) {
  const runtime = createCircleRuntime(createMockConfig(), overrides);
  const client = new Client({
    name: "circle-mcp-test-client",
    version: "1.0.0",
  });
  const transport = new StreamableHTTPClientTransport(new URL("http://circle-mcp:8093/mcp"), {
    fetch: async (input, init) => {
      const server = createCircleMcpServer(runtime);
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

test("circle MCP exposes Agent Wallet lifecycle and settlement tools", async () => {
  await withClient(async (client) => {
    const response = await client.listTools();
    const toolNames = response.tools.map((tool) => tool.name).sort();
    assert.deepEqual(toolNames, [
      "agent_wallet_gateway_deposit",
      "agent_wallet_get_or_create",
      "agent_wallet_import_circle_wallets",
      "agent_wallet_init",
      "agent_wallet_settle_ledger_transfer",
      "agent_wallet_status",
      "agent_wallet_transaction_status",
    ]);
  });
});

test("circle runtime wires live Circle wallet creation with a per-request ciphertext factory", () => {
  const runtime = createCircleRuntime(
    loadConfig({
      PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
      CIRCLE_API_KEY: "circle-api-key",
      CIRCLE_WALLET_SET_ID: "circle-wallet-set",
      CIRCLE_ENTITY_SECRET: "entity-secret",
    }),
  );

  const circleWalletService = (runtime.agentWalletService as any).circleWalletService;
  assert.equal(typeof circleWalletService.createEntitySecretCiphertext, "function");
});
