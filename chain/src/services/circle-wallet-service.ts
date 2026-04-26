import { createHash, randomUUID } from "node:crypto";

import type { AppConfig } from "../config.js";
import { AppError } from "../domain/errors.js";
import { normalizeAddress } from "../security.js";

export type CircleWalletCreateResult = {
  circleWalletId: string;
  circleWalletSetId: string;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  mode: "mock" | "circle";
};

type CircleWalletResponseWallet = {
  id?: unknown;
  address?: unknown;
  blockchain?: unknown;
  walletSetId?: unknown;
};

export type CircleEntitySecretCiphertextFactory = (
  entitySecret: string,
) => string | Promise<string>;

export type CircleWalletServiceOptions = {
  createEntitySecretCiphertext?: CircleEntitySecretCiphertextFactory;
  fetchImpl?: typeof fetch;
};

export class CircleWalletService {
  private readonly createEntitySecretCiphertext?: CircleEntitySecretCiphertextFactory;
  private readonly fetchImpl: typeof fetch;

  constructor(
    private readonly config: AppConfig,
    options: CircleWalletServiceOptions = {},
  ) {
    this.createEntitySecretCiphertext = options.createEntitySecretCiphertext;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

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

    const apiKey = this.config.circle.apiKey;
    const walletSetId = this.config.circle.walletSetId;
    const entitySecret = this.config.circle.entitySecret;
    const staticEntitySecretCiphertext = this.config.circle.entitySecretCiphertext;
    if (staticEntitySecretCiphertext && !entitySecret) {
      throw new AppError(
        "CONFIG_ERROR",
        "static CIRCLE_ENTITY_SECRET_CIPHERTEXT cannot be reused for live wallet creation; configure CIRCLE_ENTITY_SECRET and per-request ciphertext generation",
        500,
      );
    }

    if (!apiKey || !walletSetId || !entitySecret) {
      throw new AppError(
        "CONFIG_ERROR",
        "CIRCLE_API_KEY, CIRCLE_WALLET_SET_ID, and CIRCLE_ENTITY_SECRET are required",
        500,
      );
    }

    if (!this.createEntitySecretCiphertext) {
      throw new AppError(
        "CONFIG_ERROR",
        "per-request Circle entitySecretCiphertext generation is required for live wallet creation; static CIRCLE_ENTITY_SECRET_CIPHERTEXT cannot be reused",
        500,
      );
    }

    const entitySecretCiphertext = await this.createEntitySecretCiphertext(entitySecret);
    const response = await this.fetchImpl(`${this.config.circle.baseUrl}/developer/wallets`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        idempotencyKey: randomUUID(),
        walletSetId,
        entitySecretCiphertext,
        blockchains: ["BASE-SEPOLIA"],
        count: 1,
        metadata: [
          {
            name: agentName,
            refId: slug(agentName),
          },
        ],
      }),
    });
    const payload = await parseJsonPayload(response);

    if (!response.ok) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle wallet creation failed with HTTP ${response.status}`,
        response.status,
        payload,
      );
    }

    const wallet = extractFirstWallet(payload);
    if (typeof wallet?.id !== "string" || typeof wallet.address !== "string") {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle wallet response did not include id and address",
        502,
        payload,
      );
    }

    if (wallet.blockchain !== undefined && wallet.blockchain !== "BASE-SEPOLIA") {
      throw new AppError(
        "NETWORK_MISMATCH",
        `Circle returned unsupported blockchain ${String(wallet.blockchain)}`,
        502,
        payload,
      );
    }

    const walletAddress = normalizeCircleAddress(wallet.address, payload);
    return {
      circleWalletId: wallet.id,
      circleWalletSetId:
        typeof wallet.walletSetId === "string" ? wallet.walletSetId : walletSetId,
      blockchain: "BASE-SEPOLIA",
      walletAddress,
      mode: "circle",
    };
  }
}

export function slug(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  return normalized.length > 0 ? normalized : "agent";
}

export function mockAddress(seed: string): `0x${string}` {
  const digest = createHash("sha256").update(seed).digest("hex");
  return `0x${digest.slice(0, 40)}`;
}

async function parseJsonPayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractFirstWallet(payload: unknown): CircleWalletResponseWallet | undefined {
  if (!isRecord(payload)) {
    return undefined;
  }

  const data = payload.data;
  if (!isRecord(data) || !Array.isArray(data.wallets)) {
    return undefined;
  }

  const [wallet] = data.wallets;
  return isRecord(wallet) ? wallet : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeCircleAddress(address: string, payload: unknown): string {
  try {
    return normalizeAddress(address).toLowerCase();
  } catch (error) {
    throw new AppError(
      "UPSTREAM_REQUEST_FAILED",
      "Circle wallet response included an invalid address",
      502,
      {
        payload,
        cause: error instanceof Error ? error.message : String(error),
      },
    );
  }
}
