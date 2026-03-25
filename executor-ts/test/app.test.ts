import test from "node:test";
import assert from "node:assert/strict";

import { buildApp } from "../src/app.js";
import { loadConfig } from "../src/config.js";

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
