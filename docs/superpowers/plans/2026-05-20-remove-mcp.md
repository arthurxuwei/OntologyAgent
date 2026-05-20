# Remove MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove MCP from the whole Chief/OntologyAgent stack and replace every active MCP service boundary with REST or the `chief` CLI.

**Architecture:** Chain and circle become Express REST services backed by their existing service classes. Ledger remains FastAPI REST, calls chain/circle through typed HTTP clients, and no longer mounts an MCP app. Agent keeps chat/session orchestration with no dynamic MCP tool discovery.

**Tech Stack:** Python 3.12, FastAPI, httpx, unittest, TypeScript, Express, node:test, Docker Compose, shell-based `chief` CLI.

---

## File Structure

### Chain And Circle

- Create `chain/src/http/result.ts`: REST response wrapper that returns success payloads and normalized error payloads.
- Create `chain/src/http/chain-app.ts`: Express app with chain REST routes.
- Create `chain/src/http/circle-app.ts`: Express app with Circle/Agent Wallet REST routes.
- Modify `chain/src/config.ts`: replace `mcp.port` with `http.port`; read `CHAIN_HTTP_PORT`.
- Modify `chain/src/index.ts`: start `createChainHttpApp`.
- Modify `chain/src/index-circle.ts`: start `createCircleHttpApp`; read `CIRCLE_HTTP_PORT`.
- Delete `chain/src/mcp/server.ts`, `chain/src/mcp/tools.ts`, `chain/src/mcp/circle-server.ts`, `chain/src/mcp/circle-tools.ts`, and `chain/src/mcp/result.ts`.
- Replace `chain/test/mcp-server.test.ts` with `chain/test/chain-http-server.test.ts`.
- Replace `chain/test/circle-mcp-server.test.ts` with `chain/test/circle-http-server.test.ts`.
- Modify `chain/package.json` and `chain/package-lock.json`: remove `@modelcontextprotocol/sdk`.

### Ledger

- Modify `ledger/main.py`: remove MCP imports, expose `POST /ledger/payment/route`, rename MCP URL config to HTTP URL config, and use REST clients.
- Create `ledger/chain_client.py`: typed async REST client for chain execution/audit calls.
- Create `ledger/circle_client.py`: typed async REST client for Circle wallet/status/gateway/settlement calls.
- Delete `ledger/mcp_tools.py`.
- Modify `ledger/requirements.txt`: remove `mcp`.
- Modify `ledger/tests/test_ledger_service.py`: rewrite MCP tests as REST tests and client tests.

### Agent

- Modify `agent/main.py`: remove MCP runtime discovery, startup discovery, runtime reload, and MCP health fields.
- Modify `agent/prompt_builder.py`: remove `SkillCatalog` dependency and return a static prompt.
- Delete `agent/mcp_runtime.py`, `agent/chain_mcp_client.py`, `agent/mcp_tool_metadata.py`, `agent/skill_loader.py`, and `agent/tool_schema.py`.
- Delete or rewrite `agent/tests/test_mcp_runtime.py`, `agent/tests/test_chain_mcp_client.py`, `agent/tests/test_skill_loader.py`, and `agent/tests/test_tool_schema.py`.
- Modify `agent/tests/test_main_tools.py`, `agent/tests/test_main_api.py`, `agent/tests/test_clean_core.py`, and `agent/tests/test_prompt_builder.py`.
- Modify `agent/requirements.txt`: remove `mcp`.

### Compose, Docs, Install Kit

- Modify `/Users/freedom/cc/OntologyAgent/docker-compose.yml`, `docker-compose.core.yml`, and `docker-compose.cloudflare.yml` if they mention MCP.
- Modify `/Users/freedom/cc/OntologyAgent/AGENTS.md` and `README.md`.
- Modify `/Users/freedom/cc/chief-install/bin/chief`, `/Users/freedom/cc/chief-install/INSTALL.md`, and `/Users/freedom/cc/chief-install/README.md`.
- Review `/Users/freedom/cc/chief-install/skills/chief-ledger/SKILL.md` and `/Users/freedom/cc/chief-install/skills/chief-a2a-service-trade/SKILL.md`; edit only if active MCP language appears.

---

## Task 1: Convert Chain MCP Surface To Chain REST

**Files:**
- Create: `chain/src/http/result.ts`
- Create: `chain/src/http/chain-app.ts`
- Modify: `chain/src/config.ts`
- Modify: `chain/src/index.ts`
- Replace: `chain/test/mcp-server.test.ts` -> `chain/test/chain-http-server.test.ts`

- [ ] **Step 1: Write the failing REST tests**

Move `chain/test/mcp-server.test.ts` to `chain/test/chain-http-server.test.ts` and replace MCP client setup with Express request helpers.

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { loadConfig } from "../src/config.js";
import { createChainHttpApp, createChainRuntime } from "../src/http/chain-app.js";
import type { X402FetchService } from "../src/services/x402-fetch-service.js";

type RuntimeOverrides = NonNullable<Parameters<typeof createChainRuntime>[1]>;

function createMockConfig() {
  return loadConfig({
    CHAIN_MOCK: "true",
    CHAIN_MOCK_USDC_BALANCE: "321.123456",
    PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
    WHITELISTED_RECIPIENTS:
      "0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333",
  });
}

async function request(
  path: string,
  options: RequestInit = {},
  overrides?: RuntimeOverrides,
) {
  const { app } = createChainHttpApp(createMockConfig(), overrides);
  const server = createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.equal(typeof address, "object");
  assert.notEqual(address, null);
  try {
    return await fetch(`http://127.0.0.1:${address.port}${path}`, {
      ...options,
      headers: {
        "content-type": "application/json",
        ...(options.headers ?? {}),
      },
    });
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
}

test("chain REST health returns service metadata", async () => {
  const response = await request("/health");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.service, "chief-chain");
  assert.equal(body.status, "ok");
  assert.equal(body.mockChain, true);
});

test("chain REST returns the configured mock wallet balance", async () => {
  const response = await request("/chain/wallet-state");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.wallet?.mockChain, true);
  assert.equal(body.wallet?.balanceEth, "1.0");
  assert.equal(body.wallet?.usdcBalanceAtomic, "321123456");
  assert.equal(body.policy?.dailyLimitUsdcAtomic, "2000000");
});

