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

test("loadConfig normalizes private keys with missing 0x prefix", () => {
  const config = loadConfig({
    PRIVATE_KEY: "abc123",
    X402_BUYER_PRIVATE_KEY: "def456",
  });

  assert.equal(config.signer.privateKey, "0xabc123");
  assert.equal(config.x402.buyerPrivateKey, "0xdef456");
});

test("loadConfig exposes mock balance for chain wallet state", () => {
  const config = loadConfig({
    CHAIN_MOCK_BALANCE_ETH: "2.5",
  });

  assert.equal(config.network.mockBalanceWei.toString(), "2500000000000000000");
});
