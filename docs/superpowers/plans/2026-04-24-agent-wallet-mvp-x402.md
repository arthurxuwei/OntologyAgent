# Agent Wallet MVP x402 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first demoable Agent Wallet MVP: GitHub-authenticated owners can create and claim a Circle-backed seller wallet, register an x402-priced Agent service, trigger a Base Sepolia x402 A2A payment, and inspect the resulting local ledger.

**Architecture:** Keep the existing service boundaries. `chain` exposes product-shaped Agent Wallet MCP tools around Circle provisioning and x402 calls; `agent` owns OAuth, sessions, local product state, claim transitions, HTTP endpoints, and the guided web UI; `x402-seller` serves the paid Agent service endpoint using the Circle wallet address as `payTo`.

**Tech Stack:** FastAPI, Pydantic, local JSON persistence, GitHub OAuth, Circle sandbox API via `httpx`/`fetch`, TypeScript MCP server, existing `@x402/*` buyer flow, Python `unittest`, Node test runner.

---

## Source Spec

- `docs/superpowers/specs/2026-04-24-agent-wallet-mvp-x402-design.md`

## File Structure

- Create `agent/agent_wallet_state.py`
  - Pydantic models and JSON-backed repository for Owners, Agents, Claims, Services, Payments, and sessions.
- Create `agent/agent_wallet_auth.py`
  - GitHub OAuth URL generation, callback exchange, PKCE/state helpers, signed session cookie helpers.
- Modify `agent/main.py`
  - Add auth endpoints, Agent Wallet HTTP endpoints, chain MCP wrappers, and route wiring.
- Modify `agent/tests/test_main_api.py`
  - Add API tests for session, init, claim, service registration, service call persistence, and reset.
- Modify `agent/tests/test_main_tools.py`
  - Verify newly discovered Agent Wallet MCP tools can be surfaced as agent tools when advertised.
- Modify `agent/web/chat.html`
  - Add the guided Agent Wallet MVP panel and JavaScript client calls.
- Create `chain/src/services/circle-wallet-service.ts`
  - Circle sandbox wallet provisioning/status client plus deterministic mock behavior in `CHAIN_MOCK=true`.
- Create `chain/src/services/agent-wallet-service.ts`
  - Product-shaped service methods for wallet init/status, x402 registration normalization, and x402 service calls.
- Modify `chain/src/domain/types.ts`
  - Add Agent Wallet command/result types.
- Modify `chain/src/mcp/tools.ts`
  - Register `agent_wallet_init`, `agent_wallet_status`, `agent_wallet_register_x402_service`, and `agent_wallet_call_x402_service`.
- Modify `chain/src/config.ts`
  - Add Circle sandbox configuration and Agent Wallet defaults.
- Modify `chain/test/mcp-server.test.ts`
  - Cover new MCP tool discovery and structured results.
- Create `chain/test/agent-wallet-service.test.ts`
  - Unit-test Circle request validation, mock wallet behavior, service registration normalization, and x402 wrapper shape.
- Modify `x402-seller/main.py`
  - Add named Agent service endpoint.
- Modify `x402-seller/x402_seller.py`
  - Allow request-level or environment-driven Agent service metadata while keeping payment challenge behavior centralized.
- Modify `x402-seller/tests/test_x402_seller.py`
  - Cover named service challenge, `payTo`, settlement success response body, and configured metadata.
- Modify `docker-compose.yml`
  - Add GitHub/Circle/session env vars and local state volume where needed.
- Modify `README.md`
  - Document the MVP flow, configuration, and verification commands.

## Task 1: Chain Agent Wallet MCP Skeleton

**Files:**
- Modify: `chain/src/domain/types.ts`
- Create: `chain/src/services/agent-wallet-service.ts`
- Modify: `chain/src/mcp/tools.ts`
- Modify: `chain/test/mcp-server.test.ts`

- [ ] **Step 1: Write the failing MCP discovery test**

Add this assertion to `chain/test/mcp-server.test.ts` in `chain MCP exposes the expected tool names`:

```ts
assert.deepEqual(toolNames, [
  "agent_wallet_call_x402_service",
  "agent_wallet_init",
  "agent_wallet_register_x402_service",
  "agent_wallet_status",
  "chain_execute_trade_intent",
  "chain_get_transaction_receipt",
  "chain_get_user_operation_status",
  "chain_get_wallet_state",
  "chain_sign_transfer",
  "chain_submit_execution",
  "chain_submit_user_operation",
  "chain_x402_fetch",
]);
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
cd chain && npm test -- --test-name-pattern="chain MCP exposes the expected tool names"
```

Expected: FAIL because the four `agent_wallet_*` tools are not registered.

- [ ] **Step 3: Add result and command types**

Append to `chain/src/domain/types.ts`:

```ts
export type AgentWalletInitCommand = {
  agentName: string;
  agentDescription?: string;
};

export type AgentWalletInitResult = {
  circleWalletId: string;
  circleWalletSetId: string | null;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  mode: "mock" | "circle";
};

export type AgentWalletStatusCommand = {
  walletAddress?: string;
  circleWalletId?: string;
};

export type AgentWalletStatusResult = {
  circleWalletId: string | null;
  circleWalletSetId: string | null;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  status: "created" | "available" | "unknown";
  balances: Record<string, string>;
  mode: "mock" | "circle";
};

export type AgentWalletRegisterX402ServiceCommand = {
  name: string;
  path: string;
  priceAtomic: string;
  payTo: string;
};

export type AgentWalletRegisterX402ServiceResult = {
  name: string;
  path: string;
  priceAtomic: string;
  assetAddress: string;
  network: string;
  payTo: string;
  active: true;
};

export type AgentWalletCallX402ServiceCommand = X402FetchCommand;

export type AgentWalletCallX402ServiceResult = X402FetchResult & {
  agentWalletTool: "agent_wallet_call_x402_service";
};
```

- [ ] **Step 4: Add a minimal service implementation**

Create `chain/src/services/agent-wallet-service.ts`:

