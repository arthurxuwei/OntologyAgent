import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { loadConfig } from "../src/config.js";
import { createCircleHttpApp, createCircleRuntime } from "../src/http/circle-app.js";

type RuntimeOverrides = NonNullable<Parameters<typeof createCircleRuntime>[1]>;

function createMockConfig() {
  return loadConfig({
    CHAIN_MOCK: "true",
    PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
  });
}

async function request(
  path: string,
  options: RequestInit = {},
  overrides?: RuntimeOverrides,
) {
  const { app } = createCircleHttpApp(createMockConfig(), overrides);
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

test("circle REST health returns service metadata", async () => {
  const response = await request("/health");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.service, "chief-circle");
  assert.equal(body.status, "ok");
  assert.equal(body.mockChain, true);
});

test("circle REST exposes Agent Wallet lifecycle and settlement routes", async () => {
  const response = await request("/circle/routes");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.routes.sort(), [
    "GET /circle/transactions/:transactionId",
    "GET /circle/wallets/status",
    "POST /circle/gas-topups/resume",
    "POST /circle/gas-topups/webhook",
    "POST /circle/gateway/deposits",
    "POST /circle/gateway/withdrawals",
    "POST /circle/settlements",
    "POST /circle/wallets/get-or-create",
    "POST /circle/wallets/import",
    "POST /circle/wallets/init",
  ]);
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