test("chain REST signs transfers", async () => {
  const response = await request("/chain/transfers/sign", {
    method: "POST",
    body: JSON.stringify({
      to: "0x2222222222222222222222222222222222222222",
      amountEth: "0.01",
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.transfer?.to, "0x2222222222222222222222222222222222222222");
  assert.equal(body.settlement?.kind, "signed");
});

test("chain REST submits executions", async () => {
  const response = await request("/chain/executions", {
    method: "POST",
    body: JSON.stringify({
      to: "0x3333333333333333333333333333333333333333",
      valueEth: "0.001",
      data: "0x",
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.execution?.to, "0x3333333333333333333333333333333333333333");
  assert.equal(body.settlement?.kind, "submitted");
});

test("chain REST submits user operations", async () => {
  const response = await request("/chain/user-operations", {
    method: "POST",
    body: JSON.stringify({
      target: "0x3333333333333333333333333333333333333333",
      maxCostEth: "0.01",
      raw: { sender: "0x123" },
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.match(body.userOperation?.userOpHash, /^0xmock_userop_/);
});

test("chain REST returns transaction receipt status", async () => {
  const response = await request("/chain/transactions/0xexec123");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.txHash, "0xexec123");
  assert.equal(body.status, "success");
  assert.equal(body.mode, "mock");
});

test("chain REST returns pending when receipt is missing", async () => {
  const response = await request(
    "/chain/transactions/0xpending123",
    {},
    {
      transactionReceiptService: {
        execute: async (txHash: string) => ({
          txHash,
          found: false,
          finalized: false,
          success: false,
          status: "pending" as const,
          blockNumber: null,
          receipt: null,
          mode: "network" as const,
        }),
      } as RuntimeOverrides["transactionReceiptService"],
    },
  );
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "pending");
  assert.equal(body.receipt, null);
});

test("chain REST returns user operation status", async () => {
  const response = await request("/chain/user-operations/0xmock_userop_123");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.userOpHash, "0xmock_userop_123");
  assert.equal(body.status, "success");
});

test("chain REST performs x402 fetches", async () => {
  const response = await request(
    "/x402/fetch",
    {
      method: "POST",
      body: JSON.stringify({
        url: "http://x402-seller:8000/x402/demo-resource",
        method: "GET",
      }),
    },
    {
      x402FetchService: {
        execute: async () => ({
          upstream: { status: 200, contentType: "application/json", payload: { ok: true } },
          payment: {
            requiredVersion: 2,
            selected: {
              scheme: "exact",
              network: "eip155:84532",
              asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
              amount: "10000",
              payTo: "0x3333333333333333333333333333333333333333",
              maxTimeoutSeconds: 300,
              extra: { name: "USDC", version: "2" },
            },
            response: { success: true, transaction: "0xsettled", network: "eip155:84532" },
          },
          decision: {
            action: "x402-fetch" as const,
            normalizedTo: "0x3333333333333333333333333333333333333333",
            network: "eip155:84532",
            asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            amountAtomic: "10000",
            allowed: true as const,
          },
          policy: {
            dayKey: "2026-03-26",
            spentTodayWei: "0",
            dailyLimitWei: "2000000000000000000",
            spentTodayUsdcAtomic: "10000",
            dailyLimitUsdcAtomic: "2000000",
          },
        }),
      } as unknown as X402FetchService,
    },
  );
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.payment?.selected?.network, "eip155:84532");
  assert.equal(body.upstream?.status, 200);
});
```

- [ ] **Step 2: Run the chain REST test to verify it fails**

Run:

```bash
cd chain && npm test -- test/chain-http-server.test.ts
```

Expected: FAIL because `../src/http/chain-app.js` does not exist.

- [ ] **Step 3: Implement the REST response wrapper**

Create `chain/src/http/result.ts`:

```ts
import type { Request, Response } from "express";
import { normalizeError } from "../domain/errors.js";

export async function sendResult<Result>(
  res: Response,
  handler: () => Promise<Result>,
): Promise<void> {
  try {
    res.status(200).json(await handler());
  } catch (error) {
    const normalized = normalizeError(error);
    res.status(400).json({
      error: {
        code: normalized.code,
        message: normalized.message,
        details: normalized.details,
      },
    });
  }
}

export function asyncRoute(
  handler: (req: Request, res: Response) => Promise<void>,
) {
  return (req: Request, res: Response) => {
    void handler(req, res);
  };
}
```

- [ ] **Step 4: Implement `createChainHttpApp`**

Create `chain/src/http/chain-app.ts`:

```ts
import express from "express";

import { createTransactionSender } from "../chain-executor.js";
import { loadConfig, type AppConfig } from "../config.js";
import { BundlerClient } from "../erc4337.js";
import { NetworkClient } from "../infra/network-client.js";
import { PrivateKeySigner } from "../infra/signers/private-key-signer.js";
import { PolicyGuard } from "../policies/policy-guard.js";
import { ExecutionService } from "../services/execution-service.js";
import { SettlementService } from "../services/settlement-service.js";
import { SignTransferService } from "../services/sign-transfer-service.js";
import { TransactionReceiptService } from "../services/transaction-receipt-service.js";
import { UserOperationStatusService } from "../services/user-operation-status-service.js";
import { UserOperationService } from "../services/user-operation-service.js";
import { WalletStateService } from "../services/wallet-state-service.js";
import { X402FetchService } from "../services/x402-fetch-service.js";
import { asyncRoute, sendResult } from "./result.js";

type ChainRuntime = {
  walletStateService: WalletStateService;
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  transactionReceiptService: TransactionReceiptService;
  userOperationStatusService: UserOperationStatusService;
  userOperationService: UserOperationService;
  x402FetchService: X402FetchService;
};

type ChainRuntimeOverrides = {
  transactionReceiptService?: TransactionReceiptService;
  userOperationStatusService?: UserOperationStatusService;
  x402FetchService?: X402FetchService;
};

export function createChainRuntime(
  config: AppConfig = loadConfig(),
  overrides?: ChainRuntimeOverrides,
): ChainRuntime {
  const networkClient = config.network.mockChain ? null : new NetworkClient(config.network);
  const signer = config.signer.privateKey
    ? new PrivateKeySigner(config.signer, networkClient ?? new NetworkClient(config.network))
    : null;
  const sender = createTransactionSender(config);
  const bundlerClient = new BundlerClient(config);
  const policyGuard = new PolicyGuard(config.policy, config.x402);
  const settlementService = new SettlementService();
  const x402FetchService =
    overrides?.x402FetchService ?? new X402FetchService(config, policyGuard);

  return {
    walletStateService: new WalletStateService(config, policyGuard, networkClient, signer),
    signTransferService: new SignTransferService(sender, policyGuard, settlementService),
    executionService: new ExecutionService(sender, policyGuard, settlementService),
    transactionReceiptService:
      overrides?.transactionReceiptService ?? new TransactionReceiptService(config, networkClient),
    userOperationStatusService:
      overrides?.userOperationStatusService ??
      new UserOperationStatusService(config, bundlerClient),
    userOperationService: new UserOperationService(bundlerClient, policyGuard, settlementService),
    x402FetchService,
  };
}

export function createChainHttpApp(
  config: AppConfig = loadConfig(),
  overrides?: ChainRuntimeOverrides,
) {
  const runtime = createChainRuntime(config, overrides);
  const app = express();
  app.use(express.json({ limit: "1mb" }));

  app.get("/health", (_req, res) => {
    res.json({
      service: "chief-chain",
      status: "ok",
      mockChain: config.network.mockChain,
      rpcUrl: config.network.rpcUrl,
      expectedChainId: config.network.expectedChainId,
      x402Network: config.x402.network,
      x402FacilitatorUrl: config.x402.facilitatorUrl,
    });
  });

  app.get("/chain/wallet-state", asyncRoute(async (_req, res) => {
    await sendResult(res, () => runtime.walletStateService.execute());
  }));

  app.post("/chain/transfers/sign", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.signTransferService.execute(req.body));
  }));

  app.post("/chain/executions", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.executionService.execute(req.body));
  }));

  app.post("/chain/user-operations", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.userOperationService.execute(req.body));
  }));

  app.get("/chain/transactions/:txHash", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.transactionReceiptService.execute(req.params.txHash));
  }));

  app.get("/chain/user-operations/:userOpHash", asyncRoute(async (req, res) => {
    await sendResult(res, () =>
      runtime.userOperationStatusService.execute(req.params.userOpHash),
    );
  }));

  app.post("/x402/fetch", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.x402FetchService.execute(req.body));
  }));

  return { app };
}
```

- [ ] **Step 5: Update chain config and entrypoint**

Modify `chain/src/config.ts`:

```ts
export type AppConfig = {
  http: {
    port: number;
  };
  // keep the remaining fields unchanged
};