```ts
import type { AppConfig } from "../config.js";
import { AppError } from "../domain/errors.js";
import type {
  AgentWalletCallX402ServiceCommand,
  AgentWalletCallX402ServiceResult,
  AgentWalletInitCommand,
  AgentWalletInitResult,
  AgentWalletRegisterX402ServiceCommand,
  AgentWalletRegisterX402ServiceResult,
  AgentWalletStatusCommand,
  AgentWalletStatusResult,
} from "../domain/types.js";
import { normalizeAddress } from "../security.js";
import type { X402FetchService } from "./x402-fetch-service.js";

export class AgentWalletService {
  constructor(
    private readonly config: AppConfig,
    private readonly x402FetchService: X402FetchService,
  ) {}

  async init(command: AgentWalletInitCommand): Promise<AgentWalletInitResult> {
    if (!command.agentName.trim()) {
      throw new AppError("INVALID_REQUEST", "agentName is required", 400);
    }
    return {
      circleWalletId: `mock-circle-wallet-${slug(command.agentName)}`,
      circleWalletSetId: "mock-circle-wallet-set",
      blockchain: "BASE-SEPOLIA",
      walletAddress: "0x3333333333333333333333333333333333333333",
      mode: "mock",
    };
  }

  async status(command: AgentWalletStatusCommand): Promise<AgentWalletStatusResult> {
    if (!command.walletAddress && !command.circleWalletId) {
      throw new AppError("INVALID_REQUEST", "walletAddress or circleWalletId is required", 400);
    }
    return {
      circleWalletId: command.circleWalletId ?? null,
      circleWalletSetId: "mock-circle-wallet-set",
      blockchain: "BASE-SEPOLIA",
      walletAddress: command.walletAddress ?? "0x3333333333333333333333333333333333333333",
      status: "available",
      balances: {},
      mode: "mock",
    };
  }

  registerX402Service(
    command: AgentWalletRegisterX402ServiceCommand,
  ): AgentWalletRegisterX402ServiceResult {
    if (!command.name.trim()) {
      throw new AppError("INVALID_REQUEST", "name is required", 400);
    }
    if (!command.path.startsWith("/")) {
      throw new AppError("INVALID_REQUEST", "path must start with /", 400);
    }
    const amountAtomic = BigInt(command.priceAtomic);
    if (amountAtomic <= 0n) {
      throw new AppError("INVALID_REQUEST", "priceAtomic must be positive", 400);
    }
    return {
      name: command.name.trim(),
      path: command.path,
      priceAtomic: amountAtomic.toString(),
      assetAddress: normalizeAddress(this.config.x402.usdcAssetAddress),
      network: this.config.x402.network,
      payTo: normalizeAddress(command.payTo),
      active: true,
    };
  }

  async callX402Service(
    command: AgentWalletCallX402ServiceCommand,
  ): Promise<AgentWalletCallX402ServiceResult> {
    const result = await this.x402FetchService.execute(command);
    return {
      ...result,
      agentWalletTool: "agent_wallet_call_x402_service",
    };
  }
}

function slug(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
```

- [ ] **Step 5: Register the new runtime dependency and tools**

Modify `chain/src/mcp/tools.ts`:

```ts
import { AgentWalletService } from "../services/agent-wallet-service.js";
```

Add to `ChainRuntime`:

```ts
agentWalletService: AgentWalletService;
```

Add to `createChainRuntime()` return:

```ts
agentWalletService: new AgentWalletService(
  config,
  overrides?.x402FetchService ?? new X402FetchService(config, policyGuard),
),
```

Register tools before `chain_get_wallet_state`:

```ts
server.registerTool(
  "agent_wallet_init",
  {
    description: "Create a Circle sandbox Agent Wallet for an Agent.",
    inputSchema: {
      agentName: z.string().min(1).describe("Agent display name"),
      agentDescription: z.string().optional().describe("Optional Agent description"),
    },
  },
  async ({ agentName, agentDescription }) =>
    runTool(() => runtime.agentWalletService.init({ agentName, agentDescription })),
);

server.registerTool(
  "agent_wallet_status",
  {
    description: "Return Agent Wallet status by wallet address or Circle wallet id.",
    inputSchema: {
      walletAddress: z.string().optional().describe("Wallet address"),
      circleWalletId: z.string().optional().describe("Circle wallet id"),
    },
  },
  async ({ walletAddress, circleWalletId }) =>
    runTool(() => runtime.agentWalletService.status({ walletAddress, circleWalletId })),
);

server.registerTool(
  "agent_wallet_register_x402_service",
  {
    description: "Normalize an x402 service registration for an Agent Wallet payee.",
    inputSchema: {
      name: z.string().min(1),
      path: z.string().min(1),
      priceAtomic: z.string().min(1),
      payTo: z.string().min(1),
    },
  },
  async ({ name, path, priceAtomic, payTo }) =>
    runTool(() =>
      Promise.resolve(
        runtime.agentWalletService.registerX402Service({ name, path, priceAtomic, payTo }),
      ),
    ),
);

server.registerTool(
  "agent_wallet_call_x402_service",
  {
    description: "Call a paid Agent service through the existing x402 buyer flow.",
    inputSchema: {
      url: z.string().url(),
      method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).default("GET"),
      headers: z.record(z.string(), z.string()).optional(),
      body: z.unknown().optional(),
    },
  },
  async ({ url, method, headers, body }) =>
    runTool(() => runtime.agentWalletService.callX402Service({ url, method, headers, body })),
);
```

- [ ] **Step 6: Run the passing test**

Run:

```bash
cd chain && npm test -- --test-name-pattern="chain MCP exposes the expected tool names"
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add chain/src/domain/types.ts chain/src/services/agent-wallet-service.ts chain/src/mcp/tools.ts chain/test/mcp-server.test.ts
git commit -m "feat: add agent wallet MCP tool skeleton"
```

## Task 2: Circle Wallet Service and Chain Tests

**Files:**
- Create: `chain/src/services/circle-wallet-service.ts`
- Modify: `chain/src/services/agent-wallet-service.ts`
- Modify: `chain/src/config.ts`
- Create: `chain/test/agent-wallet-service.test.ts`

- [ ] **Step 1: Write failing service tests**

Create `chain/test/agent-wallet-service.test.ts`:

```ts
import test from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "../src/config.js";
import { AgentWalletService } from "../src/services/agent-wallet-service.js";

const fakeX402FetchService = {
  execute: async () => ({
    upstream: { status: 200, contentType: "application/json", payload: { ok: true } },
    payment: null,
    decision: null,
    policy: {
      dayKey: "2026-04-24",
      spentTodayWei: "0",
      dailyLimitWei: "2000000000000000000",
      spentTodayUsdcAtomic: "0",
      dailyLimitUsdcAtomic: "2000000",
    },
  }),
};

test("AgentWalletService creates deterministic mock Circle wallet in CHAIN_MOCK mode", async () => {
  const service = new AgentWalletService(
    loadConfig({ CHAIN_MOCK: "true" }),
    fakeX402FetchService as any,
  );

  const result = await service.init({ agentName: "Research Agent" });

  assert.equal(result.mode, "mock");
  assert.equal(result.blockchain, "BASE-SEPOLIA");
  assert.match(result.circleWalletId, /^mock-circle-wallet-/);
  assert.match(result.walletAddress, /^0x[0-9a-fA-F]{40}$/);
});

test("AgentWalletService normalizes x402 service registration", () => {
  const service = new AgentWalletService(
    loadConfig({ CHAIN_MOCK: "true" }),
    fakeX402FetchService as any,
  );

  const result = service.registerX402Service({
    name: "Research Summary",
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

test("AgentWalletService wraps x402 fetch result with product-specific marker", async () => {
  const service = new AgentWalletService(
    loadConfig({ CHAIN_MOCK: "true" }),
    fakeX402FetchService as any,
  );

  const result = await service.callX402Service({
    url: "http://x402-seller:8000/x402/agent-services/research-summary",
    method: "GET",
  });

  assert.equal(result.agentWalletTool, "agent_wallet_call_x402_service");
  assert.equal(result.upstream.status, 200);
});
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd chain && npm test -- --test-name-pattern="AgentWalletService"
```

