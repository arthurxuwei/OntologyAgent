import test from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "../src/config.js";
import { PolicyGuard } from "../src/policies/policy-guard.js";

test("PolicyGuard allows whitelisted amounts within limits", () => {
  const config = loadConfig({
    EXECUTOR_MOCK_CHAIN: "true",
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  const decision = guard.authorize(
    "execution-submit",
    "0x2222222222222222222222222222222222222222",
    1n,
  );

  assert.equal(decision.allowed, true);
  assert.equal(decision.normalizedTo, "0x2222222222222222222222222222222222222222");
});

test("PolicyGuard rejects addresses outside whitelist", () => {
  const config = loadConfig({
    EXECUTOR_MOCK_CHAIN: "true",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  assert.throws(
    () =>
      guard.authorize(
        "execution-submit",
        "0x2222222222222222222222222222222222222222",
        1n,
      ),
    /Address not allowed by executor whitelist/,
  );
});

test("PolicyGuard authorizes x402 Base Sepolia USDC within limits", () => {
  const config = loadConfig({
    EXECUTOR_MOCK_CHAIN: "true",
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  const decision = guard.authorizeX402(
    "0x2222222222222222222222222222222222222222",
    10000n,
    "eip155:84532",
    "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  );

  assert.equal(decision.allowed, true);
  assert.equal(decision.network, "eip155:84532");
});

test("PolicyGuard rejects x402 asset outside configured USDC", () => {
  const config = loadConfig({
    EXECUTOR_MOCK_CHAIN: "true",
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  assert.throws(
    () =>
      guard.authorizeX402(
        "0x2222222222222222222222222222222222222222",
        10000n,
        "eip155:84532",
        "0x1111111111111111111111111111111111111111",
      ),
    /x402 asset not allowed/,
  );
});