// inside loadConfig()
return {
  http: {
    port: parseNumberEnv(env, "CHAIN_HTTP_PORT", 8091),
  },
  // keep the remaining sections unchanged
};
```

Modify `chain/src/index.ts`:

```ts
import { loadConfig } from "./config.js";
import { createChainHttpApp } from "./http/chain-app.js";

async function start() {
  const config = loadConfig();
  const { app } = createChainHttpApp(config);

  app.listen(config.http.port, "0.0.0.0", (error?: Error) => {
    if (error) {
      console.error("chain HTTP startup failed", error);
      process.exit(1);
    }

    console.log(
      JSON.stringify({
        message: "chain HTTP server started",
        httpPort: config.http.port,
        rpcUrl: config.network.rpcUrl,
        expectedChainId: config.network.expectedChainId,
        mockChain: config.network.mockChain,
        dailyLimitWei: config.policy.dailyLimitWei.toString(),
        x402Network: config.x402.network,
        x402FacilitatorUrl: config.x402.facilitatorUrl,
      }),
    );
  });
}

void start();
```

- [ ] **Step 6: Run the chain REST test to verify it passes**

Run:

```bash
cd chain && npm test -- test/chain-http-server.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit chain REST conversion**

```bash
git add chain/src/http/result.ts chain/src/http/chain-app.ts chain/src/config.ts chain/src/index.ts chain/test/chain-http-server.test.ts
git rm chain/test/mcp-server.test.ts
git commit -m "refactor: expose chain actions over REST"
```

---

## Task 2: Convert Circle MCP Surface To Circle REST

**Files:**
- Create: `chain/src/http/circle-app.ts`
- Modify: `chain/src/index-circle.ts`
- Replace: `chain/test/circle-mcp-server.test.ts` -> `chain/test/circle-http-server.test.ts`

- [ ] **Step 1: Write the failing Circle REST tests**

Move `chain/test/circle-mcp-server.test.ts` to `chain/test/circle-http-server.test.ts`:

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { loadConfig } from "../src/config.js";
import { createCircleHttpApp, createCircleRuntime } from "../src/http/circle-app.js";

type RuntimeOverrides = NonNullable<Parameters<typeof createCircleRuntime>[1]>;

function createMockConfig() {
  return loadConfig({
    CHAIN_MOCK: "true",
    PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
  });
}

async function request(path: string, options: RequestInit = {}, overrides?: RuntimeOverrides) {
  const { app } = createCircleHttpApp(createMockConfig(), overrides);
  const server = createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.equal(typeof address, "object");
  assert.notEqual(address, null);
  try {
    return await fetch(`http://127.0.0.1:${address.port}${path}`, {
      ...options,
      headers: {
        "content-type": "application/json",
        ...(options.headers ?? {}),
      },
    });
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
}

test("circle REST health returns service metadata", async () => {
  const response = await request("/health");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.service, "chief-circle");
  assert.equal(body.status, "ok");
  assert.equal(body.mockChain, true);
});

test("circle REST exposes Agent Wallet lifecycle and settlement routes", async () => {
  const response = await request("/circle/routes");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.routes.sort(), [
    "GET /circle/transactions/:transactionId",
    "GET /circle/wallets/status",
    "POST /circle/gateway/deposits",
    "POST /circle/gateway/withdrawals",
    "POST /circle/settlements",
    "POST /circle/wallets/get-or-create",
    "POST /circle/wallets/import",
    "POST /circle/wallets/init",
  ]);
});

test("circle runtime wires live Circle wallet creation with a per-request ciphertext factory", () => {
  const runtime = createCircleRuntime(
    loadConfig({
      PRIVATE_KEY: "0x59c6995e998f97a5a0044966f0945382d7fb8f3c2b5b2dd8f04c208dbb0f4f8d",
      CIRCLE_API_KEY: "circle-api-key",
      CIRCLE_WALLET_SET_ID: "circle-wallet-set",
      CIRCLE_ENTITY_SECRET: "entity-secret",
    }),
  );

  const circleWalletService = (runtime.agentWalletService as any).circleWalletService;
  assert.equal(typeof circleWalletService.createEntitySecretCiphertext, "function");
});
```

- [ ] **Step 2: Run the Circle REST test to verify it fails**

```bash
cd chain && npm test -- test/circle-http-server.test.ts
```

Expected: FAIL because `../src/http/circle-app.js` does not exist.

- [ ] **Step 3: Implement the Circle REST app**

Create `chain/src/http/circle-app.ts`:

```ts
import { generateEntitySecretCiphertext } from "@circle-fin/developer-controlled-wallets";
import express from "express";

