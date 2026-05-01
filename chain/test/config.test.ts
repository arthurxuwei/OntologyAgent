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
    CHAIN_MOCK_USDC_BALANCE: "123.456789",
  });

  assert.equal(config.network.mockBalanceWei.toString(), "2500000000000000000");
  assert.equal(config.network.mockUsdcBalanceAtomic.toString(), "123456789");
});

test("loadConfig reads Agent Wallet state path", () => {
  const config = loadConfig({ AGENT_WALLET_STATE_PATH: "/tmp/agent-wallet.json" });

  assert.equal(config.agentWallet.statePath, "/tmp/agent-wallet.json");
});

test("loadConfig rejects non-integer values for integer config fields", () => {
  assert.throws(
    () =>
      loadConfig({
        CHAIN_MCP_PORT: "8091.5",
      }),
    /CHAIN_MCP_PORT must be a positive integer/,
  );

  assert.throws(
    () =>
      loadConfig({
        CHAIN_ID: "84532.1",
      }),
    /CHAIN_ID must be a positive integer/,
  );
});
