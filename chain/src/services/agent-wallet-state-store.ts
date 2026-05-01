import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import type { AgentWalletStatusResult } from "../domain/types.js";
import { normalizeAddress } from "../security.js";
import type { CircleWalletRecord } from "./circle-wallet-service.js";

export type AgentWalletState = {
  wallets: CircleWalletRecord[];
  updatedAt?: string;
};

export class AgentWalletStateStore {
  constructor(private readonly statePath?: string) {}

  async findByAgentName(agentName: string): Promise<AgentWalletStatusResult | null> {
    const normalized = normalizeAgentName(agentName);
    if (!this.statePath || !normalized) {
      return null;
    }

    const state = await this.read();
    const wallet = state.wallets.find((entry) => normalizeAgentName(entry.agentName) === normalized);
    return wallet ? toStatusResult(wallet) : null;
  }

  async saveWallets(wallets: CircleWalletRecord[]): Promise<AgentWalletState> {
    if (!this.statePath) {
      throw new Error("AGENT_WALLET_STATE_PATH is not configured");
    }

    const state: AgentWalletState = {
      wallets: dedupeWallets(wallets),
      updatedAt: new Date().toISOString(),
    };
    await mkdir(dirname(this.statePath), { recursive: true });
    await writeFile(this.statePath, `${JSON.stringify(state, null, 2)}\n`, "utf-8");
    return state;
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
        updatedAt: typeof payload.updatedAt === "string" ? payload.updatedAt : undefined,
      };
    } catch (error) {
      if (isNodeError(error) && error.code === "ENOENT") {
        return { wallets: [] };
      }
      throw error;
    }
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
  };
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

function normalizeAgentName(agentName: string): string {
  return agentName.trim().toLowerCase();
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
    (value.mode === "circle" || value.mode === "mock")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
