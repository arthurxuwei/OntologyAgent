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

test("AgentWalletService treats null optional wallet fields as absent", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [],
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
        agentName: "Debug Agent",
        agentId: "debug-agent",
        email: "debug@example.com",
        walletAddress: null,
        circleWalletId: null,
        agentDescription: null,
      } as any);

      assert.equal(createCalls, 1);
      assert.equal(result.reused, false);
      assert.equal(result.circleWalletId, "new-wallet");
      assert.equal(result.binding?.agentId, "debug-agent");
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
          accountType: "SCA",
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
        getWalletBalances: async () => ({}),
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

test("AgentWalletService ignores legacy EOA local wallets before creating SCA wallet", async () => {
  await withTempStateFile(
    {
      wallets: [
        {
          agentName: "Research Summary",
          circleWalletId: "legacy-eoa-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          walletAddress: "0x1111111111111111111111111111111111111111",
          mode: "circle",
          accountType: "EOA",
        },
      ],
      agentWalletBindings: [],
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
          return {
            circleWalletId: "new-sca-wallet",
            circleWalletSetId: "circle-wallet-set",
            blockchain: "BASE-SEPOLIA" as const,
            walletAddress: "0x2222222222222222222222222222222222222222",
            mode: "circle" as const,
            accountType: "SCA" as const,
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
        agentId: "agent_research",
        email: "research@example.com",
      });

      assert.equal(createCalls, 1);
      assert.equal(result.reused, false);
      assert.equal(result.circleWalletId, "new-sca-wallet");
      assert.equal(result.binding?.circleWalletId, "new-sca-wallet");
      assert.equal(result.binding?.accountType, "SCA");
    },
  );
});

test("AgentWalletService ignores legacy non-SCA bindings before creating SCA wallet", async () => {
  await withTempStateFile(
    {
      wallets: [
        {
          agentName: "Research Summary",
          circleWalletId: "legacy-eoa-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          walletAddress: "0x1111111111111111111111111111111111111111",
          mode: "circle",
          accountType: "EOA",
        },
      ],
      agentWalletBindings: [
        {
          agentName: "Research Summary",
          agentId: "agent_research",
          email: "research@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "legacy-eoa-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          updatedAt: "2026-05-01T00:00:00.000Z",
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
          return {
            circleWalletId: "new-sca-wallet",
            circleWalletSetId: "circle-wallet-set",
            blockchain: "BASE-SEPOLIA" as const,
            walletAddress: "0x2222222222222222222222222222222222222222",
            mode: "circle" as const,
            accountType: "SCA" as const,
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
        agentId: "agent_research",
        email: "research@example.com",
      });

      assert.equal(createCalls, 1);
      assert.equal(result.reused, false);
      assert.equal(result.circleWalletId, "new-sca-wallet");
      assert.equal(result.binding?.circleWalletId, "new-sca-wallet");
      assert.equal(result.binding?.accountType, "SCA");
    },
  );
});

test("AgentWalletService includes live Circle balances when reusing a local wallet", async () => {
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
          accountType: "SCA",
        },
      ],
    },
    async (statePath) => {
      const config = liveCircleConfig({
        AGENT_WALLET_STATE_PATH: statePath,
      });
      const circleWalletService = {
        getWalletBalances: async (circleWalletId: string) => {
          assert.equal(circleWalletId, "circle-wallet-1");
          return { USDC: "1.98" };
        },
        getGatewayBalance: async (walletAddress: string, domain: number) => {
          assert.equal(walletAddress, "0x3333333333333333333333333333333333333333");
          assert.equal(typeof domain, "number");
          return {
            total: 1250000n,
            available: 750000n,
            withdrawing: 500000n,
            withdrawable: 500000n,
            pendingDeposits: 2250000n,
            formattedTotal: "1.25",
            formattedAvailable: "0.75",
            formattedWithdrawing: "0.5",
            formattedWithdrawable: "0.5",
            formattedPendingDeposits: "2.25",
          };
        },
        createWallet: async () => {
          throw new Error("createWallet should not be called");
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.getOrCreate({ agentName: " Research Summary " });

      assert.equal(result.reused, true);
      assert.deepEqual(result.balances, { USDC: "1.98" });
      assert.deepEqual(result.gatewayBalance, {
        availableAtomic: "750000",
        totalAtomic: "1250000",
        withdrawingAtomic: "500000",
        withdrawableAtomic: "500000",
        pendingDepositsAtomic: "2250000",
        formattedAvailable: "0.75",
        formattedTotal: "1.25",
        formattedWithdrawing: "0.5",
        formattedWithdrawable: "0.5",
        formattedPendingDeposits: "2.25",
      });
    },
  );
});

