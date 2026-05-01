import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { loadConfig } from "../src/config.js";
import { AppError } from "../src/domain/errors.js";
import type { X402FetchResult } from "../src/domain/types.js";
import { AgentWalletService } from "../src/services/agent-wallet-service.js";
import { CircleWalletService } from "../src/services/circle-wallet-service.js";
import type { X402FetchService } from "../src/services/x402-fetch-service.js";

function fakeX402FetchService(result: X402FetchResult): X402FetchService {
  return {
    execute: async () => result,
  } as unknown as X402FetchService;
}

function baseX402Result(): X402FetchResult {
  return {
    upstream: {
      status: 200,
      contentType: "application/json",
      payload: { ok: true },
    },
    payment: null,
    decision: null,
    policy: {
      dayKey: "2026-04-24",
      spentTodayWei: "0",
      dailyLimitWei: "0",
      spentTodayUsdcAtomic: "0",
      dailyLimitUsdcAtomic: "0",
    },
  };
}

function liveCircleConfig(overrides: NodeJS.ProcessEnv = {}) {
  return loadConfig({
    CIRCLE_API_KEY: "circle-api-key",
    CIRCLE_WALLET_SET_ID: "circle-wallet-set",
    CIRCLE_ENTITY_SECRET: "entity-secret",
    CIRCLE_BASE_URL: "https://circle.test/v1/w3s",
    ...overrides,
  });
}