Expected: FAIL until Circle config and deterministic mock wallet generation are complete.

- [ ] **Step 3: Add Circle config fields**

Modify `chain/src/config.ts` so `AppConfig` includes:

```ts
circle: {
  apiKey?: string;
  entitySecret?: string;
  entitySecretCiphertext?: string;
  walletSetId?: string;
  baseUrl: string;
  blockchain: "BASE-SEPOLIA";
};
```

In `loadConfig`, populate:

```ts
circle: {
  apiKey: env.CIRCLE_API_KEY,
  entitySecret: env.CIRCLE_ENTITY_SECRET,
  entitySecretCiphertext: env.CIRCLE_ENTITY_SECRET_CIPHERTEXT,
  walletSetId: env.CIRCLE_WALLET_SET_ID,
  baseUrl: env.CIRCLE_BASE_URL ?? "https://api.circle.com/v1/w3s",
  blockchain: "BASE-SEPOLIA",
},
```

- [ ] **Step 4: Implement Circle wallet service boundary**

Create `chain/src/services/circle-wallet-service.ts`:

```ts
import { AppError } from "../domain/errors.js";
import type { AppConfig } from "../config.js";

export type CircleWalletCreateResult = {
  circleWalletId: string;
  circleWalletSetId: string | null;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  mode: "mock" | "circle";
};

export class CircleWalletService {
  constructor(private readonly config: AppConfig) {}

  async createWallet(agentName: string): Promise<CircleWalletCreateResult> {
    if (this.config.network.mockChain) {
      return {
        circleWalletId: `mock-circle-wallet-${slug(agentName)}`,
        circleWalletSetId: "mock-circle-wallet-set",
        blockchain: "BASE-SEPOLIA",
        walletAddress: mockAddress(agentName),
        mode: "mock",
      };
    }
    if (
      !this.config.circle.apiKey ||
      !this.config.circle.walletSetId ||
      !this.config.circle.entitySecretCiphertext
    ) {
      throw new AppError(
        "CONFIG_ERROR",
        "CIRCLE_API_KEY, CIRCLE_WALLET_SET_ID, and CIRCLE_ENTITY_SECRET_CIPHERTEXT are required",
        500,
      );
    }
    const response = await fetch(`${this.config.circle.baseUrl}/developer/wallets`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.config.circle.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        idempotencyKey: crypto.randomUUID(),
        walletSetId: this.config.circle.walletSetId,
        entitySecretCiphertext: this.config.circle.entitySecretCiphertext,
        blockchains: [this.config.circle.blockchain],
        count: 1,
        metadata: [{ name: agentName, refId: slug(agentName) }],
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle wallet creation failed with HTTP ${response.status}`,
        response.status,
        payload,
      );
    }
    const wallet = payload?.data?.wallets?.[0];
    if (!wallet?.id || !wallet?.address) {
      throw new AppError("INTERNAL_ERROR", "Circle wallet response did not include id and address", 502, payload);
    }
    if (wallet.blockchain && wallet.blockchain !== "BASE-SEPOLIA") {
      throw new AppError("NETWORK_MISMATCH", `Circle returned unsupported blockchain ${wallet.blockchain}`, 502, payload);
    }
    return {
      circleWalletId: wallet.id,
      circleWalletSetId: wallet.walletSetId ?? this.config.circle.walletSetId,
      blockchain: "BASE-SEPOLIA",
      walletAddress: wallet.address,
      mode: "circle",
    };
  }
}