test("AgentWalletService status resolves existing binding before mock fallback", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "Research Summary",
          agentId: "agent_research",
          email: "research@example.com",
          circleWalletId: "circle-wallet-1",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          walletAddress: "0x3333333333333333333333333333333333333333",
          mode: "circle",
          accountType: "SCA",
          updatedAt: "2026-05-21T00:00:00.000Z",
        },
      ],
    },
    async (statePath) => {
      const config = liveCircleConfig({
        AGENT_WALLET_STATE_PATH: statePath,
      });
      const circleWalletService = {
        getWalletBalances: async (circleWalletId: string) => {
          assert.equal(circleWalletId, "circle-wallet-1");
          return { USDC: "3" };
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.status({ circleWalletId: "circle-wallet-1" });

      assert.equal(result.mode, "circle");
      assert.equal(result.circleWalletId, "circle-wallet-1");
      assert.deepEqual(result.balances, { USDC: "3" });
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
              accountType: "SCA" as const,
            },
          ];
        },
        createWallet: async () => {
          createCalls += 1;
          throw new Error("createWallet should not be called");
        },
        getWalletBalances: async () => ({}),
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

test("AgentWalletService skips unused non-SCA Circle wallets before creating one", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [],
    },
    async (statePath) => {
      const config = liveCircleConfig({
        AGENT_WALLET_STATE_PATH: statePath,
      });
      let createCalls = 0;
      const circleWalletService = {
        listWallets: async () => [
          {
            agentName: "Legacy Spare",
            circleWalletId: "legacy-eoa-wallet",
            circleWalletSetId: "circle-wallet-set",
            blockchain: "BASE-SEPOLIA" as const,
            walletAddress: "0x1111111111111111111111111111111111111111",
            mode: "circle" as const,
            accountType: "EOA" as const,
          },
        ],
        createWallet: async () => {
          createCalls += 1;
          return {
            circleWalletId: "new-sca-wallet",
            circleWalletSetId: "circle-wallet-set",
            blockchain: "BASE-SEPOLIA" as const,
            walletAddress: "0x2222222222222222222222222222222222222222",
            mode: "circle" as const,
          };
        },
        getWalletBalances: async () => ({}),
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

      assert.equal(createCalls, 1);
      assert.equal(result.reused, false);
      assert.equal(result.circleWalletId, "new-sca-wallet");
      assert.equal(result.walletAddress, "0x2222222222222222222222222222222222222222");
    },
  );
});