async function withTempStateFile(
  payload: unknown,
  callback: (statePath: string) => Promise<void>,
) {
  const dir = await mkdtemp(join(tmpdir(), "agent-wallet-state-"));
  const statePath = join(dir, "agent_wallet_state.json");
  await writeFile(statePath, JSON.stringify(payload), "utf-8");
  try {
    await callback(statePath);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

async function assertRejectsWithAppError(
  action: () => Promise<unknown>,
  expected: {
    code: AppError["code"];
    statusCode: number;
    message: RegExp;
  },
): Promise<AppError> {
  try {
    await action();
  } catch (error) {
    assert.ok(error instanceof AppError);
    assert.equal(error.code, expected.code);
    assert.equal(error.statusCode, expected.statusCode);
    assert.match(error.message, expected.message);
    return error;
  }

  assert.fail("Expected AppError");
}

test("AgentWalletService creates deterministic mock Circle wallet in CHAIN_MOCK mode", async () => {
  const config = loadConfig({ CHAIN_MOCK: "true" });
  const service = new AgentWalletService(config, fakeX402FetchService(baseX402Result()));

  const first = await service.init({ agentName: "Research Summary" });
  const second = await service.init({ agentName: "Research Summary" });

  assert.deepEqual(first, second);
  assert.equal(first.mode, "mock");
  assert.equal(first.blockchain, "BASE-SEPOLIA");
  assert.match(first.circleWalletId, /^mock-circle-wallet-/);
  assert.match(first.walletAddress, /^0x[0-9a-fA-F]{40}$/);
});

test("AgentWalletService reuses an existing Agent Wallet before creating one", async () => {
  const config = loadConfig({ CHAIN_MOCK: "true" });
  let createCalls = 0;
  const circleWalletService = {
    createWallet: async () => {
      createCalls += 1;
      return {
        circleWalletId: "new-wallet",
        circleWalletSetId: "new-wallet-set",
        blockchain: "BASE-SEPOLIA" as const,
        walletAddress: "0x2222222222222222222222222222222222222222",
        mode: "mock" as const,
      };
    },
  } as unknown as CircleWalletService;
  const service = new AgentWalletService(
    config,
    fakeX402FetchService(baseX402Result()),
    circleWalletService,
  );

  const result = await service.getOrCreate({
    agentName: "Research Summary",
    walletAddress: "0x3333333333333333333333333333333333333333",
    circleWalletId: "existing-circle-wallet",
  });

  assert.equal(createCalls, 0);
  assert.equal(result.reused, true);
  assert.equal(result.circleWalletId, "existing-circle-wallet");
  assert.equal(result.walletAddress, "0x3333333333333333333333333333333333333333");
  assert.equal(result.status, "available");
});

test("AgentWalletService creates an Agent Wallet when no existing wallet is supplied", async () => {
  const config = loadConfig({ CHAIN_MOCK: "true" });
  let createCalls = 0;
  const circleWalletService = {
    createWallet: async (agentName: string) => {
      createCalls += 1;
      assert.equal(agentName, "Research Summary");
      return {
        circleWalletId: "new-wallet",
        circleWalletSetId: "new-wallet-set",
        blockchain: "BASE-SEPOLIA" as const,
        walletAddress: "0x2222222222222222222222222222222222222222",
        mode: "mock" as const,
      };
    },
  } as unknown as CircleWalletService;
  const service = new AgentWalletService(
    config,
    fakeX402FetchService(baseX402Result()),
    circleWalletService,
  );

  const result = await service.getOrCreate({ agentName: " Research Summary " });

  assert.equal(createCalls, 1);
  assert.equal(result.reused, false);
  assert.equal(result.circleWalletId, "new-wallet");
  assert.equal(result.walletAddress, "0x2222222222222222222222222222222222222222");
});

test("AgentWalletService reuses a matching wallet from local Agent Wallet state", async () => {
  await withTempStateFile(
    {
      wallets: [
        {
          agentName: "Research Summary",
          circleWalletId: "circle-wallet-1",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          walletAddress: "0x3333333333333333333333333333333333333333",
          mode: "circle",
        },
      ],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "true",
        AGENT_WALLET_STATE_PATH: statePath,
      });
      let createCalls = 0;
      const circleWalletService = {
        createWallet: async () => {
          createCalls += 1;
          throw new Error("createWallet should not be called");
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.getOrCreate({ agentName: " Research Summary " });

      assert.equal(createCalls, 0);
      assert.equal(result.reused, true);
      assert.equal(result.circleWalletId, "circle-wallet-1");
      assert.equal(result.circleWalletSetId, "circle-wallet-set");
      assert.equal(result.walletAddress, "0x3333333333333333333333333333333333333333");
      assert.equal(result.mode, "circle");
    },
  );
});

test("AgentWalletService normalizes x402 service registration", async () => {
  const config = loadConfig({ CHAIN_MOCK: "true" });
  const service = new AgentWalletService(config, fakeX402FetchService(baseX402Result()));

  const result = await service.registerX402Service({
    name: " Research Summary ",
    path: "/x402/agent-services/research-summary",
    priceAtomic: "10000",
    payTo: "0x3333333333333333333333333333333333333333",
  });

  assert.deepEqual(result, {
    name: "Research Summary",
    path: "/x402/agent-services/research-summary",
    priceAtomic: "10000",
    assetAddress: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
    network: "eip155:84532",
    payTo: "0x3333333333333333333333333333333333333333",
    active: true,
  });
});

test("AgentWalletService wraps x402 fetch result with agent wallet tool marker", async () => {
  const config = loadConfig({ CHAIN_MOCK: "true" });
  const service = new AgentWalletService(config, fakeX402FetchService(baseX402Result()));

  const result = await service.callX402Service({
    url: "https://example.com/research",
  });

  assert.equal(result.agentWalletTool, "agent_wallet_call_x402_service");
  assert.equal(result.upstream.status, 200);
});

test("CircleWalletService creates deterministic mock wallets with exact repeatability and different seed behavior", async () => {
  const service = new CircleWalletService(loadConfig({ CHAIN_MOCK: "true" }));

  const first = await service.createWallet("Research Summary");
  const second = await service.createWallet("Research Summary");
  const different = await service.createWallet("Market Scanner");

  assert.deepEqual(first, second);
  assert.notEqual(first.walletAddress, different.walletAddress);
  assert.notEqual(first.circleWalletId, different.circleWalletId);
  assert.deepEqual(first, {
    circleWalletId: "mock-circle-wallet-research-summary",
    circleWalletSetId: "mock-circle-wallet-set",
    blockchain: "BASE-SEPOLIA",
    walletAddress: first.walletAddress,
    mode: "mock",
  });
  assert.match(first.walletAddress, /^0x[0-9a-fA-F]{40}$/);
});

test("CircleWalletService rejects live creation when required config is missing", async () => {
  const service = new CircleWalletService(loadConfig({}));

  await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "CONFIG_ERROR",
    statusCode: 500,
    message: /CIRCLE_API_KEY, CIRCLE_WALLET_SET_ID, and CIRCLE_ENTITY_SECRET are required/,
  });
});

test("CircleWalletService rejects live creation with only static entity secret ciphertext", async () => {
  const service = new CircleWalletService(
    liveCircleConfig({
      CIRCLE_ENTITY_SECRET: "",
      CIRCLE_ENTITY_SECRET_CIPHERTEXT: "static-ciphertext",
    }),
  );

  await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "CONFIG_ERROR",
    statusCode: 500,
    message: /static CIRCLE_ENTITY_SECRET_CIPHERTEXT cannot be reused/,
  });
});

test("CircleWalletService rejects live creation until per-request ciphertext generation is configured", async () => {
  const service = new CircleWalletService(liveCircleConfig());

  await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "CONFIG_ERROR",
    statusCode: 500,
    message: /per-request Circle entitySecretCiphertext generation is required/,
  });
});

test("CircleWalletService maps non-OK Circle responses to upstream request failures", async () => {
  const service = new CircleWalletService(liveCircleConfig(), {
    createEntitySecretCiphertext: async () => "ciphertext-1",
    fetchImpl: async () =>
      new Response(JSON.stringify({ error: "bad_request" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      }),
  });

  const error = await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "UPSTREAM_REQUEST_FAILED",
    statusCode: 400,
    message: /Circle wallet creation failed with HTTP 400/,
  });
  assert.deepEqual(error.details, { error: "bad_request" });
});

