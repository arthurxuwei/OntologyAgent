import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { loadConfig } from "../src/config.js";
import { createChainHttpApp, createChainRuntime } from "../src/http/chain-app.js";
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

async function request(
  path: string,
  options: RequestInit = {},
  overrides?: RuntimeOverrides,
) {
  const { app } = createChainHttpApp(createMockConfig(), overrides);
  const server = createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo | null;
  if (address === null) {
    throw new Error("test server did not expose an address");
  }
  try {
    return await fetch(`http://127.0.0.1:${address.port}${path}`, {
      ...options,
      headers: {
        "content-type": "application/json",
        ...(options.headers ?? {}),
      },
    });
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
}

test("chain REST health returns service metadata", async () => {
  const response = await request("/health");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.service, "kovaloop-chain");
  assert.equal(body.status, "ok");
  assert.equal(body.mockChain, true);
});

test("chain REST returns the configured mock wallet balance", async () => {
  const response = await request("/chain/wallet-state");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.wallet?.mockChain, true);
  assert.equal(body.wallet?.balanceEth, "1.0");
  assert.equal(body.wallet?.usdcBalanceAtomic, "321123456");
  assert.equal(body.policy?.dailyLimitUsdcAtomic, "2000000");
});

test("chain REST signs transfers", async () => {
  const response = await request("/chain/transfers/sign", {
    method: "POST",
    body: JSON.stringify({
      to: "0x2222222222222222222222222222222222222222",
      amountEth: "0.01",
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.transfer?.to, "0x2222222222222222222222222222222222222222");
  assert.equal(body.settlement?.kind, "signed");
});

test("chain REST submits executions", async () => {
  const response = await request("/chain/executions", {
    method: "POST",
    body: JSON.stringify({
      to: "0x3333333333333333333333333333333333333333",
      valueEth: "0.001",
      data: "0x",
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.execution?.to, "0x3333333333333333333333333333333333333333");
  assert.equal(body.settlement?.kind, "submitted");
});

test("chain REST submits user operations", async () => {
  const response = await request("/chain/user-operations", {
    method: "POST",
    body: JSON.stringify({
      target: "0x3333333333333333333333333333333333333333",
      maxCostEth: "0.01",
      raw: { sender: "0x123" },
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.match(body.userOperation?.userOpHash, /^0xmock_userop_/);
});

test("chain REST returns transaction receipt status", async () => {
  const response = await request("/chain/transactions/0xexec123");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.txHash, "0xexec123");
  assert.equal(body.status, "success");
  assert.equal(body.mode, "mock");
});

test("chain REST returns pending when receipt is missing", async () => {
  const response = await request(
    "/chain/transactions/0xpending123",
    {},
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
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "pending");
  assert.equal(body.receipt, null);
});

test("chain REST returns user operation status", async () => {
  const response = await request("/chain/user-operations/0xmock_userop_123");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.userOpHash, "0xmock_userop_123");
  assert.equal(body.status, "success");
});

test("chain REST performs x402 fetches", async () => {
  const response = await request(
    "/x402/fetch",
    {
      method: "POST",
      body: JSON.stringify({
        url: "http://x402-seller:8000/x402/demo-resource",
        method: "GET",
      }),
    },
    {
      x402FetchService: {
        execute: async () => ({
          upstream: { status: 200, contentType: "application/json", payload: { ok: true } },
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
            response: { success: true, transaction: "0xsettled", network: "eip155:84532" },
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
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.payment?.selected?.network, "eip155:84532");
  assert.equal(body.upstream?.status, 200);
});
