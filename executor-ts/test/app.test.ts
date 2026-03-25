import test from "node:test";
import assert from "node:assert/strict";

import { buildApp } from "../src/app.js";
import { loadConfig } from "../src/config.js";
import { X402FetchService } from "../src/services/x402-fetch-service.js";

function createMockConfig() {
  return loadConfig({
    EXECUTOR_MOCK_CHAIN: "true",
    WHITELISTED_RECIPIENTS:
      "0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333",
  });
}

test("POST /transfers/sign returns envelope and signed transfer result", async () => {
  const app = buildApp(createMockConfig());

  const response = await app.inject({
    method: "POST",
    url: "/transfers/sign",
    payload: {
      to: "0x2222222222222222222222222222222222222222",
      amountEth: "0.01",
    },
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.ok, true);
  assert.equal(body.result.transfer.to, "0x2222222222222222222222222222222222222222");
  assert.equal(body.result.settlement.kind, "signed");

  await app.close();
});

test("POST /executions/submit returns envelope and submitted execution", async () => {
  const app = buildApp(createMockConfig());

  const response = await app.inject({
    method: "POST",
    url: "/executions/submit",
    payload: {
      to: "0x3333333333333333333333333333333333333333",
      valueEth: "0.001",
      data: "0x",
    },
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.ok, true);
  assert.equal(body.result.execution.to, "0x3333333333333333333333333333333333333333");
  assert.equal(body.result.settlement.kind, "submitted");

  await app.close();
});

test("POST /user-operations/submit works in mock mode", async () => {
  const app = buildApp(createMockConfig());

  const response = await app.inject({
    method: "POST",
    url: "/user-operations/submit",
    payload: {
      target: "0x3333333333333333333333333333333333333333",
      maxCostEth: "0.01",
      raw: {
        sender: "0x123",
      },
    },
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.ok, true);
  assert.match(body.result.userOperation.userOpHash, /^0xmock_userop_/);

  await app.close();
});

test("POST /x402/fetch returns envelope and x402 result", async () => {
  const app = buildApp(createMockConfig(), {
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
  });

  const response = await app.inject({
    method: "POST",
    url: "/x402/fetch",
    payload: {
      url: "http://brain-py:8000/x402/demo-resource",
      method: "GET",
    },
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.ok, true);
  assert.equal(body.result.payment.selected.network, "eip155:84532");

  await app.close();
});
