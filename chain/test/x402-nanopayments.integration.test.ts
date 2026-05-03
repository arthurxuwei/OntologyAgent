import test from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "../src/config.js";
import { PolicyGuard } from "../src/policies/policy-guard.js";
import { X402FetchService } from "../src/services/x402-fetch-service.js";

const TEST_PRIVATE_KEY =
  "0x59c6995e998f97a5a0044966f0945382d8f6d5b40f5f0c6d9c0a0f6f6b6b6b6b";

const NANOPAYMENTS_RESOURCE_URL =
  process.env.X402_NANOPAYMENTS_RESOURCE_URL ??
  "http://x402-seller:8000/x402/agent-services/research-summary/nanopayments";

const integrationTest =
  process.env.RUN_X402_NANOPAYMENTS_INTEGRATION === "true" ? test : test.skip;
const liveIntegrationTest =
  process.env.RUN_X402_NANOPAYMENTS_LIVE === "true" ? test : test.skip;

function findGatewayRequirement(challenge: any) {
  return challenge.accepts?.find(
    (requirement: any) => requirement.extra?.name === "GatewayWalletBatched",
  );
}

function requireEnv(name: string): string {
  const value = process.env[name];
  assert.ok(value, `${name} is required`);
  return value;
}

integrationTest(
  "chain buyer selects GatewayWalletBatched against x402-seller with mock facilitator",
  { timeout: 30_000 },
  async () => {
    const challengeResponse = await fetch(NANOPAYMENTS_RESOURCE_URL);
    assert.equal(challengeResponse.status, 402);
    const challenge = await challengeResponse.json();
    const gatewayRequirement = findGatewayRequirement(challenge);

    assert.ok(gatewayRequirement, "seller did not advertise GatewayWalletBatched");
    assert.equal(gatewayRequirement.network, "eip155:84532");
    assert.equal(
      gatewayRequirement.extra.verifyingContract,
      "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
    );

    const config = loadConfig({
      CHAIN_MOCK: "true",
      PRIVATE_KEY: process.env.X402_INTEGRATION_PRIVATE_KEY ?? TEST_PRIVATE_KEY,
      RPC_URL: process.env.RPC_URL ?? "https://base-sepolia-rpc.publicnode.com",
      WHITELISTED_RECIPIENTS: gatewayRequirement.payTo,
    });
    const guard = new PolicyGuard(config.policy, config.x402);
    const service = new X402FetchService(config, guard);

    const result = await service.execute({
      url: NANOPAYMENTS_RESOURCE_URL,
      method: "GET",
      paymentPreference: "circle-gateway",
    });

    assert.equal(result.upstream.status, 200);
    assert.equal(result.payment?.selected.extra.name, "GatewayWalletBatched");
    assert.equal(
      result.payment?.selected.extra.verifyingContract,
      "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
    );
    assert.equal(result.payment?.response.success, true);
    assert.match(result.payment?.response.transaction ?? "", /^0xmock_x402_settlement_/);
  },
);

liveIntegrationTest(
  "chain buyer settles GatewayWalletBatched through the configured live facilitator",
  { timeout: 60_000 },
  async () => {
    const buyerPrivateKey = requireEnv("X402_LIVE_BUYER_PRIVATE_KEY");
    const expectedPayTo = requireEnv("X402_LIVE_PAY_TO");
    const liveResourceUrl =
      process.env.X402_LIVE_NANOPAYMENTS_RESOURCE_URL ?? NANOPAYMENTS_RESOURCE_URL;
    const liveFacilitatorUrl =
      process.env.X402_LIVE_FACILITATOR_URL ?? "https://gateway-api-testnet.circle.com";

    const challengeResponse = await fetch(liveResourceUrl);
    assert.equal(challengeResponse.status, 402);
    const challenge = await challengeResponse.json();
    const gatewayRequirement = findGatewayRequirement(challenge);

    assert.ok(gatewayRequirement, "seller did not advertise GatewayWalletBatched");
    assert.equal(gatewayRequirement.payTo.toLowerCase(), expectedPayTo.toLowerCase());
    assert.equal(gatewayRequirement.network, "eip155:84532");
    assert.equal(
      gatewayRequirement.extra.verifyingContract,
      "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
    );

    const config = loadConfig({
      CHAIN_MOCK: "false",
      PRIVATE_KEY: buyerPrivateKey,
      RPC_URL: process.env.RPC_URL ?? "https://base-sepolia-rpc.publicnode.com",
      X402_FACILITATOR_URL: liveFacilitatorUrl,
      X402_USDC_SINGLE_CAP: process.env.X402_LIVE_USDC_SINGLE_CAP ?? "0.05",
      X402_USDC_DAILY_CAP: process.env.X402_LIVE_USDC_DAILY_CAP ?? "0.10",
      WHITELISTED_RECIPIENTS: gatewayRequirement.payTo,
    });
    const guard = new PolicyGuard(config.policy, config.x402);
    const service = new X402FetchService(config, guard);

    const result = await service.execute({
      url: liveResourceUrl,
      method: "GET",
      paymentPreference: "circle-gateway",
    });

    assert.equal(result.upstream.status, 200);
    assert.equal(result.payment?.selected.extra.name, "GatewayWalletBatched");
    assert.equal(result.payment?.response.success, true);
    assert.equal(result.payment?.response.network, "eip155:84532");
    assert.ok(result.payment?.response.transaction);
  },
);
