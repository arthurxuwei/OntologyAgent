import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import type { AgentWalletBinding, AgentWalletStatusResult } from "../domain/types.js";
import { normalizeAddress } from "../security.js";
import type { CircleWalletRecord } from "./circle-wallet-service.js";

export type AgentWalletState = {
  wallets: CircleWalletRecord[];
  agentWalletBindings?: AgentWalletBinding[];
  updatedAt?: string;
};

export type SaveAgentWalletBindingCommand = {
  agentName: string;
  agentId?: string;
  email?: string;
  circleWalletId?: string | null;
  circleWalletSetId?: string | null;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  mode: "mock" | "circle";
  accountType?: "SCA" | "EOA";
};

export class AgentWalletStateStore {
  constructor(private readonly statePath?: string) {}

  async findByAgentName(agentName: string): Promise<AgentWalletStatusResult | null> {
    const normalized = normalizeAgentName(agentName);
    if (!this.statePath || !normalized) {
      return null;
    }

    const state = await this.read();
    const wallet = state.wallets.find(
      (entry) => isUsableWallet(entry) && normalizeAgentName(entry.agentName) === normalized,
    );
    return wallet ? toStatusResult(wallet) : null;
  }

  async findByWallet(command: {
    walletAddress?: string;
    circleWalletId?: string;
  }): Promise<AgentWalletStatusResult | null> {
    if (!this.statePath) {
      return null;
    }
    const normalizedCircleWalletId = normalizeOptional(command.circleWalletId);
    let normalizedAddress: string | null = null;
    if (command.walletAddress !== undefined && command.walletAddress.trim()) {
      normalizedAddress = normalizeAddress(command.walletAddress).toLowerCase();
    }

    const state = await this.read();
    const wallet = state.wallets.find((entry) => {
      if (!isUsableWallet(entry)) {
        return false;
      }
      if (normalizedCircleWalletId && entry.circleWalletId === normalizedCircleWalletId) {
        return true;
      }
      if (
        normalizedAddress &&
        normalizeAddress(entry.walletAddress).toLowerCase() === normalizedAddress
      ) {
        return true;
      }
      return false;
    });
    return wallet ? toStatusResult(wallet) : null;
  }

  async findUnboundWallet(): Promise<AgentWalletStatusResult | null> {
    if (!this.statePath) {
      return null;
    }

    const state = await this.read();
    const boundCircleWalletIds = new Set(
      (state.agentWalletBindings ?? [])
        .map((entry) => normalizeOptional(entry.circleWalletId))
        .filter((entry): entry is string => entry !== null),
    );
    const boundWalletAddresses = new Set(
      (state.agentWalletBindings ?? []).map((entry) =>
        normalizeAddress(entry.walletAddress).toLowerCase(),
      ),
    );
    const wallet = state.wallets.find((entry) => {
      if (boundCircleWalletIds.has(entry.circleWalletId)) {
        return false;
      }
      if (boundWalletAddresses.has(normalizeAddress(entry.walletAddress).toLowerCase())) {
        return false;
      }
      if (!isUsableWallet(entry)) {
        return false;
      }
      return true;
    });
    return wallet ? toStatusResult(wallet) : null;
  }

  async findBindingByAgentId(agentId: string): Promise<AgentWalletBinding | null> {
    const normalized = normalizeOptional(agentId);
    if (!this.statePath || !normalized) {
      return null;
    }

    const state = await this.read();
    return (
      (state.agentWalletBindings ?? []).find(
        (entry) => normalizeOptional(entry.agentId) === normalized,
      ) ?? null
    );
  }

  async findBindingByAgentName(agentName: string): Promise<AgentWalletBinding | null> {
    const normalized = normalizeAgentName(agentName);
    if (!this.statePath || !normalized) {
      return null;
    }

    const state = await this.read();
    return (
      (state.agentWalletBindings ?? []).find(
        (entry) => normalizeAgentName(entry.agentName) === normalized,
      ) ?? null
    );
  }

  async saveWallets(wallets: CircleWalletRecord[]): Promise<AgentWalletState> {
    if (!this.statePath) {
      throw new Error("AGENT_WALLET_STATE_PATH is not configured");
    }

    const previous = await this.read();
    const state: AgentWalletState = {
      wallets: dedupeWallets(wallets),
      agentWalletBindings: previous.agentWalletBindings ?? [],
      updatedAt: new Date().toISOString(),
    };
    await this.write(state);
    return state;
  }

  async saveBinding(command: SaveAgentWalletBindingCommand): Promise<AgentWalletBinding | null> {
    if (!this.statePath) {
      return null;
    }

    const state = await this.read();
    const binding: AgentWalletBinding = {
      agentName: command.agentName.trim(),
      agentId: normalizeOptional(command.agentId),
      email: normalizeEmail(command.email),
      walletAddress: normalizeAddress(command.walletAddress).toLowerCase(),
      circleWalletId: normalizeOptional(command.circleWalletId),
      circleWalletSetId: normalizeOptional(command.circleWalletSetId),
      blockchain: command.blockchain,
      mode: command.mode,
      ...(command.accountType === "SCA" || command.accountType === "EOA"
        ? { accountType: command.accountType }
        : {}),
      updatedAt: new Date().toISOString(),
    };
    state.agentWalletBindings = upsertBinding(state.agentWalletBindings ?? [], binding);
    state.updatedAt = binding.updatedAt;
    await this.write(state);
    return binding;
  }