function slug(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function mockAddress(seed: string): string {
  const hex = Buffer.from(seed || "agent-wallet").toString("hex").padEnd(40, "0").slice(0, 40);
  return `0x${hex}`;
}
```

- [ ] **Step 5: Wire CircleWalletService into AgentWalletService**

Change `AgentWalletService` constructor to accept a Circle wallet service:

```ts
constructor(
  private readonly config: AppConfig,
  private readonly x402FetchService: X402FetchService,
  private readonly circleWalletService = new CircleWalletService(config),
) {}
```

Change `init()` to:

```ts
async init(command: AgentWalletInitCommand): Promise<AgentWalletInitResult> {
  if (!command.agentName.trim()) {
    throw new AppError("INVALID_REQUEST", "agentName is required", 400);
  }
  return this.circleWalletService.createWallet(command.agentName);
}
```

- [ ] **Step 6: Run chain tests**

Run:

```bash
cd chain && npm test
cd chain && npm run typecheck
```

Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add chain/src/config.ts chain/src/services/circle-wallet-service.ts chain/src/services/agent-wallet-service.ts chain/test/agent-wallet-service.test.ts
git commit -m "feat: add circle-backed agent wallet service boundary"
```

## Task 3: Agent Wallet Local State Store

**Files:**
- Create: `agent/agent_wallet_state.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing state-store tests**

Append to `agent/tests/test_main_api.py`:

```python
class AgentWalletStateStoreTests(unittest.TestCase):
    def test_create_agent_wallet_state_persists_claim_hash_without_plaintext_code(self) -> None:
        from agent_wallet_state import AgentWalletStore

        with tempfile.TemporaryDirectory() as tmp:
            store = AgentWalletStore(os.path.join(tmp, "state.json"))
            owner = store.upsert_owner(
                provider="github",
                provider_user_id="42",
                login="octo",
                email="octo@example.test",
                display_name="Octo",
                avatar_url="https://example.test/avatar.png",
            )
            agent, claim_code = store.create_agent_wallet(
                agent_name="Research Agent",
                agent_description="demo",
                wallet_payload={
                    "circleWalletId": "cw_123",
                    "circleWalletSetId": "cws_123",
                    "blockchain": "BASE-SEPOLIA",
                    "walletAddress": "0x3333333333333333333333333333333333333333",
                },
            )

            payload = json.loads(open(os.path.join(tmp, "state.json")).read())
            self.assertNotIn(claim_code, json.dumps(payload))
            self.assertEqual(agent.claimStatus, "unclaimed")

            claimed = store.claim_wallet(claim_code=claim_code, owner_id=owner.ownerId)
            self.assertEqual(claimed.claimStatus, "claimed")

            with self.assertRaises(ValueError):
                store.claim_wallet(claim_code=claim_code, owner_id=owner.ownerId)
```

Add imports at the top:

```python
import tempfile
```

- [ ] **Step 2: Run failing test**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletStateStoreTests -v
```

Expected: FAIL because `agent_wallet_state.py` does not exist.

- [ ] **Step 3: Implement models and JSON repository**

Create `agent/agent_wallet_state.py` with:

```python
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Owner(BaseModel):
    ownerId: str
    provider: Literal["github"]
    providerUserId: str
    login: str
    email: Optional[str] = None
    displayName: Optional[str] = None
    avatarUrl: Optional[str] = None
    createdAt: str
    updatedAt: str


class AgentRecord(BaseModel):
    agentId: str
    name: str
    description: Optional[str] = None
    ownerId: Optional[str] = None
    walletId: str
    walletAddress: str
    circleWalletSetId: Optional[str] = None
    blockchain: str = "BASE-SEPOLIA"
    claimStatus: Literal["unclaimed", "claimed"] = "unclaimed"
    createdAt: str
    updatedAt: str


class ClaimRecord(BaseModel):
    claimId: str
    agentId: str
    claimCodeHash: str
    expiresAt: str
    claimedAt: Optional[str] = None
    consumedByOwnerId: Optional[str] = None
    createdAt: str


class ServiceRegistration(BaseModel):
    serviceId: str
    agentId: str
    name: str
    path: str
    priceAtomic: str
    assetAddress: str
    network: str
    payTo: str
    active: bool
    createdAt: str


class PaymentRecord(BaseModel):
    paymentId: str
    serviceId: str
    buyerKind: Literal["local_x402_buyer"] = "local_x402_buyer"
    sellerAgentId: str
    sellerWalletAddress: str
    amountAtomic: str
    assetAddress: str
    network: str
    status: str
    requestUrl: str
    resultSummary: dict
    txHash: Optional[str] = None
    settlementReference: Optional[str] = None
    createdAt: str


class AgentWalletState(BaseModel):
    owners: list[Owner] = Field(default_factory=list)
    agents: list[AgentRecord] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    services: list[ServiceRegistration] = Field(default_factory=list)
    payments: list[PaymentRecord] = Field(default_factory=list)


class AgentWalletStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> AgentWalletState:
        if not os.path.exists(self.path):
            return AgentWalletState()
        with open(self.path, "r", encoding="utf-8") as handle:
            return AgentWalletState.model_validate(json.load(handle))

    def save(self, state: AgentWalletState) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(state.model_dump(), handle, indent=2, sort_keys=True)

    def upsert_owner(self, *, provider: str, provider_user_id: str, login: str, email: str | None, display_name: str | None, avatar_url: str | None) -> Owner:
        state = self.load()
        current = now_iso()
        for index, owner in enumerate(state.owners):
            if owner.provider == provider and owner.providerUserId == provider_user_id:
                updated = owner.model_copy(update={"login": login, "email": email, "displayName": display_name, "avatarUrl": avatar_url, "updatedAt": current})
                state.owners[index] = updated
                self.save(state)
                return updated
        owner = Owner(ownerId=f"owner_{uuid.uuid4().hex}", provider="github", providerUserId=provider_user_id, login=login, email=email, displayName=display_name, avatarUrl=avatar_url, createdAt=current, updatedAt=current)
        state.owners.append(owner)
        self.save(state)
        return owner

    def create_agent_wallet(self, *, agent_name: str, agent_description: str | None, wallet_payload: dict) -> tuple[AgentRecord, str]:
        state = self.load()
        current = now_iso()
        claim_code = secrets.token_urlsafe(18)
        agent = AgentRecord(agentId=f"agent_{uuid.uuid4().hex}", name=agent_name, description=agent_description, walletId=wallet_payload["circleWalletId"], walletAddress=wallet_payload["walletAddress"], circleWalletSetId=wallet_payload.get("circleWalletSetId"), blockchain=wallet_payload.get("blockchain", "BASE-SEPOLIA"), createdAt=current, updatedAt=current)
        claim = ClaimRecord(claimId=f"claim_{uuid.uuid4().hex}", agentId=agent.agentId, claimCodeHash=self.hash_claim_code(claim_code), expiresAt=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), createdAt=current)
        state.agents.append(agent)
        state.claims.append(claim)
        self.save(state)
        return agent, claim_code

    def claim_wallet(self, *, claim_code: str, owner_id: str) -> AgentRecord:
        state = self.load()
        code_hash = self.hash_claim_code(claim_code)
        current = now_iso()
        for claim_index, claim in enumerate(state.claims):
            if claim.claimCodeHash != code_hash:
                continue
            if claim.claimedAt is not None:
                raise ValueError("claim code has already been consumed")
            if datetime.fromisoformat(claim.expiresAt) <= datetime.now(timezone.utc):
                raise ValueError("claim code has expired")
            for agent_index, agent in enumerate(state.agents):
                if agent.agentId == claim.agentId:
                    if agent.claimStatus != "unclaimed":
                        raise ValueError("wallet is already claimed")
                    updated_agent = agent.model_copy(update={"ownerId": owner_id, "claimStatus": "claimed", "updatedAt": current})
                    updated_claim = claim.model_copy(update={"claimedAt": current, "consumedByOwnerId": owner_id})
                    state.agents[agent_index] = updated_agent
                    state.claims[claim_index] = updated_claim
                    self.save(state)
                    return updated_agent
        raise ValueError("claim code is invalid")

    @staticmethod
    def hash_claim_code(claim_code: str) -> str:
        return hashlib.sha256(claim_code.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run state tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletStateStoreTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/agent_wallet_state.py agent/tests/test_main_api.py
git commit -m "feat: add agent wallet local state store"
```

## Task 4: GitHub OAuth and Session Endpoints

**Files:**
- Create: `agent/agent_wallet_auth.py`
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing auth endpoint tests**

Add to `agent/tests/test_main_api.py`:

```python
class AgentWalletAuthApiTests(unittest.TestCase):
    def test_auth_session_returns_anonymous_without_cookie(self) -> None:
        client = TestClient(main.app)
        response = client.get("/auth/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authenticated": False, "owner": None})

    def test_github_login_requires_config(self) -> None:
        client = TestClient(main.app)
        with patch.dict(os.environ, {}, clear=True):
            response = client.get("/auth/github/login", follow_redirects=False)
        self.assertEqual(response.status_code, 500)
        self.assertIn("GITHUB_CLIENT_ID", response.json()["detail"])

    def test_logout_clears_session_cookie(self) -> None:
        client = TestClient(main.app)
        response = client.post("/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletAuthApiTests -v
```

Expected: FAIL because routes are missing.

- [ ] **Step 3: Implement auth helpers**

Create `agent/agent_wallet_auth.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True)
class OAuthState:
    state: str
    code_verifier: str
    code_challenge: str


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def build_github_oauth_state() -> OAuthState:
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return OAuthState(state=state, code_verifier=code_verifier, code_challenge=code_challenge)


def build_github_login_url(oauth_state: OAuthState) -> str:
    client_id = require_env("GITHUB_CLIENT_ID")
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": f"{public_base_url}/auth/github/callback",
            "scope": "read:user user:email",
            "state": oauth_state.state,
            "code_challenge": oauth_state.code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


def sign_session(payload: dict[str, str]) -> str:
    secret = require_env("AUTH_SESSION_SECRET").encode("utf-8")
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    signature = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_session(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    try:
        body, signature = value.split(".", 1)
    except ValueError:
        return None
    secret = require_env("AUTH_SESSION_SECRET").encode("utf-8")
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
```

- [ ] **Step 4: Add FastAPI routes**

Modify `agent/main.py` imports:

```python
from fastapi import Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from agent_wallet_auth import build_github_login_url, build_github_oauth_state, sign_session, verify_session
from agent_wallet_state import AgentWalletStore
```

Add constants:

```python
AGENT_WALLET_STATE_PATH = os.getenv("AGENT_WALLET_STATE_PATH", "agent/data/agent_wallet_state.json")
SESSION_COOKIE = "agent_wallet_session"
OAUTH_STATE_COOKIE = "agent_wallet_oauth_state"
```

Add helpers:

```python
def get_agent_wallet_store() -> AgentWalletStore:
    return AgentWalletStore(AGENT_WALLET_STATE_PATH)

def resolve_current_owner(session_cookie: str | None) -> dict[str, object] | None:
    session = verify_session(session_cookie)
    if session is None:
        return None
    owner_id = session.get("ownerId")
    state = get_agent_wallet_store().load()
    for owner in state.owners:
        if owner.ownerId == owner_id:
            return owner.model_dump()
    return None
```

Add routes:

```python
@app.get("/auth/session")
async def auth_session(agent_wallet_session: str | None = Cookie(default=None)) -> dict[str, object]:
    owner = resolve_current_owner(agent_wallet_session)
    return {"authenticated": owner is not None, "owner": owner}


@app.get("/auth/github/login")
async def github_login() -> Response:
    try:
        oauth_state = build_github_oauth_state()
        url = build_github_login_url(oauth_state)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    response = RedirectResponse(url)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        f"{oauth_state.state}:{oauth_state.code_verifier}",
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/auth/logout")
async def auth_logout() -> Response:
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(SESSION_COOKIE)
    return response
```

- [ ] **Step 5: Run auth tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletAuthApiTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/agent_wallet_auth.py agent/main.py agent/tests/test_main_api.py
git commit -m "feat: add github oauth session endpoints"
```

## Task 5: Agent Wallet Init and Claim HTTP APIs

**Files:**
- Modify: `agent/agent_wallet_state.py`
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing API tests**

Add to `agent/tests/test_main_api.py`:

```python
class AgentWalletInitClaimApiTests(unittest.TestCase):
    def test_agent_wallet_init_calls_chain_and_returns_claim_code(self) -> None:
        client = TestClient(main.app)
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "AGENT_WALLET_STATE_PATH", os.path.join(tmp, "state.json")), patch.object(
            main,
            "call_chain_tool",
            return_value={
                "circleWalletId": "cw_123",
                "circleWalletSetId": "cws_123",
                "blockchain": "BASE-SEPOLIA",
                "walletAddress": "0x3333333333333333333333333333333333333333",
            },
        ):
            response = client.post("/agent-wallet/init", json={"agentName": "Research Agent", "agentDescription": "demo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["agent"]["claimStatus"], "unclaimed")
        self.assertRegex(response.json()["claimCode"], r".+")

    def test_agent_wallet_claim_rejects_unauthenticated_request(self) -> None:
        client = TestClient(main.app)
        response = client.post("/agent-wallet/claim", json={"claimCode": "abc"})
        self.assertEqual(response.status_code, 401)
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletInitClaimApiTests -v
```

Expected: FAIL because endpoints are missing.

- [ ] **Step 3: Add request models**

Modify `agent/main.py`:

```python
class AgentWalletInitRequest(BaseModel):
    agentName: str = Field(min_length=1)
    agentDescription: str | None = None


class AgentWalletClaimRequest(BaseModel):
    claimCode: str = Field(min_length=1)
```

- [ ] **Step 4: Add endpoints**

Modify `agent/main.py`:

```python
@app.get("/agent-wallet/state")
async def agent_wallet_state(agent_wallet_session: str | None = Cookie(default=None)) -> dict[str, object]:
    owner = resolve_current_owner(agent_wallet_session)
    state = get_agent_wallet_store().load()
    owner_id = owner["ownerId"] if owner else None
    agents = [agent.model_dump() for agent in state.agents if owner_id is None or agent.ownerId in (None, owner_id)]
    return {
        "owner": owner,
        "agents": agents,
        "services": [service.model_dump() for service in state.services],
        "payments": [payment.model_dump() for payment in state.payments],
    }


@app.post("/agent-wallet/init")
async def agent_wallet_init(request: AgentWalletInitRequest) -> dict[str, object]:
    wallet_payload = await call_chain_tool(
        "agent_wallet_init",
        {"agentName": request.agentName, "agentDescription": request.agentDescription},
    )
    agent, claim_code = get_agent_wallet_store().create_agent_wallet(
        agent_name=request.agentName,
        agent_description=request.agentDescription,
        wallet_payload=wallet_payload,
    )
    return {"agent": agent.model_dump(), "claimCode": claim_code}


@app.post("/agent-wallet/claim")
async def agent_wallet_claim(
    request: AgentWalletClaimRequest,
    agent_wallet_session: str | None = Cookie(default=None),
) -> dict[str, object]:
    owner = resolve_current_owner(agent_wallet_session)
    if owner is None:
        raise HTTPException(status_code=401, detail="Authentication is required to claim a wallet")
    try:
        agent = get_agent_wallet_store().claim_wallet(
            claim_code=request.claimCode,
            owner_id=str(owner["ownerId"]),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"agent": agent.model_dump()}
```

- [ ] **Step 5: Run API tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletInitClaimApiTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/agent_wallet_state.py agent/main.py agent/tests/test_main_api.py
git commit -m "feat: add agent wallet init and claim APIs"
```

## Task 6: Service Registration and x402 Payment Persistence

**Files:**
- Modify: `agent/agent_wallet_state.py`
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing registration and payment tests**

Add to `agent/tests/test_main_api.py`:

```python
class AgentWalletServicePaymentApiTests(unittest.TestCase):
    def test_register_service_uses_agent_wallet_pay_to(self) -> None:
        client = TestClient(main.app)
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "AGENT_WALLET_STATE_PATH", os.path.join(tmp, "state.json")), patch.object(
            main,
            "call_chain_tool",
            side_effect=[
                {"circleWalletId": "cw_123", "circleWalletSetId": "cws_123", "blockchain": "BASE-SEPOLIA", "walletAddress": "0x3333333333333333333333333333333333333333"},
                {"name": "Research Summary", "path": "/x402/agent-services/research-summary", "priceAtomic": "10000", "assetAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", "network": "eip155:84532", "payTo": "0x3333333333333333333333333333333333333333", "active": True},
            ],
        ):
            init_response = client.post("/agent-wallet/init", json={"agentName": "Research Agent"})
            agent_id = init_response.json()["agent"]["agentId"]
            response = client.post("/agent-wallet/register-service", json={"agentId": agent_id, "name": "Research Summary", "path": "/x402/agent-services/research-summary", "priceAtomic": "10000"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"]["payTo"], "0x3333333333333333333333333333333333333333")

    def test_call_service_persists_successful_payment_record(self) -> None:
        client = TestClient(main.app)
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "AGENT_WALLET_STATE_PATH", os.path.join(tmp, "state.json")), patch.object(
            main,
            "call_chain_tool",
            side_effect=[
                {"circleWalletId": "cw_123", "circleWalletSetId": "cws_123", "blockchain": "BASE-SEPOLIA", "walletAddress": "0x3333333333333333333333333333333333333333"},
                {"name": "Research Summary", "path": "/x402/agent-services/research-summary", "priceAtomic": "10000", "assetAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", "network": "eip155:84532", "payTo": "0x3333333333333333333333333333333333333333", "active": True},
                {"upstream": {"status": 200, "payload": {"ok": True}}, "payment": {"response": {"success": True, "transaction": "0xsettled", "network": "eip155:84532"}}, "decision": {"amountAtomic": "10000"}, "policy": {}},
            ],
        ):
            agent_id = client.post("/agent-wallet/init", json={"agentName": "Research Agent"}).json()["agent"]["agentId"]
            service = client.post("/agent-wallet/register-service", json={"agentId": agent_id, "name": "Research Summary", "path": "/x402/agent-services/research-summary", "priceAtomic": "10000"}).json()["service"]
            response = client.post("/agent-wallet/call-service", json={"serviceId": service["serviceId"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payment"]["txHash"], "0xsettled")
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletServicePaymentApiTests -v
```

Expected: FAIL because service registration and call endpoints are missing.

- [ ] **Step 3: Add store methods**

Add to `AgentWalletStore` in `agent/agent_wallet_state.py`:

```python
def add_service(self, *, agent_id: str, service_payload: dict) -> ServiceRegistration:
    state = self.load()
    agent = next((item for item in state.agents if item.agentId == agent_id), None)
    if agent is None:
        raise ValueError("agent not found")
    current = now_iso()
    service = ServiceRegistration(serviceId=f"service_{uuid.uuid4().hex}", agentId=agent_id, name=service_payload["name"], path=service_payload["path"], priceAtomic=service_payload["priceAtomic"], assetAddress=service_payload["assetAddress"], network=service_payload["network"], payTo=service_payload["payTo"], active=bool(service_payload["active"]), createdAt=current)
    state.services.append(service)
    self.save(state)
    return service

def add_payment(self, *, service_id: str, result: dict, request_url: str) -> PaymentRecord:
    state = self.load()
    service = next((item for item in state.services if item.serviceId == service_id), None)
    if service is None:
        raise ValueError("service not found")
    agent = next((item for item in state.agents if item.agentId == service.agentId), None)
    if agent is None:
        raise ValueError("seller agent not found")
    settlement = ((result.get("payment") or {}).get("response") or {})
    tx_hash = settlement.get("transaction")
    success = bool(settlement.get("success"))
    payment = PaymentRecord(paymentId=f"payment_{uuid.uuid4().hex}", serviceId=service_id, sellerAgentId=agent.agentId, sellerWalletAddress=agent.walletAddress, amountAtomic=service.priceAtomic, assetAddress=service.assetAddress, network=service.network, status="settled" if success else "failed", requestUrl=request_url, resultSummary=result, txHash=tx_hash, settlementReference=tx_hash, createdAt=now_iso())
    state.payments.append(payment)
    self.save(state)
    return payment
```

- [ ] **Step 4: Add request models and endpoints**

Modify `agent/main.py`:

```python
class AgentWalletRegisterServiceRequest(BaseModel):
    agentId: str
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    priceAtomic: str = Field(min_length=1)


class AgentWalletCallServiceRequest(BaseModel):
    serviceId: str
```

Add endpoints:

```python
@app.post("/agent-wallet/register-service")
async def agent_wallet_register_service(request: AgentWalletRegisterServiceRequest) -> dict[str, object]:
    store = get_agent_wallet_store()
    state = store.load()
    agent = next((item for item in state.agents if item.agentId == request.agentId), None)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    service_payload = await call_chain_tool(
        "agent_wallet_register_x402_service",
        {"name": request.name, "path": request.path, "priceAtomic": request.priceAtomic, "payTo": agent.walletAddress},
    )
    service = store.add_service(agent_id=request.agentId, service_payload=service_payload)
    return {"service": service.model_dump()}


@app.post("/agent-wallet/call-service")
async def agent_wallet_call_service(request: AgentWalletCallServiceRequest) -> dict[str, object]:
    store = get_agent_wallet_store()
    state = store.load()
    service = next((item for item in state.services if item.serviceId == request.serviceId), None)
    if service is None:
        raise HTTPException(status_code=404, detail="service not found")
    base_url = os.getenv("X402_SELLER_BASE_URL", "http://x402-seller:8000")
    request_url = f"{base_url}{service.path}"
    result = await call_chain_tool("agent_wallet_call_x402_service", {"url": request_url, "method": "GET"})
    payment = store.add_payment(service_id=request.serviceId, result=result, request_url=request_url)
    return {"payment": payment.model_dump(), "result": result}
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletServicePaymentApiTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/agent_wallet_state.py agent/main.py agent/tests/test_main_api.py
git commit -m "feat: persist agent wallet services and x402 payments"
```

## Task 7: x402 Seller Named Agent Service Endpoint

**Files:**
- Modify: `x402-seller/main.py`
- Modify: `x402-seller/x402_seller.py`
- Modify: `x402-seller/tests/test_x402_seller.py`

- [ ] **Step 1: Write failing seller tests**

Add to `x402-seller/tests/test_x402_seller.py`:

```python
def test_agent_service_resource_returns_standard_402_header(self) -> None:
    client = TestClient(main.app)
    response = client.get("/x402/agent-services/research-summary")
    self.assertEqual(response.status_code, 402)
    self.assertIn("PAYMENT-REQUIRED", response.headers)
    self.assertEqual(response.json()["accepts"][0]["payTo"], "0x2222222222222222222222222222222222222222")

def test_agent_service_returns_structured_result_on_success(self) -> None:
    service = X402SellerService(
        X402SellerConfig(
            pay_to="0x2222222222222222222222222222222222222222",
            facilitator_url="http://facilitator.test",
            price="$0.01",
        )
    )
    service.verify_payment = AsyncMock(return_value={"isValid": True})
    service.settle_payment = AsyncMock(return_value={"success": True, "transaction": "0xsettled", "network": "eip155:84532"})
    client = TestClient(main.app)

    with patch.object(main, "get_x402_seller_service", return_value=service):
        response = client.get("/x402/agent-services/research-summary", headers={"PAYMENT-SIGNATURE": encode_header({"x402Version": 2, "accepted": {"network": "eip155:84532", "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", "amount": "10000", "payTo": "0x2222222222222222222222222222222222222222", "scheme": "exact", "maxTimeoutSeconds": 300, "extra": {"name": "USDC", "version": "2"}}, "payload": {"authorization": {"from": "0x1111111111111111111111111111111111111111"}}})})

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["service"], "research-summary")
    self.assertEqual(response.json()["settlement"]["transaction"], "0xsettled")
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd x402-seller && python -m unittest tests.test_x402_seller.X402SellerTests -v
```

Expected: FAIL because the named endpoint does not exist.

- [ ] **Step 3: Add endpoint**

Modify `x402-seller/main.py`:

```python
@app.get("/x402/agent-services/research-summary")
async def x402_research_summary_service(request: Request):
    seller = get_x402_seller_service()
    result = await seller.handle_request(request)
    if result.status_code != 200:
        return result
    payload = result.body
    return JSONResponse(
        {
            "ok": True,
            "service": "research-summary",
            "summary": "Agent Wallet MVP paid research summary",
            "settlement": json.loads(payload.decode("utf-8")).get("settlement", {}),
        },
        headers=dict(result.headers),
    )
```

If `handle_request` currently returns a plain dict or `JSONResponse`, adapt this endpoint to preserve the existing 402 behavior and only wrap the successful paid body.

- [ ] **Step 4: Run seller tests**

Run:

```bash
cd x402-seller && python -m unittest tests.test_x402_seller.X402SellerTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add x402-seller/main.py x402-seller/x402_seller.py x402-seller/tests/test_x402_seller.py
git commit -m "feat: add named x402 agent service"
```

## Task 8: Guided Agent Wallet UI Panel

**Files:**
- Modify: `agent/web/chat.html`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing HTML smoke test**

Add to `agent/tests/test_main_api.py`:

```python
class AgentWalletUiTests(unittest.TestCase):
    def test_chat_page_contains_agent_wallet_panel_and_api_calls(self) -> None:
        client = TestClient(main.app)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Wallet MVP", html)
        self.assertIn("/auth/session", html)
        self.assertIn("/agent-wallet/init", html)
        self.assertIn("/agent-wallet/claim", html)
        self.assertIn("/agent-wallet/register-service", html)
        self.assertIn("/agent-wallet/call-service", html)
```

- [ ] **Step 2: Run failing test**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletUiTests -v
```

Expected: FAIL because the UI does not contain the panel.

- [ ] **Step 3: Add panel markup**

Modify `agent/web/chat.html` by adding a new section near the existing observability/chat layout:

```html
<section class="agent-wallet-panel" aria-label="Agent Wallet MVP">
  <header class="panel-header">
    <h2>Agent Wallet MVP</h2>
    <button id="walletSignInButton" type="button">Sign in with GitHub</button>
    <button id="walletLogoutButton" type="button">Sign out</button>
  </header>
  <div id="walletAuthState" class="status-line">Checking session...</div>
  <div class="wallet-step-grid">
    <form id="walletCreateForm" class="wallet-step">
      <h3>Create Agent Wallet</h3>
      <input id="walletAgentName" name="agentName" value="Research Agent" />
      <input id="walletAgentDescription" name="agentDescription" value="Demo paid research agent" />
      <button type="submit">Create Wallet</button>
      <output id="walletCreateResult"></output>
    </form>
    <form id="walletClaimForm" class="wallet-step">
      <h3>Claim Wallet</h3>
      <input id="walletClaimCode" name="claimCode" placeholder="One-time claim code" />
      <button type="submit">Claim</button>
      <output id="walletClaimResult"></output>
    </form>
    <form id="walletServiceForm" class="wallet-step">
      <h3>Register x402 Service</h3>
      <input id="walletServiceName" value="Research Summary" />
      <input id="walletServicePath" value="/x402/agent-services/research-summary" />
      <input id="walletServicePrice" value="10000" />
      <button type="submit">Register</button>
      <output id="walletServiceResult"></output>
    </form>
    <div class="wallet-step">
      <h3>Trigger A2A Payment</h3>
      <button id="walletCallServiceButton" type="button">Call Paid Service</button>
      <output id="walletPaymentResult"></output>
    </div>
  </div>
  <pre id="walletStateSummary"></pre>
</section>
```

- [ ] **Step 4: Add UI JavaScript**

Add JavaScript functions in the existing inline script:

```js
let selectedWalletAgentId = null;
let selectedWalletServiceId = null;

async function walletApi(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || payload.error || "Agent Wallet request failed");
  return payload;
}

async function refreshAgentWalletState() {
  const [session, state] = await Promise.all([
    walletApi("/auth/session"),
    walletApi("/agent-wallet/state"),
  ]);
  document.getElementById("walletAuthState").textContent = session.authenticated
    ? `Signed in as ${session.owner.login}`
    : "Not signed in";
  document.getElementById("walletStateSummary").textContent = JSON.stringify(state, null, 2);
}

document.getElementById("walletSignInButton").addEventListener("click", () => {
  window.location.href = "/auth/github/login";
});

document.getElementById("walletLogoutButton").addEventListener("click", async () => {
  await walletApi("/auth/logout", { method: "POST" });
  await refreshAgentWalletState();
});

document.getElementById("walletCreateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = await walletApi("/agent-wallet/init", {
    method: "POST",
    body: JSON.stringify({
      agentName: document.getElementById("walletAgentName").value,
      agentDescription: document.getElementById("walletAgentDescription").value,
    }),
  });
  selectedWalletAgentId = payload.agent.agentId;
  document.getElementById("walletClaimCode").value = payload.claimCode;
  document.getElementById("walletCreateResult").textContent = `Created ${payload.agent.walletAddress}`;
  await refreshAgentWalletState();
});