import { loadConfig, type AppConfig } from "../config.js";
import { AgentWalletService } from "../services/agent-wallet-service.js";
import { CircleWalletService } from "../services/circle-wallet-service.js";
import { asyncRoute, sendResult } from "./result.js";

type CircleRuntime = {
  agentWalletService: AgentWalletService;
};

type CircleRuntimeOverrides = {
  agentWalletService?: AgentWalletService;
  circleWalletService?: CircleWalletService;
};

export function createCircleRuntime(
  config: AppConfig = loadConfig(),
  overrides?: CircleRuntimeOverrides,
): CircleRuntime {
  const circleWalletService =
    overrides?.circleWalletService ??
    new CircleWalletService(config, {
      createEntitySecretCiphertext: (entitySecret) =>
        generateEntitySecretCiphertext({
          apiKey: config.circle.apiKey ?? "",
          entitySecret,
        }),
    });

  return {
    agentWalletService:
      overrides?.agentWalletService ??
      new AgentWalletService(config, undefined, circleWalletService),
  };
}

const ROUTES = [
  "GET /circle/transactions/:transactionId",
  "GET /circle/wallets/status",
  "POST /circle/gateway/deposits",
  "POST /circle/gateway/withdrawals",
  "POST /circle/settlements",
  "POST /circle/wallets/get-or-create",
  "POST /circle/wallets/import",
  "POST /circle/wallets/init",
];

export function createCircleHttpApp(config: AppConfig = loadConfig(), overrides?: CircleRuntimeOverrides) {
  const runtime = createCircleRuntime(config, overrides);
  const app = express();
  app.use(express.json({ limit: "1mb" }));

  app.get("/health", (_req, res) => {
    res.json({
      service: "chief-circle",
      status: "ok",
      mockChain: config.network.mockChain,
      circleBaseUrl: config.circle.baseUrl,
      circleWalletSetId: config.circle.walletSetId,
      agentWalletStatePath: config.agentWallet.statePath,
    });
  });

  app.get("/circle/routes", (_req, res) => {
    res.json({ routes: ROUTES });
  });

  app.post("/circle/wallets/import", asyncRoute(async (_req, res) => {
    await sendResult(res, async () => {
      const wallets = await runtime.agentWalletService.listCircleWallets();
      return runtime.agentWalletService.saveLocalWallets(wallets);
    });
  }));

  app.post("/circle/wallets/get-or-create", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.agentWalletService.getOrCreate(req.body));
  }));

  app.post("/circle/wallets/init", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.agentWalletService.init(req.body));
  }));

  app.get("/circle/wallets/status", asyncRoute(async (req, res) => {
    await sendResult(res, () =>
      runtime.agentWalletService.status({
        walletAddress: typeof req.query.walletAddress === "string" ? req.query.walletAddress : undefined,
        circleWalletId: typeof req.query.circleWalletId === "string" ? req.query.circleWalletId : undefined,
      }),
    );
  }));

  app.post("/circle/settlements", asyncRoute(async (req, res) => {
    await sendResult(res, () => {
      if (req.body.toAddress && !req.body.toAgentId && !req.body.toAgentName) {
        return runtime.agentWalletService.withdrawUsdc(req.body);
      }
      return runtime.agentWalletService.transfer({ ...req.body, asset: "USDC" });
    });
  }));

  app.post("/circle/gateway/deposits", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.agentWalletService.depositToGateway(req.body));
  }));

  app.post("/circle/gateway/withdrawals", asyncRoute(async (req, res) => {
    await sendResult(res, () => runtime.agentWalletService.withdrawFromGateway(req.body));
  }));

  app.get("/circle/transactions/:transactionId", asyncRoute(async (req, res) => {
    await sendResult(res, () =>
      runtime.agentWalletService.transactionStatus({ transactionId: req.params.transactionId }),
    );
  }));

  return { app };
}
```

- [ ] **Step 4: Update the Circle entrypoint**

Modify `chain/src/index-circle.ts`:

```ts
import { loadConfig } from "./config.js";
import { createCircleHttpApp } from "./http/circle-app.js";

function circleHttpPort(): number {
  const raw = process.env.CIRCLE_HTTP_PORT ?? "8093";
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`CIRCLE_HTTP_PORT must be a positive integer, got ${raw}`);
  }
  return parsed;
}

async function start() {
  const config = loadConfig();
  const port = circleHttpPort();
  const { app } = createCircleHttpApp(config);

  app.listen(port, "0.0.0.0", (error?: Error) => {
    if (error) {
      console.error("circle HTTP startup failed", error);
      process.exit(1);
    }

    console.log(
      JSON.stringify({
        message: "circle HTTP server started",
        httpPort: port,
        mockChain: config.network.mockChain,
        circleBaseUrl: config.circle.baseUrl,
        circleWalletSetId: config.circle.walletSetId,
        agentWalletStatePath: config.agentWallet.statePath,
      }),
    );
  });
}

void start();
```

- [ ] **Step 5: Run the Circle REST test to verify it passes**

```bash
cd chain && npm test -- test/circle-http-server.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit Circle REST conversion**

```bash
git add chain/src/http/circle-app.ts chain/src/index-circle.ts chain/test/circle-http-server.test.ts
git rm chain/test/circle-mcp-server.test.ts
git commit -m "refactor: expose circle actions over REST"
```

---

## Task 3: Replace Ledger MCP Clients With REST Clients

**Files:**
- Create: `ledger/chain_client.py`
- Create: `ledger/circle_client.py`
- Modify: `ledger/main.py`
- Modify: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing tests for payment routing REST**

Add to `ledger/tests/test_ledger_service.py`:

```python
def test_route_payment_intent_is_served_by_rest(self) -> None:
    with self._client() as client:
        response = client.post(
            "/ledger/payment/route",
            json={
                "purpose": "buy async service",
                "deliveryMode": "async_task",
                "requiresAcceptance": True,
                "externalService": False,
            },
        )

    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(payload["method"], "ledger_escrow")
    self.assertEqual(
        payload["allowedTools"],
        [
            "agent_wallet_create_escrow",
            "agent_wallet_release_escrow",
            "agent_wallet_refund_escrow",
        ],
    )
```

- [ ] **Step 2: Run the routing test to verify it fails**

```bash
cd ledger && python -m unittest tests.test_ledger_service.LedgerServiceTests.test_route_payment_intent_is_served_by_rest
```