  private async read(): Promise<AgentWalletState> {
    if (!this.statePath) {
      return { wallets: [] };
    }

    try {
      const payload = JSON.parse(await readFile(this.statePath, "utf-8")) as unknown;
      if (!isRecord(payload) || !Array.isArray(payload.wallets)) {
        return { wallets: [] };
      }
      return {
        wallets: payload.wallets.filter(isWalletRecord),
        agentWalletBindings: Array.isArray(payload.agentWalletBindings)
          ? payload.agentWalletBindings.filter(isAgentWalletBinding)
          : [],
        updatedAt: typeof payload.updatedAt === "string" ? payload.updatedAt : undefined,
      };
    } catch (error) {
      if (isNodeError(error) && error.code === "ENOENT") {
        return { wallets: [] };
      }
      throw error;
    }
  }

  private async write(state: AgentWalletState): Promise<void> {
    if (!this.statePath) {
      return;
    }

    await mkdir(dirname(this.statePath), { recursive: true });
    await writeFile(this.statePath, `${JSON.stringify(state, null, 2)}\n`, "utf-8");
  }
}

function toStatusResult(wallet: CircleWalletRecord): AgentWalletStatusResult {
  return {
    circleWalletId: wallet.circleWalletId,
    circleWalletSetId: wallet.circleWalletSetId,
    blockchain: wallet.blockchain,
    walletAddress: normalizeAddress(wallet.walletAddress).toLowerCase(),
    status: "available",
    balances: {},
    mode: wallet.mode,
    ...(wallet.accountType === "SCA" || wallet.accountType === "EOA"
      ? { accountType: wallet.accountType }
      : {}),
  };
}

function isUsableWallet(wallet: Pick<CircleWalletRecord, "mode" | "accountType">): boolean {
  return wallet.mode !== "circle" || wallet.accountType === "SCA";
}

function dedupeWallets(wallets: CircleWalletRecord[]): CircleWalletRecord[] {
  const byId = new Map<string, CircleWalletRecord>();
  for (const wallet of wallets) {
    byId.set(wallet.circleWalletId, {
      ...wallet,
      walletAddress: normalizeAddress(wallet.walletAddress).toLowerCase(),
    });
  }
  return [...byId.values()];
}

function upsertBinding(
  bindings: AgentWalletBinding[],
  binding: AgentWalletBinding,
): AgentWalletBinding[] {
  const normalizedAgentName = normalizeAgentName(binding.agentName);
  const normalizedAgentId = normalizeOptional(binding.agentId);
  const normalizedEmail = normalizeEmail(binding.email);
  const filtered = bindings.filter((entry) => {
    if (normalizeAgentName(entry.agentName) === normalizedAgentName) {
      return false;
    }
    if (normalizedAgentId && normalizeOptional(entry.agentId) === normalizedAgentId) {
      return false;
    }
    if (normalizedEmail && normalizeEmail(entry.email) === normalizedEmail) {
      return false;
    }
    return true;
  });
  return [...filtered, binding];
}

function normalizeAgentName(agentName: string): string {
  return agentName.trim().toLowerCase();
}

function normalizeOptional(value: string | null | undefined): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function normalizeEmail(value: string | null | undefined): string | null {
  return normalizeOptional(value)?.toLowerCase() ?? null;
}

function isWalletRecord(value: unknown): value is CircleWalletRecord {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.agentName === "string" &&
    typeof value.circleWalletId === "string" &&
    (typeof value.circleWalletSetId === "string" || value.circleWalletSetId === null) &&
    value.blockchain === "BASE-SEPOLIA" &&
    typeof value.walletAddress === "string" &&
    (
      value.accountType === undefined ||
      value.accountType === "SCA" ||
      value.accountType === "EOA"
    ) &&
    (value.mode === "circle" || value.mode === "mock")
  );
}

function isAgentWalletBinding(value: unknown): value is AgentWalletBinding {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.agentName === "string" &&
    (typeof value.agentId === "string" || value.agentId === null) &&
    (typeof value.email === "string" || value.email === null) &&
    typeof value.walletAddress === "string" &&
    (typeof value.circleWalletId === "string" || value.circleWalletId === null) &&
    (typeof value.circleWalletSetId === "string" || value.circleWalletSetId === null) &&
    value.blockchain === "BASE-SEPOLIA" &&
    (value.mode === "circle" || value.mode === "mock") &&
    (
      value.accountType === undefined ||
      value.accountType === "SCA" ||
      value.accountType === "EOA"
    ) &&
    typeof value.updatedAt === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