document.getElementById("walletClaimForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = await walletApi("/agent-wallet/claim", {
    method: "POST",
    body: JSON.stringify({ claimCode: document.getElementById("walletClaimCode").value }),
  });
  selectedWalletAgentId = payload.agent.agentId;
  document.getElementById("walletClaimResult").textContent = payload.agent.claimStatus;
  await refreshAgentWalletState();
});

document.getElementById("walletServiceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = await walletApi("/agent-wallet/register-service", {
    method: "POST",
    body: JSON.stringify({
      agentId: selectedWalletAgentId,
      name: document.getElementById("walletServiceName").value,
      path: document.getElementById("walletServicePath").value,
      priceAtomic: document.getElementById("walletServicePrice").value,
    }),
  });
  selectedWalletServiceId = payload.service.serviceId;
  document.getElementById("walletServiceResult").textContent = `${payload.service.name} -> ${payload.service.payTo}`;
  await refreshAgentWalletState();
});

document.getElementById("walletCallServiceButton").addEventListener("click", async () => {
  const payload = await walletApi("/agent-wallet/call-service", {
    method: "POST",
    body: JSON.stringify({ serviceId: selectedWalletServiceId }),
  });
  document.getElementById("walletPaymentResult").textContent = `${payload.payment.status}: ${payload.payment.txHash || "no tx hash"}`;
  await refreshAgentWalletState();
});

