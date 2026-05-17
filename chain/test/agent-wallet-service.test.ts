import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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

test("AgentWalletService stores agent identity binding when reusing an existing wallet", async () => {
  await withTempStateFile(
    {
      wallets: [],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "true",
        AGENT_WALLET_STATE_PATH: statePath,
      });
      const circleWalletService = {
        createWallet: async () => {
          throw new Error("createWallet should not be called");
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.getOrCreate({
        agentName: "ZeroClaw EigenFlux Peer",
        agentId: "312877741349273600",
        email: "XW007120@163.COM",
        walletAddress: "0x3333333333333333333333333333333333333333",
      });

      assert.equal(result.reused, true);
      assert.equal(result.binding?.agentName, "ZeroClaw EigenFlux Peer");
      assert.equal(result.binding?.agentId, "312877741349273600");
      assert.equal(result.binding?.email, "xw007120@163.com");
      assert.equal(result.binding?.walletAddress, "0x3333333333333333333333333333333333333333");

      const state = JSON.parse(await readFile(statePath, "utf-8")) as {
        agentWalletBindings: unknown[];
      };
      assert.equal(state.agentWalletBindings.length, 1);
      assert.deepEqual(state.agentWalletBindings[0], result.binding);
    },
  );
});

test("AgentWalletService allows multiple agent bindings to share one wallet address", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "ZeroClaw Chief Agent",
          agentId: "312586087945994240",
          email: "xw007110@163.com",
          walletAddress: "0x3333333333333333333333333333333333333333",
          circleWalletId: null,
          circleWalletSetId: "mock-circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "mock",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
      ],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "true",
        AGENT_WALLET_STATE_PATH: statePath,
      });
      const service = new AgentWalletService(config, fakeX402FetchService(baseX402Result()));

      await service.getOrCreate({
        agentName: "ZeroClaw EigenFlux Peer",
        agentId: "312877741349273600",
        email: "xw007120@163.com",
        walletAddress: "0x3333333333333333333333333333333333333333",
      });

      const state = JSON.parse(await readFile(statePath, "utf-8")) as {
        agentWalletBindings: Array<{ agentName: string; walletAddress: string }>;
      };
      assert.equal(state.agentWalletBindings.length, 2);
      assert.deepEqual(
        state.agentWalletBindings.map((binding) => binding.agentName).sort(),
        ["ZeroClaw Chief Agent", "ZeroClaw EigenFlux Peer"],
      );
      assert.ok(
        state.agentWalletBindings.every(
          (binding) => binding.walletAddress === "0x3333333333333333333333333333333333333333",
        ),
      );
    },
  );
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