Expected: FAIL with HTTP 404 for `/ledger/payment/route`.

- [ ] **Step 3: Implement the route endpoint**

Modify `ledger/main.py` imports:

```python
from payment_router import PaymentIntent, route_payment_intent
```

Add endpoint near the other `/ledger/*` endpoints:

```python
@app.post("/ledger/payment/route")
def route_ledger_payment_intent(intent: PaymentIntent) -> dict[str, Any]:
    return route_payment_intent(intent)
```

Update `withdraw_agent_wallet()` to call the direct function:

```python
route = route_payment_intent(
    PaymentIntent(
        purpose="withdraw Agent Wallet USDC to an external Base address",
        deliveryMode="withdrawal",
        externalService=True,
    )
)
```

- [ ] **Step 4: Run the routing test to verify it passes**

```bash
cd ledger && python -m unittest tests.test_ledger_service.LedgerServiceTests.test_route_payment_intent_is_served_by_rest
```

Expected: PASS.

- [ ] **Step 5: Write failing tests for REST clients**

Add focused tests to `ledger/tests/test_ledger_service.py`:

```python
def test_chain_recorder_posts_rest_execution(self) -> None:
    calls = []

    async def handler(request):
        calls.append((request.url.path, request.headers.get("content-type"), request.json()))
        return httpx.Response(
            200,
            json={
                "execution": {"txHash": "0xabc123", "mode": "mock"},
                "settlement": {"kind": "submitted"},
            },
        )

    recorder = main.LedgerChainRecorder(
        enabled=True,
        chain_http_url="http://chain.test",
        recorder_address="0x000000000000000000000000000000000000dEaD",
        timeout_seconds=30,
        max_payload_bytes=2048,
        require_success=True,
        transport=httpx.MockTransport(handler),
    )

    entry = main.LedgerEntry(
        entryId="entry_1",
        entryType="credit",
        agentId="agentA",
        availableDeltaAtomic="100",
        createdAt=main.now_iso(),
    )
    record = asyncio.run(
        recorder.submit(
            event_type="credit",
            escrow=None,
            entries=[entry],
            payload={"eventType": "credit"},
        )
    )

    self.assertEqual(calls[0][0], "/chain/executions")
    self.assertEqual(calls[0][2]["to"], "0x000000000000000000000000000000000000dEaD")
    self.assertEqual(record.status, "submitted")
    self.assertEqual(record.txHash, "0xabc123")

def test_wallet_client_uses_circle_rest_status(self) -> None:
    async def handler(request):
        self.assertEqual(request.url.path, "/circle/wallets/status")
        self.assertEqual(request.url.params["walletAddress"], "0xabc")
        return httpx.Response(200, json={"balances": {"USDC": "1.23"}})

    client = main.LedgerWalletClient(
        wallet_http_url="http://circle.test",
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    status = asyncio.run(client.status(wallet_address="0xabc", circle_wallet_id=None))

    self.assertEqual(status["balances"]["USDC"], "1.23")
```

- [ ] **Step 6: Run the REST client tests to verify they fail**

```bash
cd ledger && python -m unittest tests.test_ledger_service.LedgerServiceTests.test_chain_recorder_posts_rest_execution tests.test_ledger_service.LedgerServiceTests.test_wallet_client_uses_circle_rest_status
```

Expected: FAIL because constructors still use `*_mcp_url` and JSON-RPC payloads.

- [ ] **Step 7: Implement `ledger/chain_client.py`**

Create `ledger/chain_client.py`:

```python
from __future__ import annotations

from typing import Any

import httpx


class ChainHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def submit_execution(self, *, to: str, value_eth: str, data: str) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chain/executions",
                json={"to": to, "valueEth": value_eth, "data": data},
            )
        return self._json_or_error(response, "chain")

    @staticmethod
    def _json_or_error(response: httpx.Response, service: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"{service} REST request failed: HTTP {response.status_code} {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{service} REST response was not a JSON object")
        error = payload.get("error")
        if isinstance(error, dict):
            raise RuntimeError(str(error.get("message") or error))
        return payload
```

- [ ] **Step 8: Implement `ledger/circle_client.py`**

Create `ledger/circle_client.py`:

```python
from __future__ import annotations

from typing import Any

import httpx


class CircleHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def get_or_create_wallet(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/wallets/get-or-create", payload)

    async def wallet_status(self, *, wallet_address: str | None, circle_wallet_id: str | None) -> dict[str, Any]:
        params = {
            key: value
            for key, value in {
                "walletAddress": wallet_address,
                "circleWalletId": circle_wallet_id,
            }.items()
            if value is not None
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.get(f"{self.base_url}/circle/wallets/status", params=params)
        return self._json_or_error(response, "circle")

    async def gateway_deposit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/gateway/deposits", payload)

    async def gateway_withdraw(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/gateway/withdrawals", payload)

    async def settle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/settlements", payload)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload)
        return self._json_or_error(response, "circle")

    @staticmethod
    def _json_or_error(response: httpx.Response, service: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"{service} REST request failed: HTTP {response.status_code} {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{service} REST response was not a JSON object")
        error = payload.get("error")
        if isinstance(error, dict):
            raise RuntimeError(str(error.get("message") or error))
        return payload
```

- [ ] **Step 9: Wire ledger classes to REST clients**

Modify `ledger/main.py`:

```python
from chain_client import ChainHttpClient
from circle_client import CircleHttpClient

DEFAULT_CHAIN_HTTP_URL = "http://chain:8091"
DEFAULT_SETTLEMENT_HTTP_URL = "http://circle:8093"
DEFAULT_WALLET_HTTP_URL = "http://circle:8093"
```

Rename `LedgerChainRecord.chainMcpUrl` to `chainHttpUrl` and `chainTool` to `chainAction`:

```python
chainAction: str = "POST /chain/executions"
chainHttpUrl: str
```

Update `LedgerChainRecorder.__init__()` to accept `chain_http_url` and optional `transport`. In `_call_chain_submit_execution()`, replace JSON-RPC with:

```python
client = ChainHttpClient(
    base_url=self.chain_http_url,
    timeout_seconds=self.timeout_seconds,
    transport=self.transport,
)
return await client.submit_execution(
    to=self.recorder_address,
    value_eth="0",
    data=data,
)
```

Update `LedgerSettlementClient` and `LedgerWalletClient` to use `CircleHttpClient` and return direct REST payloads.

Update cache factories:

```python
chain_http_url=os.getenv("LEDGER_CHAIN_HTTP_URL", DEFAULT_CHAIN_HTTP_URL)
settlement_http_url=os.getenv("LEDGER_SETTLEMENT_HTTP_URL", DEFAULT_SETTLEMENT_HTTP_URL)
wallet_http_url=os.getenv("LEDGER_WALLET_HTTP_URL", DEFAULT_WALLET_HTTP_URL)
```

- [ ] **Step 10: Run ledger client tests to verify they pass**

```bash
cd ledger && python -m unittest tests.test_ledger_service.LedgerServiceTests.test_chain_recorder_posts_rest_execution tests.test_ledger_service.LedgerServiceTests.test_wallet_client_uses_circle_rest_status
```

Expected: PASS.

- [ ] **Step 11: Commit ledger REST clients**

```bash
git add ledger/main.py ledger/chain_client.py ledger/circle_client.py ledger/tests/test_ledger_service.py
git commit -m "refactor: call chain and circle over REST"
```

---

## Task 4: Remove Ledger MCP App And Tests

**Files:**
- Delete: `ledger/mcp_tools.py`
- Modify: `ledger/main.py`
- Modify: `ledger/requirements.txt`
- Modify: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing tests that `/mcp/` is gone**

Replace `test_mcp_endpoint_initializes_with_ledger_app_lifespan` in `ledger/tests/test_ledger_service.py`:

```python
def test_mcp_endpoint_is_not_exposed(self) -> None:
    with self._client() as client:
        response = client.get("/mcp/")

    self.assertEqual(response.status_code, 404)
```

Delete or rewrite these tests so they use REST endpoints or direct payment router calls:

- `test_mcp_ledger_state_uses_circle_balance_as_agent_visible_available`
- `test_wallet_get_or_create_mcp_tool_creates_ledger_account`
- `test_route_payment_intent_tool_is_served_by_ledger`
- `test_route_payment_intent_supports_funding_onramp`
- `test_route_payment_intent_supports_direct_agent_transfer`
- `test_route_payment_intent_supports_withdrawal`
- `test_ledger_mcp_tools_operate_on_local_store`
- `test_onramp_mcp_tool_creates_session`

- [ ] **Step 2: Run the no-MCP ledger test to verify it fails**

```bash
cd ledger && python -m unittest tests.test_ledger_service.LedgerServiceTests.test_mcp_endpoint_is_not_exposed
```

Expected: FAIL because `/mcp/` is still mounted.

- [ ] **Step 3: Remove ledger MCP imports and mount**

Modify `ledger/main.py`:

- Remove the `from mcp_tools import ...` import block.
- Remove `_ledger_mcp_app`.
- Remove `ledger_app_lifespan`.
- Remove `app.router.lifespan_context = ledger_app_lifespan`.
- Remove `app.mount("/", _ledger_mcp_app)`.

Delete dependency line from `ledger/requirements.txt`:

```text
mcp>=1.0,<2
```

Delete file:

```bash
git rm ledger/mcp_tools.py
```

- [ ] **Step 4: Run ledger tests**

```bash
cd ledger && python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 5: Commit ledger MCP removal**

```bash
git add ledger/main.py ledger/requirements.txt ledger/tests/test_ledger_service.py
git rm ledger/mcp_tools.py
git commit -m "refactor: remove ledger MCP surface"
```

---

## Task 5: Remove Agent MCP Runtime

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/prompt_builder.py`
- Modify: `agent/requirements.txt`
- Modify: `agent/tests/test_main_api.py`
- Modify: `agent/tests/test_main_tools.py`
- Modify: `agent/tests/test_clean_core.py`
- Modify: `agent/tests/test_prompt_builder.py`
- Delete: `agent/mcp_runtime.py`
- Delete: `agent/chain_mcp_client.py`
- Delete: `agent/mcp_tool_metadata.py`
- Delete: `agent/skill_loader.py`
- Delete: `agent/tool_schema.py`
- Delete: `agent/tests/test_mcp_runtime.py`
- Delete: `agent/tests/test_chain_mcp_client.py`
- Delete: `agent/tests/test_skill_loader.py`
- Delete: `agent/tests/test_tool_schema.py`

- [ ] **Step 1: Write failing agent tests for no dynamic tools**

Modify `agent/tests/test_main_tools.py`:

```python
import unittest

import main


class MainToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        main.get_agent_graph.cache_clear()

    def test_build_tools_returns_no_dynamic_mcp_tools(self) -> None:
        self.assertEqual(main.build_tools(), [])
```

Modify `agent/tests/test_main_api.py` health test:

```python
def test_health_returns_rest_orchestrator_shape(self) -> None:
    response = TestClient(main.app).get("/health")

    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(payload["status"], "ok")
    self.assertEqual(payload["toolCount"], 0)
    self.assertEqual(payload["tools"], [])
    self.assertNotIn("mcpServers", payload)
    self.assertNotIn("mcpHealth", payload)
    self.assertNotIn("autonomy", payload)
    self.assertNotIn("chainWallet", payload)
```

Modify `agent/tests/test_prompt_builder.py`:

```python
import unittest

from prompt_builder import build_agent_prompt


class PromptBuilderTests(unittest.TestCase):
    def test_prompt_describes_rest_orchestrator(self) -> None:
        prompt = build_agent_prompt()
        self.assertIn("You are OntologyAgent.", prompt)
        self.assertIn("REST-backed orchestration", prompt)
        self.assertNotIn("MCP", prompt)
```

- [ ] **Step 2: Run agent tests to verify they fail**

```bash
PYTHONPATH=agent python -m unittest agent.tests.test_main_tools agent.tests.test_main_api agent.tests.test_prompt_builder
```

Expected: FAIL because `main` still reports MCP runtime fields and `build_agent_prompt()` still expects a catalog.

- [ ] **Step 3: Simplify agent prompt and tool registry**

Modify `agent/prompt_builder.py`:

```python
from __future__ import annotations


BASE_AGENT_PROMPT = (
    "You are OntologyAgent. You are a REST-backed orchestration agent. "
    "Use only explicitly configured local tools. Do not invent tool results."
)


def build_agent_prompt() -> str:
    return BASE_AGENT_PROMPT
```

Modify `agent/main.py`:

- Remove imports of `McpRuntime`, `SkillCatalog`, and `load_skill_catalog`.
- Remove `SKILLS_DIR`.
- Remove `_discovered_tools`.
- Remove `get_skill_catalog()`.
- Remove `get_mcp_runtime()`.
- Remove `clear_discovered_tool_cache()`.
- Remove `refresh_discovered_tool_cache()`.
- Remove `_in_running_loop()`.
- Replace `build_tools()` with:

```python
def build_tools() -> list[StructuredTool]:
    return []
```

- Replace `get_agent_prompt()` with:

```python
def get_agent_prompt() -> str:
    return build_agent_prompt()
```

- Replace startup event body:

```python
@app.on_event("startup")
async def startup_event() -> None:
    return None
```

- Replace `/health`:

```python
@app.get("/health")
def health() -> dict[str, Any]:
    tools = build_tools()
    return {
        "service": "OntologyAgent-agent",
        "status": "ok",
        "modelName": os.getenv("BRAIN_AGENT_MODEL", "gpt-4o-mini"),
        "openaiBaseUrl": get_openai_base_url(),
        "toolCount": len(tools),
        "tools": [tool.name for tool in tools],
    }
```

- Delete `/agent/reload-runtime`.

Remove `mcp>=1.0,<2` from `agent/requirements.txt`.

- [ ] **Step 4: Delete MCP runtime files and tests**

```bash
git rm agent/mcp_runtime.py agent/chain_mcp_client.py agent/mcp_tool_metadata.py agent/skill_loader.py agent/tool_schema.py
git rm agent/tests/test_mcp_runtime.py agent/tests/test_chain_mcp_client.py agent/tests/test_skill_loader.py agent/tests/test_tool_schema.py
```

Update `agent/tests/test_clean_core.py` core file list:

```python
core_files = [
    root / "main.py",
    root / "prompt_builder.py",
]
```

- [ ] **Step 5: Run agent tests**

```bash
PYTHONPATH=agent python -m unittest discover -s agent/tests
```

Expected: PASS.

- [ ] **Step 6: Commit agent MCP removal**

```bash
git add agent/main.py agent/prompt_builder.py agent/requirements.txt agent/tests/test_main_api.py agent/tests/test_main_tools.py agent/tests/test_clean_core.py agent/tests/test_prompt_builder.py
git rm agent/mcp_runtime.py agent/chain_mcp_client.py agent/mcp_tool_metadata.py agent/skill_loader.py agent/tool_schema.py agent/tests/test_mcp_runtime.py agent/tests/test_chain_mcp_client.py agent/tests/test_skill_loader.py agent/tests/test_tool_schema.py
git commit -m "refactor: remove agent MCP runtime"
```

---

## Task 6: Remove Chain/Circle MCP Files And Dependencies

**Files:**
- Delete: `chain/src/mcp/server.ts`
- Delete: `chain/src/mcp/tools.ts`
- Delete: `chain/src/mcp/circle-server.ts`
- Delete: `chain/src/mcp/circle-tools.ts`
- Delete: `chain/src/mcp/result.ts`
- Modify: `chain/package.json`
- Modify: `chain/package-lock.json`
- Modify: `chain/test/config.test.ts`

- [ ] **Step 1: Write failing config test for HTTP port naming**

Modify `chain/test/config.test.ts`:

```ts
test("loadConfig rejects invalid CHAIN_HTTP_PORT", () => {
  assert.throws(
    () =>
      loadConfig({
        CHAIN_HTTP_PORT: "8091.5",
      }),
    /CHAIN_HTTP_PORT must be a positive integer/,
  );
});
```

Delete the old `CHAIN_MCP_PORT` assertion.

- [ ] **Step 2: Run config test to verify it fails**

```bash
cd chain && npm test -- test/config.test.ts
```

Expected: FAIL until `loadConfig()` reads `CHAIN_HTTP_PORT`.

- [ ] **Step 3: Remove MCP dependency and files**

```bash
cd chain && npm uninstall @modelcontextprotocol/sdk
cd ..
git rm chain/src/mcp/server.ts chain/src/mcp/tools.ts chain/src/mcp/circle-server.ts chain/src/mcp/circle-tools.ts chain/src/mcp/result.ts
```

Ensure `chain/src/config.ts` contains no `mcp` property and no `CHAIN_MCP_PORT` fallback.

- [ ] **Step 4: Run chain tests and typecheck**

```bash
cd chain && npm test && npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit chain MCP deletion**

```bash
git add chain/package.json chain/package-lock.json chain/src/config.ts chain/test/config.test.ts
git rm chain/src/mcp/server.ts chain/src/mcp/tools.ts chain/src/mcp/circle-server.ts chain/src/mcp/circle-tools.ts chain/src/mcp/result.ts
git commit -m "refactor: remove chain MCP implementation"
```

---

## Task 7: Update Compose And Core Documentation

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.core.yml`
- Modify: `docker-compose.cloudflare.yml` if needed
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the failing MCP scan**

Run:

```bash
rg -n "MCP|mcp|/mcp|CHAIN_MCP|CIRCLE_MCP|LEDGER_.*MCP|chain-mcp|circle-mcp" README.md AGENTS.md docker-compose*.yml
```

Expected before edits: matches in docs and compose.

- [ ] **Step 2: Update compose**

In `docker-compose.yml`:

- Replace `LEDGER_CHAIN_MCP_URL` with `LEDGER_CHAIN_HTTP_URL: ${LEDGER_CHAIN_HTTP_URL:-http://chain:8091}`.
- Replace `LEDGER_SETTLEMENT_MCP_URL` with `LEDGER_SETTLEMENT_HTTP_URL: ${LEDGER_SETTLEMENT_HTTP_URL:-http://circle:8093}`.
- Replace `CHAIN_MCP_PORT` with `CHAIN_HTTP_PORT`.
- Replace `CIRCLE_MCP_PORT` with `CIRCLE_HTTP_PORT`.
- Remove network aliases `chain-mcp` and `circle-mcp`.

In `docker-compose.core.yml`, make the same naming changes.

- [ ] **Step 3: Update `AGENTS.md`**

Replace MCP rules with REST/CLI rules:

```md
- Direct chain actions ONLY via chain REST service or approved `chief` command family after routing
- Circle Agent Wallet lifecycle and Circle settlement ONLY via ledger-mediated Circle REST backend
- Agent-facing ledger access is through `chief ledger ...` and ledger REST endpoints
- Any payment, x402 call, chain transfer, escrow lock, release, or refund MUST call route_payment_intent first
- After routing, use only the returned allowed command/API family; if the router returns needs_clarification, ask the user before paying
```

Replace testing line:

```md
- Ledger route endpoint: `POST http://localhost:8092/ledger/payment/route`
```

- [ ] **Step 4: Update `README.md`**

Rewrite the architecture section:

- Rename `## MCP 架构` to `## REST/CLI 架构`.
- Describe `chain` as a REST service.
- Describe `circle` as an internal REST backend.
- Describe `ledger` as the public/offchain REST service.
- Replace `/mcp/` endpoint bullets with `/health` and REST route bullets.
- Replace `*_MCP_URL` environment variables with `*_HTTP_URL`.
- Replace `CHAIN_MCP_PORT` and `CIRCLE_MCP_PORT` with `CHAIN_HTTP_PORT` and `CIRCLE_HTTP_PORT`.

- [ ] **Step 5: Verify core docs are clean**

Run:

```bash
rg -n "MCP|mcp|/mcp|CHAIN_MCP|CIRCLE_MCP|LEDGER_.*MCP|chain-mcp|circle-mcp" README.md AGENTS.md docker-compose*.yml
```

Expected: no matches.

- [ ] **Step 6: Commit compose and docs**

```bash
git add docker-compose.yml docker-compose.core.yml docker-compose.cloudflare.yml README.md AGENTS.md
git commit -m "docs: document REST and CLI architecture"
```

---

## Task 8: Update `chief-install`

**Files:**
- Modify: `/Users/freedom/cc/chief-install/bin/chief`
- Modify: `/Users/freedom/cc/chief-install/INSTALL.md`
- Modify: `/Users/freedom/cc/chief-install/README.md`

This task edits a sibling repository outside `/Users/freedom/cc/OntologyAgent`. Request filesystem approval before editing if the sandbox blocks writes.

- [ ] **Step 1: Write the failing chief-install scan**

Run:

```bash
rg -n "MCP|mcp|/mcp" /Users/freedom/cc/chief-install/README.md /Users/freedom/cc/chief-install/INSTALL.md /Users/freedom/cc/chief-install/bin/chief /Users/freedom/cc/chief-install/skills
```

Expected before edits: matches in `bin/chief` and `INSTALL.md`.

- [ ] **Step 2: Update `bin/chief` defaults and help**

Modify `/Users/freedom/cc/chief-install/bin/chief`:

```sh
LEDGER_URL="${CHIEF_LEDGER_HTTP_URL:-${CHIEF_LEDGER_URL:-https://ledger.curawealth.ai}}"
LEDGER_FALLBACK_URL="${CHIEF_LEDGER_FALLBACK_URL:-}"
```

Remove the `case "$LEDGER_CONFIG_URL"` block that strips `/mcp`.

Update usage text:

```text
  CHIEF_LEDGER_URL             default hosted ledger service base URL
  CHIEF_LEDGER_HTTP_URL        optional explicit service base URL for CLI REST calls
  CHIEF_LEDGER_FALLBACK_URL    optional fallback service base URL
```

- [ ] **Step 3: Update chief-install docs**

In `/Users/freedom/cc/chief-install/README.md`, replace hosted endpoint wording:

```md
Hosted REST endpoint defaults live in `bin/chief`; override them with `CHIEF_*`
environment variables only when using another deployment.
```

In `/Users/freedom/cc/chief-install/INSTALL.md`, replace the URL paragraph:

```md
The hosted Chief REST endpoint is built into the `chief` command. Override
`CHIEF_LEDGER_URL` only when pointing the same install kit at another deployment.
Set `CHIEF_LEDGER_FALLBACK_URL` only when you want an explicit local fallback
during development.
```

- [ ] **Step 4: Verify chief-install is clean**

Run:

```bash
rg -n "MCP|mcp|/mcp" /Users/freedom/cc/chief-install/README.md /Users/freedom/cc/chief-install/INSTALL.md /Users/freedom/cc/chief-install/bin/chief /Users/freedom/cc/chief-install/skills
```

Expected: no matches.

- [ ] **Step 5: Run chief-install tests**

```bash
cd /Users/freedom/cc/chief-install && python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 6: Commit chief-install changes in its repository**

```bash
cd /Users/freedom/cc/chief-install
git add bin/chief INSTALL.md README.md
git commit -m "refactor: remove MCP URL compatibility"
```

---

## Task 9: Final Repository Sweep And Verification

**Files:**
- Modify any remaining file reported by the final scans.

- [ ] **Step 1: Run repository-wide MCP implementation scan**

```bash
rg -n "MCP|mcp|/mcp|McpServer|FastMCP|modelcontextprotocol|CHAIN_MCP|CIRCLE_MCP|LEDGER_.*MCP|chainMcpUrl|chainTool|chain-mcp|circle-mcp" .
```

Expected: matches only in migration history under `docs/superpowers/specs/*mcp*` or `docs/superpowers/plans/*mcp*`. Any active code, tests, README, compose, AGENTS, or runtime file match must be removed or rewritten.

- [ ] **Step 2: Run Python agent tests**

```bash
PYTHONPATH=agent python -m unittest discover -s agent/tests
```

Expected: PASS.

- [ ] **Step 3: Run ledger tests**

```bash
cd ledger && python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 4: Run chain tests and typecheck**

```bash
cd chain && npm test && npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Build Docker services**

```bash
docker compose build ledger chain circle agent
```

Expected: all requested images build successfully.

- [ ] **Step 6: Start services**

```bash
docker compose up -d --build
```

Expected: `agent`, `ledger`, `chain`, `circle`, `x402-seller`, and `x402-mock` start or recreate successfully.

- [ ] **Step 7: Verify health endpoints**

```bash
curl http://localhost:8092/health
curl http://localhost:8091/health
curl http://localhost:8093/health
curl http://localhost:8000/health
```

Expected:

- Ledger response includes `"status":"ok"`.
- Chain response includes `"service":"chief-chain"`.
- Circle response includes `"service":"chief-circle"`.
- Agent response has no `mcpServers` or `mcpHealth`.

- [ ] **Step 8: Verify ledger state and payment routing**

```bash
curl http://localhost:8092/ledger/state
curl -X POST http://localhost:8092/ledger/payment/route \
  -H "Content-Type: application/json" \
  -d '{"purpose":"buy async service","deliveryMode":"async_task","requiresAcceptance":true,"externalService":false}'
```

Expected: route response has `"method":"ledger_escrow"` and no transport-specific fields.

- [ ] **Step 9: Verify Chief CLI health when installed**

```bash
runtime/workspace/.local/bin/chief ledger health
```

Expected: health JSON from the configured hosted or local ledger service.

- [ ] **Step 10: Commit final cleanup**

```bash
git status --short
git add .
git commit -m "chore: finish MCP removal"
```

Only run this commit if `git status --short` shows final cleanup files not already committed by earlier tasks.