refreshAgentWalletState().catch((error) => {
  document.getElementById("walletAuthState").textContent = error.message;
});
```

- [ ] **Step 5: Add CSS without disrupting current layout**

Add CSS near existing panel styles:

```css
.agent-wallet-panel {
  border-top: 1px solid var(--border-color);
  padding: 16px;
}
.wallet-step-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.wallet-step {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 12px;
}
.wallet-step input,
.wallet-step button {
  width: 100%;
  min-height: 36px;
  margin-top: 8px;
}
#walletStateSummary {
  max-height: 260px;
  overflow: auto;
}
```

- [ ] **Step 6: Run UI smoke test**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletUiTests -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: add agent wallet guided UI panel"
```

## Task 9: Reset Endpoint, Config, Docs, and End-to-End Verification

**Files:**
- Modify: `agent/main.py`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing reset test**

Add to `agent/tests/test_main_api.py`:

```python
class AgentWalletResetApiTests(unittest.TestCase):
    def test_agent_wallet_reset_removes_local_demo_state(self) -> None:
        client = TestClient(main.app)
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "AGENT_WALLET_STATE_PATH", os.path.join(tmp, "state.json")), patch.object(
            main,
            "call_chain_tool",
            return_value={"circleWalletId": "cw_123", "circleWalletSetId": "cws_123", "blockchain": "BASE-SEPOLIA", "walletAddress": "0x3333333333333333333333333333333333333333"},
        ):
            client.post("/agent-wallet/init", json={"agentName": "Research Agent"})
            response = client.post("/agent-wallet/reset")
            state = client.get("/agent-wallet/state").json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(state["agents"], [])
```