test("AgentWalletService stores agent identity binding when creating a wallet", async () => {
  await withTempStateFile(
    {
      wallets: [],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "true",
        AGENT_WALLET_STATE_PATH: statePath,
      });
      const circleWalletService = {
        createWallet: async () => ({
          circleWalletId: "new-wallet",
          circleWalletSetId: "new-wallet-set",
          blockchain: "BASE-SEPOLIA" as const,
          walletAddress: "0x2222222222222222222222222222222222222222",
          mode: "mock" as const,
        }),
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.getOrCreate({
        agentName: "ZeroClaw EigenFlux Peer",
        agentId: "312877741349273600",
        email: "xw007120@163.com",
      });

      assert.equal(result.reused, false);
      assert.equal(result.binding?.circleWalletId, "new-wallet");
      assert.equal(result.binding?.circleWalletSetId, "new-wallet-set");
      assert.equal(result.binding?.walletAddress, "0x2222222222222222222222222222222222222222");

      const state = JSON.parse(await readFile(statePath, "utf-8")) as {
        agentWalletBindings: unknown[];
      };
      assert.deepEqual(state.agentWalletBindings[0], result.binding);
    },
  );
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

test("AgentWalletService imports and assigns an unused Circle wallet before creating one", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "Existing Agent",
          agentId: "agent_existing",
          email: "existing@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "used-circle-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
      ],
    },
    async (statePath) => {
      const config = liveCircleConfig({
        AGENT_WALLET_STATE_PATH: statePath,
      });
      let listCalls = 0;
      let createCalls = 0;
      const circleWalletService = {
        listWallets: async () => {
          listCalls += 1;
          return [
            {
              agentName: "Existing Agent",
              circleWalletId: "used-circle-wallet",
              circleWalletSetId: "circle-wallet-set",
              blockchain: "BASE-SEPOLIA" as const,
              walletAddress: "0x1111111111111111111111111111111111111111",
              mode: "circle" as const,
            },
            {
              agentName: "Imported Spare",
              circleWalletId: "unused-circle-wallet",
              circleWalletSetId: "circle-wallet-set",
              blockchain: "BASE-SEPOLIA" as const,
              walletAddress: "0x2222222222222222222222222222222222222222",
              mode: "circle" as const,
            },
          ];
        },
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

      const result = await service.getOrCreate({
        agentName: "New Agent",
        agentId: "agent_new",
        email: "new@example.com",
      });

      assert.equal(listCalls, 1);
      assert.equal(createCalls, 0);
      assert.equal(result.reused, true);
      assert.equal(result.circleWalletId, "unused-circle-wallet");
      assert.equal(result.walletAddress, "0x2222222222222222222222222222222222222222");
      assert.equal(result.binding?.agentName, "New Agent");
      assert.equal(result.binding?.agentId, "agent_new");

      const state = JSON.parse(await readFile(statePath, "utf-8")) as {
        wallets: unknown[];
        agentWalletBindings: Array<{ agentName: string; circleWalletId: string }>;
      };
      assert.equal(state.wallets.length, 2);
      assert.deepEqual(
        state.agentWalletBindings.map((binding) => binding.circleWalletId).sort(),
        ["unused-circle-wallet", "used-circle-wallet"],
      );
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

test("AgentWalletService creates Circle transfer between bound agents", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "ZeroClaw Chief Agent",
          agentId: "main-agent",
          email: "main@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "circle-main",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
        {
          agentName: "ZeroClaw EigenFlux Peer",
          agentId: "peer-agent",
          email: "peer@example.com",
          walletAddress: "0x2222222222222222222222222222222222222222",
          circleWalletId: "circle-peer",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
      ],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "false",
        AGENT_WALLET_STATE_PATH: statePath,
        CIRCLE_API_KEY: "circle-api-key",
        CIRCLE_ENTITY_SECRET: "entity-secret",
        CIRCLE_WALLET_SET_ID: "circle-wallet-set",
      });
      const circleWalletService = new CircleWalletService(config, {
        client: {
          createTransaction: async (input: unknown) => {
            assert.deepEqual(input, {
              walletId: "circle-peer",
              destinationAddress: "0x1111111111111111111111111111111111111111",
              amount: ["0.000001"],
              tokenAddress: "",
              blockchain: "BASE-SEPOLIA",
              refId: "prepay:test",
              fee: {
                type: "level",
                config: {
                  feeLevel: "MEDIUM",
                },
              },
            });
            return {
              data: {
                transaction: {
                  id: "circle-tx-1",
                  txHash: "0xabc",
                  state: "INITIATED",
                },
              },
            };
          },
          getTransaction: async () => ({ data: {} }),
          requestTestnetTokens: async () => ({}) as never,
        },
      });
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.transfer({
        fromAgentId: "peer-agent",
        toAgentId: "main-agent",
        amountEth: "0.000001",
        refId: "prepay:test",
      });

      assert.equal(result.fromCircleWalletId, "circle-peer");
      assert.equal(result.toAddress, "0x1111111111111111111111111111111111111111");
      assert.equal(result.asset, "ETH");
      assert.equal(result.amount, "0.000001");
      assert.equal(result.amountEth, "0.000001");
      assert.equal(result.amountAtomic, null);
      assert.equal(result.tokenId, null);
      assert.equal(result.tokenAddress, "");
      assert.equal(result.transactionId, "circle-tx-1");
      assert.equal(result.transactionHash, "0xabc");
      assert.equal(result.state, "INITIATED");
      assert.equal(result.mode, "circle");
    },
  );
});