test("AgentWalletService reuses an existing binding by agent id before importing unused wallets", async () => {
  await withTempStateFile(
    {
      wallets: [
        {
          agentName: "Original Agent Name",
          circleWalletId: "bound-circle-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          walletAddress: "0x1111111111111111111111111111111111111111",
          mode: "circle",
          accountType: "SCA",
        },
        {
          agentName: "Imported Spare",
          circleWalletId: "unused-circle-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          walletAddress: "0x2222222222222222222222222222222222222222",
          mode: "circle",
          accountType: "SCA",
        },
      ],
      agentWalletBindings: [
        {
          agentName: "Original Agent Name",
          agentId: "agent_existing",
          email: "existing@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "bound-circle-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          accountType: "SCA",
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
          return [];
        },
        createWallet: async () => {
          createCalls += 1;
          throw new Error("createWallet should not be called");
        },
        getWalletBalances: async () => ({}),
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.getOrCreate({
        agentName: "Changed Agent Name",
        agentId: "agent_existing",
        email: "updated@example.com",
      });

      assert.equal(listCalls, 0);
      assert.equal(createCalls, 0);
      assert.equal(result.reused, true);
      assert.equal(result.circleWalletId, "bound-circle-wallet");
      assert.equal(result.walletAddress, "0x1111111111111111111111111111111111111111");
      assert.equal(result.binding?.agentName, "Changed Agent Name");
      assert.equal(result.binding?.agentId, "agent_existing");
      assert.equal(result.binding?.circleWalletId, "bound-circle-wallet");

      const state = JSON.parse(await readFile(statePath, "utf-8")) as {
        agentWalletBindings: Array<{ agentName: string; circleWalletId: string }>;
      };
      assert.deepEqual(state.agentWalletBindings, [
        {
          agentName: "Changed Agent Name",
          agentId: "agent_existing",
          email: "updated@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "bound-circle-wallet",
          circleWalletSetId: "circle-wallet-set",
          blockchain: "BASE-SEPOLIA",
          mode: "circle",
          accountType: "SCA",
          updatedAt: result.binding?.updatedAt,
        },
      ]);
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

test("AgentWalletService settles USDC transfer through Circle Gateway", async () => {
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
        X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com",
        X402_NETWORK: "eip155:84532",
        X402_USDC_ASSET_ADDRESS: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        CIRCLE_USDC_TOKEN_ID: "circle-usdc-token",
      });
      let signCalls = 0;
      let settleCalls = 0;
      const circleWalletService = {
        getGatewayBalance: async (walletAddress: string) => {
          assert.equal(walletAddress, "0x2222222222222222222222222222222222222222");
          return {
            total: 2_000_000n,
            available: 2_000_000n,
            withdrawing: 0n,
            withdrawable: 2_000_000n,
            formattedTotal: "2",
            formattedAvailable: "2",
            formattedWithdrawing: "0",
            formattedWithdrawable: "2",
          };
        },
        signTypedData: async (input: { walletId: string; data: unknown; memo?: string }) => {
          signCalls += 1;
          assert.equal(input.walletId, "circle-peer");
          assert.equal(input.memo, "escrow:test:release");
          assert.equal((input.data as { domain: { name: string } }).domain.name, "GatewayWalletBatched");
          assert.equal(
            (input.data as { domain: { verifyingContract: string } }).domain.verifyingContract.toLowerCase(),
            "0x0077777d7eba4688bdef3e311b846f25870a19b9",
          );
          return `0x${"11".repeat(65)}`;
        },
        settleGatewayPayment: async (input: {
          paymentPayload: { x402Version: number; payload: unknown };
          paymentRequirements: { amount: string; payTo: string; network: string; asset: string };
        }) => {
          settleCalls += 1;
          assert.equal(input.paymentPayload.x402Version, 2);
          assert.equal(input.paymentRequirements.amount, "1250000");
          assert.equal(input.paymentRequirements.payTo, "0x1111111111111111111111111111111111111111");
          assert.equal(input.paymentRequirements.network, "eip155:84532");
          assert.equal(input.paymentRequirements.asset, "0x036cbd53842c5426634e7929541ec2318f3dcf7e");
          return {
            verify: {
              isValid: true,
              payer: "0x2222222222222222222222222222222222222222",
            },
            settle: {
              success: true,
              payer: "0x2222222222222222222222222222222222222222",
              transaction: "0xgatewaysettlement",
              network: "eip155:84532",
            },
          };
        },
      } as unknown as CircleWalletService;
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
      assert.equal(result.tokenId, null);
      assert.equal(result.tokenAddress, "0x036cbd53842c5426634e7929541ec2318f3dcf7e");
      assert.equal(result.transactionId, "0xgatewaysettlement");
      assert.equal(result.transactionHash, "0xgatewaysettlement");
      assert.equal(result.state, "SETTLED");
      assert.equal(result.mode, "gateway");
      assert.equal(signCalls, 1);
      assert.equal(settleCalls, 1);
    },
  );
});

test("AgentWalletService uses an EOA delegate for SCA Gateway transfers", async () => {
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
          accountType: "SCA",
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
          accountType: "SCA",
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
        X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com",
        X402_NETWORK: "eip155:84532",
        X402_USDC_ASSET_ADDRESS: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        CIRCLE_USDC_TOKEN_ID: "circle-usdc-token",
      });
      let createDelegateCalls = 0;
      let addDelegateCalls = 0;
      let withdrawCalls = 0;
      const circleWalletService = {
        getGatewayBalance: async (walletAddress: string, domain: number) => {
          assert.equal(walletAddress, "0x2222222222222222222222222222222222222222");
          assert.equal(domain, 6);
          return {
            total: 2_000_000n,
            available: 2_000_000n,
            withdrawing: 0n,
            withdrawable: 2_000_000n,
            formattedTotal: "2",
            formattedAvailable: "2",
            formattedWithdrawing: "0",
            formattedWithdrawable: "2",
          };
        },
        createWallet: async (agentName: string, accountType: "SCA" | "EOA") => {
          createDelegateCalls += 1;
          assert.equal(agentName, "ZeroClaw EigenFlux Peer Gateway Delegate");
          assert.equal(accountType, "EOA");
          return {
            circleWalletId: "circle-peer-delegate",
            circleWalletSetId: "circle-wallet-set",
            blockchain: "BASE-SEPOLIA" as const,
            walletAddress: "0x3333333333333333333333333333333333333333",
            mode: "circle" as const,
            accountType: "EOA" as const,
          };
        },
        addGatewayDelegate: async (input: {
          walletId: string;
          tokenAddress: string;
          gatewayWallet: string;
          delegateAddress: string;
          refId?: string;
        }) => {
          addDelegateCalls += 1;
          assert.deepEqual(input, {
            walletId: "circle-peer",
            tokenAddress: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            gatewayWallet: "0x0077777d7eba4688bdef3e311b846f25870a19b9",
            delegateAddress: "0x3333333333333333333333333333333333333333",
            refId: "gateway-delegate:peer-agent",
          });
          return {
            transaction: { transaction: { id: "delegate-tx", state: "INITIATED" } },
            transactionFinal: { transaction: { id: "delegate-tx", state: "COMPLETE" } },
          };
        },
        withdrawFromGateway: async (input: {
          walletId: string;
          walletAddress: string;
          signerWalletId?: string;
          signerAddress?: string;
          recipientAddress: string;
          tokenAddress: string;
          gatewayWallet: string;
          gatewayMinter: string;
          sourceDomain: number;
          destinationDomain: number;
          amountAtomic: string;
          refId?: string;
        }) => {
          withdrawCalls += 1;
          assert.deepEqual(input, {
            walletId: "circle-peer",
            walletAddress: "0x2222222222222222222222222222222222222222",
            signerWalletId: "circle-peer-delegate",
            signerAddress: "0x3333333333333333333333333333333333333333",
            recipientAddress: "0x1111111111111111111111111111111111111111",
            tokenAddress: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            gatewayWallet: "0x0077777d7eba4688bdef3e311b846f25870a19b9",
            gatewayMinter: "0x0022222abe238cc2c7bb1f21003f0a260052475b",
            sourceDomain: 6,
            destinationDomain: 6,
            amountAtomic: "1250000",
            refId: "escrow:test:release",
          });
          return {
            gatewayTransferId: "gateway-transfer-1",
            mint: { transaction: { id: "mint-tx", txHash: "0xmint", state: "INITIATED" } },
            mintFinal: { transaction: { id: "mint-tx", txHash: "0xmint", state: "CONFIRMED" } },
          };
        },
      } as unknown as CircleWalletService;
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

      assert.equal(result.transactionId, "mint-tx");
      assert.equal(result.transactionHash, "0xmint");
      assert.equal(result.state, "CONFIRMED");
      assert.equal(result.mode, "gateway");
      assert.equal(createDelegateCalls, 1);
      assert.equal(addDelegateCalls, 1);
      assert.equal(withdrawCalls, 1);

      const state = JSON.parse(await readFile(statePath, "utf-8"));
      const peerBinding = state.agentWalletBindings.find(
        (binding: { agentId: string }) => binding.agentId === "peer-agent",
      );
      assert.equal(peerBinding.gatewayDelegateWalletId, "circle-peer-delegate");
      assert.equal(peerBinding.gatewayDelegateAddress, "0x3333333333333333333333333333333333333333");
    },
  );
});