- [ ] **Step 2: Run failing reset test**

Run:

```bash
cd agent && python -m unittest tests.test_main_api.AgentWalletResetApiTests -v
```

Expected: FAIL because reset is missing.

- [ ] **Step 3: Implement reset**

Add to `agent/main.py`:

```python
@app.post("/agent-wallet/reset")
async def agent_wallet_reset() -> dict[str, bool]:
    if os.path.exists(AGENT_WALLET_STATE_PATH):
        os.remove(AGENT_WALLET_STATE_PATH)
    return {"ok": True}
```

- [ ] **Step 4: Add runtime env vars to docker compose**

Modify `docker-compose.yml` under `agent.environment`:

```yaml
      GITHUB_CLIENT_ID: ${GITHUB_CLIENT_ID:-}
      GITHUB_CLIENT_SECRET: ${GITHUB_CLIENT_SECRET:-}
      AUTH_SESSION_SECRET: ${AUTH_SESSION_SECRET:-dev-agent-wallet-session-secret}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-http://localhost:8000}
      AGENT_WALLET_STATE_PATH: ${AGENT_WALLET_STATE_PATH:-/app/data/agent_wallet_state.json}
      X402_SELLER_BASE_URL: ${X402_SELLER_BASE_URL:-http://x402-seller:8000}
```