test("AgentWalletService creates Circle USDC transfer from atomic amount", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "ZeroClaw Chief Agent",
          agentId: "main-agent",
          email: "main@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "circle-main",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
        {
          agentName: "ZeroClaw EigenFlux Peer",
          agentId: "peer-agent",
          email: "peer@example.com",
          walletAddress: "0x2222222222222222222222222222222222222222",
          circleWalletId: "circle-peer",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
      ],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "false",
        AGENT_WALLET_STATE_PATH: statePath,
        CIRCLE_API_KEY: "circle-api-key",
        CIRCLE_ENTITY_SECRET: "entity-secret",
        CIRCLE_WALLET_SET_ID: "circle-wallet-set",
        X402_USDC_ASSET_ADDRESS: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        CIRCLE_USDC_TOKEN_ID: "circle-usdc-token",
      });
      const circleWalletService = new CircleWalletService(config, {
        client: {
          createTransaction: async (input: unknown) => {
            assert.deepEqual(input, {
              walletId: "circle-peer",
              destinationAddress: "0x1111111111111111111111111111111111111111",
              amount: ["1.25"],
              tokenId: "circle-usdc-token",
              refId: "escrow:test:release",
              fee: {
                type: "level",
                config: {
                  feeLevel: "MEDIUM",
                },
              },
            });
            return {
              data: {
                transaction: {
                  id: "circle-usdc-tx-1",
                  transactionHash: "0xdef",
                  state: "INITIATED",
                },
              },
            };
          },
          getTransaction: async () => ({ data: {} }),
          requestTestnetTokens: async () => ({}) as never,
        },
      });
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.transfer({
        fromAgentId: "peer-agent",
        toAgentId: "main-agent",
        amountAtomic: "1250000",
        asset: "USDC",
        refId: "escrow:test:release",
      });

      assert.equal(result.asset, "USDC");
      assert.equal(result.amount, "1.25");
      assert.equal(result.amountEth, null);
      assert.equal(result.amountAtomic, "1250000");
      assert.equal(result.tokenId, "circle-usdc-token");
      assert.equal(result.tokenAddress, "0x036cbd53842c5426634e7929541ec2318f3dcf7e");
      assert.equal(result.transactionId, "circle-usdc-tx-1");
      assert.equal(result.transactionHash, "0xdef");
    },
  );
});

test("AgentWalletService rejects Circle transfer without real source wallet id", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "ZeroClaw EigenFlux Peer",
          agentId: "peer-agent",
          email: "peer@example.com",
          walletAddress: "0x2222222222222222222222222222222222222222",
          circleWalletId: null,
          circleWalletSetId: "mock-circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "mock",
          updatedAt: "2026-05-13T00:00:00.000Z",
        },
      ],
    },
    async (statePath) => {
      const config = loadConfig({
        CHAIN_MOCK: "true",
        AGENT_WALLET_STATE_PATH: statePath,
      });
      const service = new AgentWalletService(config, fakeX402FetchService(baseX402Result()));

      await assertRejectsWithAppError(
        () =>
          service.transfer({
            fromAgentId: "peer-agent",
            toAddress: "0x1111111111111111111111111111111111111111",
            amountEth: "0.000001",
          }),
        {
          code: "VALIDATION_ERROR",
          statusCode: 400,
          message: /real Circle wallet id/,
        },
      );
    },
  );
});

test("AgentWalletService returns Circle transaction status", async () => {
  const config = loadConfig({
    CHAIN_MOCK: "false",
    CIRCLE_API_KEY: "circle-api-key",
    CIRCLE_ENTITY_SECRET: "entity-secret",
  });
  const circleWalletService = new CircleWalletService(config, {
    client: {
      createTransaction: async () => ({ data: {} }),
      getTransaction: async (input: unknown) => {
        assert.deepEqual(input, { id: "circle-tx-1" });
        return {
          data: {
            transaction: {
              id: "circle-tx-1",
              txHash: "0xabc",
              state: "COMPLETE",
            },
          },
        };
      },
      requestTestnetTokens: async () => ({}) as never,
    },
  });
  const service = new AgentWalletService(
    config,
    fakeX402FetchService(baseX402Result()),
    circleWalletService,
  );

  const result = await service.transactionStatus({ transactionId: "circle-tx-1" });

  assert.equal(result.transactionId, "circle-tx-1");
  assert.equal(result.transactionHash, "0xabc");
  assert.equal(result.state, "COMPLETE");
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