test("AgentWalletService withdraws USDC from Circle wallet without Gateway balance", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
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
        CIRCLE_USDC_TOKEN_ID: "circle-usdc-token",
      });
      let createTransferCalls = 0;
      const circleWalletService = {
        createTransfer: async (input: unknown) => {
          createTransferCalls += 1;
          assert.deepEqual(input, {
            walletId: "circle-peer",
            destinationAddress: "0x1111111111111111111111111111111111111111",
            amount: "5",
            tokenId: "circle-usdc-token",
            tokenAddress: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            refId: "withdrawal:test",
          });
          return {
            transaction: {
              id: "circle-withdrawal-1",
              txHash: "0xwithdrawal",
              state: "INITIATED",
            },
          };
        },
        getGatewayBalance: async () => {
          throw new Error("withdrawal must not read Gateway balance");
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.withdrawUsdc({
        fromAgentId: "peer-agent",
        toAddress: "0x1111111111111111111111111111111111111111",
        amountAtomic: "5000000",
        refId: "withdrawal:test",
      });

      assert.equal(createTransferCalls, 1);
      assert.equal(result.asset, "USDC");
      assert.equal(result.amount, "5");
      assert.equal(result.amountEth, null);
      assert.equal(result.amountAtomic, "5000000");
      assert.equal(result.tokenId, "circle-usdc-token");
      assert.equal(result.transactionId, "circle-withdrawal-1");
      assert.equal(result.transactionHash, "0xwithdrawal");
      assert.equal(result.state, "INITIATED");
      assert.equal(result.mode, "circle");
    },
  );
});