Modify `chain.environment`:

```yaml
      CIRCLE_API_KEY: ${CIRCLE_API_KEY:-}
      CIRCLE_ENTITY_SECRET: ${CIRCLE_ENTITY_SECRET:-}
      CIRCLE_ENTITY_SECRET_CIPHERTEXT: ${CIRCLE_ENTITY_SECRET_CIPHERTEXT:-}
      CIRCLE_WALLET_SET_ID: ${CIRCLE_WALLET_SET_ID:-}
      CIRCLE_BASE_URL: ${CIRCLE_BASE_URL:-https://api.circle.com/v1/w3s}
```

- [ ] **Step 5: Document the MVP flow**

Add to `README.md`:

```markdown
## Agent Wallet MVP x402 Demo

The Agent Wallet MVP adds a guided flow to the existing web console:

1. sign in with GitHub OAuth
2. create a Circle sandbox Agent Wallet
3. claim it with a one-time claim code
4. register `/x402/agent-services/research-summary`
5. trigger an x402 paid service call on Base Sepolia
6. inspect local payment history

Required configuration:

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `AUTH_SESSION_SECRET`
- `PUBLIC_BASE_URL`
- `CIRCLE_API_KEY`
- `CIRCLE_ENTITY_SECRET`
- `CIRCLE_ENTITY_SECRET_CIPHERTEXT`
- `CIRCLE_WALLET_SET_ID`
- `X402_BUYER_PRIVATE_KEY`
- `X402_NETWORK=eip155:84532`
- `X402_USDC_ASSET_ADDRESS=0x036CbD53842c5426634e7929541eC2318f3dCF7e`

Local demo state is stored at `AGENT_WALLET_STATE_PATH`, defaulting to `/app/data/agent_wallet_state.json` in Docker.
```

- [ ] **Step 6: Run full verification**

Run:

```bash
cd chain && npm test
cd chain && npm run typecheck
cd agent && python -m unittest
cd x402-seller && python -m unittest
docker compose config
```

Expected: all commands PASS.

- [ ] **Step 7: Optional local stack smoke test**

Run:

```bash
docker compose up -d --build
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/auth/session
```

Expected:

```json
{"authenticated":false,"owner":null}
```

The exact `/health` payload may include additional existing service fields; it must return HTTP 200.

- [ ] **Step 8: Commit**

```bash
git add agent/main.py docker-compose.yml README.md agent/tests/test_main_api.py
git commit -m "docs: wire agent wallet MVP configuration and verification"
```

## Self-Review Checklist

- Spec coverage:
  - GitHub OAuth: Task 4.
  - Owner/session records: Tasks 3 and 4.
  - Circle-backed wallet provisioning boundary: Tasks 1 and 2.
  - One-time hashed claim flow: Tasks 3 and 5.
  - Local JSON state model: Task 3.
  - Agent Wallet MCP tools: Tasks 1 and 2.
  - x402 service registration and A2A call: Tasks 6 and 7.
  - Guided UI: Task 8.
  - Reset endpoint and config/docs: Task 9.
- Safety coverage:
  - Plaintext claim code is returned once and not persisted.
  - Claim requires authenticated owner.
  - x402 spending continues through the existing chain `PolicyGuard`.
  - Missing provider credentials produce explicit errors rather than fake success.
- Verification coverage:
  - Chain unit/MCP tests and typecheck.
  - Agent Python API tests.
  - x402 seller Python tests.
  - Docker compose config validation.
