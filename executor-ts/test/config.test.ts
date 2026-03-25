import test from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "../src/config.js";

test("loadConfig defaults to Base Sepolia and x402 v2 settings", () => {
  const config = loadConfig({});

  assert.equal(config.network.expectedChainId, 84532);
  assert.equal(config.network.rpcUrl, "https://base-sepolia-rpc.publicnode.com");
  assert.equal(config.x402.network, "eip155:84532");
  assert.equal(config.x402.facilitatorUrl, "https://x402.org/facilitator");
  assert.equal(config.x402.usdcAssetAddress, "0x036CbD53842c5426634e7929541eC2318f3dCF7e");
});