test("AgentWalletService rejects USDC transfer when Gateway balance is insufficient", async () => {
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
      const config = liveCircleConfig({
        AGENT_WALLET_STATE_PATH: statePath,
        X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com",
      });
      let signCalls = 0;
      let settleCalls = 0;
      const circleWalletService = {
        getGatewayBalance: async () => ({
          total: 999n,
          available: 999n,
          withdrawing: 0n,
          withdrawable: 999n,
          formattedTotal: "0.000999",
          formattedAvailable: "0.000999",
          formattedWithdrawing: "0",
          formattedWithdrawable: "0.000999",
        }),
        signTypedData: async () => {
          signCalls += 1;
          return `0x${"11".repeat(65)}`;
        },
        settleGatewayPayment: async () => {
          settleCalls += 1;
          return {};
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      await assertRejectsWithAppError(
        () =>
          service.transfer({
            fromAgentId: "peer-agent",
            toAgentId: "main-agent",
            amountAtomic: "1000",
            asset: "USDC",
          }),
        {
          code: "INSUFFICIENT_GATEWAY_BALANCE",
          statusCode: 424,
          message: /Gateway available balance is insufficient/,
        },
      );
      assert.equal(signCalls, 0);
      assert.equal(settleCalls, 0);
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

test("AgentWalletService deposits USDC into Circle Gateway", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
        {
          agentName: "ZeroClaw OntologyAgent",
          agentId: "main-agent",
          email: "main@example.com",
          walletAddress: "0x1111111111111111111111111111111111111111",
          circleWalletId: "circle-main",
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
        X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com",
        X402_NETWORK: "eip155:84532",
      });
      const circleWalletService = {
        depositToGateway: async (input: {
          walletId: string;
          walletAddress: string;
          tokenAddress: string;
          gatewayWallet: string;
          amountAtomic: string;
          refId?: string;
        }) => {
          assert.deepEqual(input, {
            walletId: "circle-main",
            walletAddress: "0x1111111111111111111111111111111111111111",
            tokenAddress: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            gatewayWallet: "0x0077777d7eba4688bdef3e311b846f25870a19b9",
            amountAtomic: "1000",
            refId: "deposit:test",
          });
          return {
            approval: { transaction: { id: "approve-tx", state: "COMPLETE" } },
            approvalFinal: { transaction: { id: "approve-tx", state: "COMPLETE" } },
            deposit: { transaction: { id: "deposit-tx", state: "COMPLETE" } },
            depositFinal: { transaction: { id: "deposit-tx", state: "COMPLETE" } },
          };
        },
        getGatewayBalance: async (walletAddress: string, domain: number) => {
          assert.equal(walletAddress, "0x1111111111111111111111111111111111111111");
          assert.equal(domain, 6);
          return {
            total: 1000n,
            available: 1000n,
            withdrawing: 0n,
            withdrawable: 1000n,
            formattedTotal: "0.001",
            formattedAvailable: "0.001",
            formattedWithdrawing: "0",
            formattedWithdrawable: "0.001",
          };
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.depositToGateway({
        agentId: "main-agent",
        amountAtomic: "1000",
        refId: "deposit:test",
      });

      assert.equal(result.circleWalletId, "circle-main");
      assert.equal(result.amount, "0.001");
      assert.equal(result.approvalTransactionId, "approve-tx");
      assert.equal(result.depositTransactionId, "deposit-tx");
      assert.equal(result.gatewayBalance.availableAtomic, "1000");
      assert.equal(result.mode, "gateway_deposit");
    },
  );
});

test("AgentWalletService withdraws USDC from Circle Gateway", async () => {
  await withTempStateFile(
    {
      wallets: [],
      agentWalletBindings: [
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
      const config = liveCircleConfig({
        AGENT_WALLET_STATE_PATH: statePath,
        X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com",
        X402_NETWORK: "eip155:84532",
      });
      let balanceCalls = 0;
      const circleWalletService = {
        withdrawFromGateway: async (input: {
          walletId: string;
          walletAddress: string;
          recipientAddress: string;
          tokenAddress: string;
          gatewayWallet: string;
          gatewayMinter: string;
          sourceDomain: number;
          destinationDomain: number;
          amountAtomic: string;
          refId?: string;
        }) => {
          assert.deepEqual(input, {
            walletId: "circle-peer",
            walletAddress: "0x2222222222222222222222222222222222222222",
            recipientAddress: "0x1111111111111111111111111111111111111111",
            tokenAddress: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            gatewayWallet: "0x0077777d7eba4688bdef3e311b846f25870a19b9",
            gatewayMinter: "0x0022222abe238cc2c7bb1f21003f0a260052475b",
            sourceDomain: 6,
            destinationDomain: 6,
            amountAtomic: "1000",
            refId: "withdraw:test",
          });
          return {
            gatewayTransferId: "gateway-transfer-1",
            mint: { transaction: { id: "mint-tx", txHash: "0xmint", state: "INITIATED" } },
            mintFinal: { transaction: { id: "mint-tx", txHash: "0xmint", state: "CONFIRMED" } },
          };
        },
        getGatewayBalance: async (walletAddress: string, domain: number) => {
          balanceCalls += 1;
          assert.equal(walletAddress, "0x2222222222222222222222222222222222222222");
          assert.equal(domain, 6);
          return {
            total: balanceCalls === 1 ? 1000n : 0n,
            available: balanceCalls === 1 ? 1000n : 0n,
            withdrawing: 0n,
            withdrawable: balanceCalls === 1 ? 1000n : 0n,
            formattedTotal: balanceCalls === 1 ? "0.001" : "0",
            formattedAvailable: balanceCalls === 1 ? "0.001" : "0",
            formattedWithdrawing: "0",
            formattedWithdrawable: balanceCalls === 1 ? "0.001" : "0",
          };
        },
      } as unknown as CircleWalletService;
      const service = new AgentWalletService(
        config,
        fakeX402FetchService(baseX402Result()),
        circleWalletService,
      );

      const result = await service.withdrawFromGateway({
        agentId: "peer-agent",
        recipientAddress: "0x1111111111111111111111111111111111111111",
        amountAtomic: "1000",
        refId: "withdraw:test",
      });

      assert.equal(result.circleWalletId, "circle-peer");
      assert.equal(result.amount, "0.001");
      assert.equal(result.gatewayTransferId, "gateway-transfer-1");
      assert.equal(result.mintTransactionId, "mint-tx");
      assert.equal(result.mintTransactionHash, "0xmint");
      assert.equal(result.mintState, "CONFIRMED");
      assert.equal(result.gatewayBalance.availableAtomic, "0");
      assert.equal(result.mode, "gateway_withdraw");
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
  assert.equal(requestBody?.accountType, "SCA");
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
    accountType: "SCA",
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

test("CircleWalletService returns token balances for a Circle wallet", async () => {
  const service = new CircleWalletService(liveCircleConfig(), {
    fetchImpl: async (input, init) => {
      assert.equal(input, "https://circle.test/v1/w3s/wallets/circle-wallet-1/balances");
      assert.equal(init?.method, "GET");
      assert.equal(
        (init?.headers as Record<string, string>).Authorization,
        "Bearer circle-api-key",
      );

      return new Response(
        JSON.stringify({
          data: {
            tokenBalances: [
              {
                token: {
                  symbol: "USDC",
                },
                amount: "1.98",
              },
              {
                token: {
                  symbol: "ETH-SEPOLIA",
                },
                amount: "0.000998913465464524",
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

  const result = await service.getWalletBalances("circle-wallet-1");

  assert.deepEqual(result, {
    USDC: "1.98",
    "ETH-SEPOLIA": "0.000998913465464524",
  });
});

test("CircleWalletService returns Gateway balance and pending deposits", async () => {
  const service = new CircleWalletService(
    liveCircleConfig({ X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com" }),
    {
      fetchImpl: async (input, init) => {
        assert.equal(input, "https://gateway-api-testnet.circle.com/v1/balances");
        assert.equal(init?.method, "POST");
        const body = JSON.parse(String(init?.body));
        assert.deepEqual(body.sources, [
          { depositor: "0x3333333333333333333333333333333333333333", domain: 6 },
        ]);

        return new Response(
          JSON.stringify({
            balances: [
              {
                balance: "1.25",
                withdrawing: "0.5",
                withdrawable: "0.5",
                pendingDeposits: "2.25",
              },
            ],
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        );
      },
    },
  );

  const result = await service.getGatewayBalance(
    "0x3333333333333333333333333333333333333333",
    6,
  );

  assert.equal(result.available.toString(), "1250000");
  assert.equal(result.total.toString(), "1750000");
  assert.equal(result.withdrawable.toString(), "500000");
  assert.equal(result.withdrawing.toString(), "500000");
  assert.equal(result.pendingDeposits.toString(), "2250000");
  assert.equal(result.formattedAvailable, "1.25");
  assert.equal(result.formattedPendingDeposits, "2.25");
});

test("CircleWalletService serializes bigint typed data and adds EIP712Domain for Circle signing", async () => {
  let signedPayload: unknown;
  const service = new CircleWalletService(liveCircleConfig(), {
    client: {
      createTransaction: async () => ({ data: {} }),
      getTransaction: async () => ({ data: {} }),
      requestTestnetTokens: async () => ({}) as never,
      signTypedData: async (input: unknown) => {
        signedPayload = input;
        return {
          data: {
            signature: `0x${"22".repeat(65)}`,
          },
        };
      },
    },
  });

  const signature = await service.signTypedData({
    walletId: "circle-wallet-1",
    memo: "gateway:test",
    data: {
      domain: {
        name: "GatewayWalletBatched",
        version: "1",
        chainId: 84532,
        verifyingContract: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
      },
      types: {
        TransferWithAuthorization: [{ name: "value", type: "uint256" }],
      },
      primaryType: "TransferWithAuthorization",
      message: { value: 1000n },
    },
  });

  assert.equal(signature, `0x${"22".repeat(65)}`);
  assert.ok(signedPayload && typeof signedPayload === "object");
  const request = signedPayload as { walletId?: string; memo?: string; data?: unknown };
  assert.equal(request.walletId, "circle-wallet-1");
  assert.equal(request.memo, "gateway:test");
  const signedData = JSON.parse(String(request.data));
  assert.deepEqual(signedData.types.EIP712Domain, [
    { name: "name", type: "string" },
    { name: "version", type: "string" },
    { name: "chainId", type: "uint256" },
    { name: "verifyingContract", type: "address" },
  ]);
  assert.equal(signedData.message.value, "1000");
});

test("CircleWalletService retries Gateway transfer while delegate authorization is indexing", async () => {
  let transferCalls = 0;
  const sleepDelays: number[] = [];
  const contractExecutions: unknown[] = [];
  const service = new CircleWalletService(
    liveCircleConfig({ X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com" }),
    {
      sleepImpl: async (ms: number) => {
        sleepDelays.push(ms);
      },
      fetchImpl: async (input, init) => {
        assert.equal(String(input), "https://gateway-api-testnet.circle.com/v1/transfer");
        assert.equal(init?.method, "POST");
        const requests = JSON.parse(String(init?.body));
        assert.equal(requests[0].burnIntent.spec.sourceDepositor.toLowerCase(), `0x${"0".repeat(24)}${"22".repeat(20)}`);
        assert.equal(requests[0].burnIntent.spec.sourceSigner.toLowerCase(), `0x${"0".repeat(24)}${"33".repeat(20)}`);
        transferCalls += 1;
        if (transferCalls < 3) {
          return new Response(
            JSON.stringify({
              success: false,
              message: "Signer is not authorized to spend funds from sourceDepositor",
            }),
            {
              status: 400,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        return new Response(
          JSON.stringify({
            success: true,
            id: "gateway-transfer-1",
            attestation: `0x${"44".repeat(64)}`,
            signature: `0x${"55".repeat(65)}`,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        );
      },
      client: {
        createTransaction: async () => ({ data: {} }),
        getTransaction: async () => ({
          data: {
            transaction: {
              id: "circle-mint-tx",
              txHash: "0xmint",
              state: "COMPLETE",
            },
          },
        }),
        requestTestnetTokens: async () => ({}) as never,
        signTypedData: async () => ({
          data: {
            signature: `0x${"66".repeat(65)}`,
          },
        }),
        createContractExecutionTransaction: async (input: unknown) => {
          contractExecutions.push(input);
          return {
            data: {
              transaction: {
                id: "circle-mint-tx",
                txHash: "0xmint",
                state: "INITIATED",
              },
            },
          };
        },
      },
    },
  );

  const result = await service.withdrawFromGateway({
    walletId: "circle-peer",
    walletAddress: "0x2222222222222222222222222222222222222222",
    signerWalletId: "circle-peer-delegate",
    signerAddress: "0x3333333333333333333333333333333333333333",
    recipientAddress: "0x1111111111111111111111111111111111111111",
    tokenAddress: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    gatewayWallet: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
    gatewayMinter: "0x0022222ABE238Cc2C7Bb1f21003F0a260052475B",
    sourceDomain: 6,
    destinationDomain: 6,
    amountAtomic: "10000",
    refId: "gateway:test",
  });

  assert.equal(transferCalls, 3);
  assert.deepEqual(sleepDelays, [5_000, 15_000]);
  assert.equal(result.gatewayTransferId, "gateway-transfer-1");
  assert.equal(contractExecutions.length, 1);
});

test("CircleWalletService wraps direct Gateway payloads with x402 envelope", async () => {
  const requests: Array<{ url: string; body: any }> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push({
      url,
      body: JSON.parse(String(init?.body)),
    });
    if (url.endsWith("/v1/x402/verify")) {
      return new Response(
        JSON.stringify({ isValid: true, payer: "0x1111111111111111111111111111111111111111" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    return new Response(
      JSON.stringify({
        success: true,
        payer: "0x1111111111111111111111111111111111111111",
        transaction: "0xgatewaysettlement",
        network: "eip155:84532",
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  }) as typeof fetch;

  try {
    const service = new CircleWalletService(
      liveCircleConfig({ X402_FACILITATOR_URL: "https://gateway-api-testnet.circle.com" }),
    );
    const paymentRequirements = {
      scheme: "exact",
      network: "eip155:84532",
      asset: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
      amount: "1000",
      payTo: "0x2222222222222222222222222222222222222222",
      maxTimeoutSeconds: 604800,
      extra: {
        name: "GatewayWalletBatched",
        version: "1",
        verifyingContract: "0x0077777d7eba4688bdef3e311b846f25870a19b9",
      },
    };

    const result = await service.settleGatewayPayment({
      paymentPayload: {
        x402Version: 2,
        payload: {
          signature: `0x${"33".repeat(65)}`,
        },
      },
      paymentRequirements,
    });

    assert.equal(result.verify.isValid, true);
    assert.equal(result.settle.success, true);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, "https://gateway-api-testnet.circle.com/v1/x402/verify");
    assert.deepEqual(requests[0].body.paymentPayload.resource, {
      url: "agent-wallet://gateway-transfer",
      description: "Agent Wallet Gateway transfer",
      mimeType: "application/json",
    });
    assert.deepEqual(requests[0].body.paymentPayload.accepted, paymentRequirements);
    assert.deepEqual(requests[1].body.paymentPayload.accepted, paymentRequirements);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