test("CircleWalletService maps malformed successful Circle payloads to upstream request failures", async () => {
  const service = new CircleWalletService(liveCircleConfig(), {
    createEntitySecretCiphertext: async () => "ciphertext-1",
    fetchImpl: async () =>
      new Response(JSON.stringify({ data: { wallets: [{}] } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  });

  await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "UPSTREAM_REQUEST_FAILED",
    statusCode: 502,
    message: /Circle wallet response did not include id and address/,
  });
});

test("CircleWalletService rejects Circle wallets on unsupported blockchains", async () => {
  const payload = {
    data: {
      wallets: [
        {
          id: "circle-wallet-1",
          walletSetId: "circle-wallet-set",
          blockchain: "ETH-SEPOLIA",
          address: "0x3333333333333333333333333333333333333333",
        },
      ],
    },
  };
  const service = new CircleWalletService(liveCircleConfig(), {
    createEntitySecretCiphertext: async () => "ciphertext-1",
    fetchImpl: async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  });

  const error = await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "NETWORK_MISMATCH",
    statusCode: 502,
    message: /Circle returned unsupported blockchain ETH-SEPOLIA/,
  });
  assert.deepEqual(error.details, payload);
});

test("CircleWalletService rejects successful Circle payloads with invalid wallet addresses", async () => {
  const service = new CircleWalletService(liveCircleConfig(), {
    createEntitySecretCiphertext: async () => "ciphertext-1",
    fetchImpl: async () =>
      new Response(
        JSON.stringify({
          data: {
            wallets: [
              {
                id: "circle-wallet-1",
                blockchain: "BASE-SEPOLIA",
                address: "not-an-address",
              },
            ],
          },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
  });

  await assertRejectsWithAppError(() => service.createWallet("Research Summary"), {
    code: "UPSTREAM_REQUEST_FAILED",
    statusCode: 502,
    message: /Circle wallet response included an invalid address/,
  });
});

test("CircleWalletService returns normalized successful Circle wallet shape", async () => {
  let requestBody: Record<string, unknown> | undefined;
  const service = new CircleWalletService(liveCircleConfig(), {
    createEntitySecretCiphertext: async (entitySecret) => `ciphertext-for-${entitySecret}`,
    fetchImpl: async (input, init) => {
      assert.equal(input, "https://circle.test/v1/w3s/developer/wallets");
      assert.equal(init?.method, "POST");
      assert.equal(
        (init?.headers as Record<string, string>).Authorization,
        "Bearer circle-api-key",
      );
      requestBody = JSON.parse(String(init?.body)) as Record<string, unknown>;

      return new Response(
        JSON.stringify({
          data: {
            wallets: [
              {
                id: "circle-wallet-1",
                blockchain: "BASE-SEPOLIA",
                address: "0x3333333333333333333333333333333333333333",
              },
            ],
          },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    },
  });

  const result = await service.createWallet("Research Summary");

  assert.equal(requestBody?.walletSetId, "circle-wallet-set");
  assert.equal(requestBody?.entitySecretCiphertext, "ciphertext-for-entity-secret");
  assert.deepEqual(requestBody?.blockchains, ["BASE-SEPOLIA"]);
  assert.deepEqual(requestBody?.metadata, [
    {
      name: "Research Summary",
      refId: "research-summary",
    },
  ]);
  assert.deepEqual(result, {
    circleWalletId: "circle-wallet-1",
    circleWalletSetId: "circle-wallet-set",
    blockchain: "BASE-SEPOLIA",
    walletAddress: "0x3333333333333333333333333333333333333333",
    mode: "circle",
  });
});

test("CircleWalletService lists existing Circle wallets for a wallet set", async () => {
  const service = new CircleWalletService(liveCircleConfig(), {
    fetchImpl: async (input, init) => {
      assert.equal(
        input,
        "https://circle.test/v1/w3s/wallets?walletSetId=circle-wallet-set&blockchain=BASE-SEPOLIA",
      );
      assert.equal(init?.method, "GET");
      assert.equal(
        (init?.headers as Record<string, string>).Authorization,
        "Bearer circle-api-key",
      );

      return new Response(
        JSON.stringify({
          data: {
            wallets: [
              {
                id: "circle-wallet-1",
                walletSetId: "circle-wallet-set",
                blockchain: "BASE-SEPOLIA",
                address: "0x3333333333333333333333333333333333333333",
                name: "Research Summary",
                refId: "research-summary",
              },
            ],
          },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    },
  });

  const result = await service.listWallets();

  assert.deepEqual(result, [
    {
      agentName: "Research Summary",
      circleWalletId: "circle-wallet-1",
      circleWalletSetId: "circle-wallet-set",
      blockchain: "BASE-SEPOLIA",
      walletAddress: "0x3333333333333333333333333333333333333333",
      mode: "circle",
    },
  ]);
});
