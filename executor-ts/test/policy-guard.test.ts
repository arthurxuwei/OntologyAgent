import test from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "../src/config.js";
import { PolicyGuard } from "../src/policies/policy-guard.js";

test("PolicyGuard allows whitelisted amounts within limits", () => {
  const config = loadConfig({
    EXECUTOR_MOCK_CHAIN: "true",
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy);

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
  const guard = new PolicyGuard(config.policy);

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
