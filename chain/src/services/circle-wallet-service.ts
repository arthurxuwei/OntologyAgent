import { createHash, randomUUID } from "node:crypto";
import {
  initiateDeveloperControlledWalletsClient,
  type CircleDeveloperControlledWalletsClient,
} from "@circle-fin/developer-controlled-wallets";

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

export type CircleWalletRecord = Omit<CircleWalletCreateResult, "circleWalletSetId"> & {
  agentName: string;
  circleWalletSetId: string | null;
};

type CircleWalletResponseWallet = {
  id?: unknown;
  address?: unknown;
  blockchain?: unknown;
  walletSetId?: unknown;
  name?: unknown;
  refId?: unknown;
};

export type CircleEntitySecretCiphertextFactory = (
  entitySecret: string,
) => string | Promise<string>;

export type CircleWalletServiceOptions = {
  createEntitySecretCiphertext?: CircleEntitySecretCiphertextFactory;
  fetchImpl?: typeof fetch;
  client?: Pick<
    CircleDeveloperControlledWalletsClient,
    "createTransaction" | "getTransaction" | "requestTestnetTokens"
  > | {
    createTransaction(input: unknown): Promise<any>;
    getTransaction(input: unknown): Promise<any>;
    requestTestnetTokens(input: unknown): Promise<any>;
  };
};

export class CircleWalletService {
  private readonly createEntitySecretCiphertext?: CircleEntitySecretCiphertextFactory;
  private readonly fetchImpl: typeof fetch;
  private readonly client?: Pick<
    CircleDeveloperControlledWalletsClient,
    "createTransaction" | "getTransaction" | "requestTestnetTokens"
  > | {
    createTransaction(input: unknown): Promise<any>;
    getTransaction(input: unknown): Promise<any>;
    requestTestnetTokens(input: unknown): Promise<any>;
  };

  constructor(
    private readonly config: AppConfig,
    options: CircleWalletServiceOptions = {},
  ) {
    this.createEntitySecretCiphertext = options.createEntitySecretCiphertext;
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.client = options.client;
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

  async listWallets(): Promise<CircleWalletRecord[]> {
    const apiKey = this.config.circle.apiKey;
    const walletSetId = this.config.circle.walletSetId;
    if (!apiKey || !walletSetId) {
      throw new AppError(
        "CONFIG_ERROR",
        "CIRCLE_API_KEY and CIRCLE_WALLET_SET_ID are required",
        500,
      );
    }

    const query = new URLSearchParams({
      walletSetId,
      blockchain: this.config.circle.blockchain,
    });
    const response = await this.fetchImpl(`${this.config.circle.baseUrl}/wallets?${query}`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
    });
    const payload = await parseJsonPayload(response);

    if (!response.ok) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle wallet listing failed with HTTP ${response.status}`,
        response.status,
        payload,
      );
    }

    return extractWallets(payload).map((wallet) => normalizeCircleWalletRecord(wallet, payload));
  }

  async createTransfer(command: {
    walletId: string;
    destinationAddress: string;
    amount: string;
    tokenId?: string;
    tokenAddress: string;
    refId?: string;
  }): Promise<unknown> {
    if (this.config.network.mockChain) {
      return {
        transaction: {
          id: `mock-circle-transfer-${Date.now().toString(16)}`,
          txHash: mockTransactionHash(
            command.walletId,
            command.destinationAddress,
            command.amount,
            command.tokenAddress,
          ),
          state: "COMPLETE",
        },
      };
    }

    const client = this.requireClient();
    return (
      await client.createTransaction({
        walletId: command.walletId,
        destinationAddress: normalizeAddress(command.destinationAddress),
        amount: [command.amount],
        ...(command.tokenId
          ? { tokenId: command.tokenId }
          : {
              tokenAddress: command.tokenAddress,
              blockchain: this.config.circle.blockchain,
            }),
        refId: command.refId,
        fee: {
          type: "level",
          config: {
            feeLevel: "MEDIUM",
          },
        },
      } as any)
    ).data;
  }

  async createNativeTransfer(command: {
    walletId: string;
    destinationAddress: string;
    amountEth: string;
    refId?: string;
  }): Promise<unknown> {
    return this.createTransfer({
      walletId: command.walletId,
      destinationAddress: command.destinationAddress,
      amount: command.amountEth,
      tokenId: undefined,
      tokenAddress: "",
      refId: command.refId,
    });
  }

  async getTransaction(transactionId: string): Promise<unknown> {
    if (this.config.network.mockChain) {
      return {
        transaction: {
          id: transactionId,
          state: "COMPLETE",
        },
      };
    }

    const client = this.requireClient();
    return (await client.getTransaction({ id: transactionId })).data;
  }

  async requestTestnetFunds(command: {
    walletAddress: string;
    native: boolean;
    usdc: boolean;
  }): Promise<void> {
    const client = this.requireClient();
    await client.requestTestnetTokens({
      address: normalizeAddress(command.walletAddress),
      blockchain: this.config.circle.blockchain,
      native: command.native,
      usdc: command.usdc,
    });
  }

  private requireClient(): Pick<
    CircleDeveloperControlledWalletsClient,
    "createTransaction" | "getTransaction" | "requestTestnetTokens"
  > | {
    createTransaction(input: unknown): Promise<any>;
    getTransaction(input: unknown): Promise<any>;
    requestTestnetTokens(input: unknown): Promise<any>;
  } {
    if (this.client) {
      return this.client;
    }

    const apiKey = this.config.circle.apiKey;
    const entitySecret = this.config.circle.entitySecret;
    if (!apiKey || !entitySecret) {
      throw new AppError(
        "CONFIG_ERROR",
        "CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET are required",
        500,
      );
    }

    return initiateDeveloperControlledWalletsClient({
      apiKey,
      entitySecret,
      baseUrl: circleSdkBaseUrl(this.config.circle.baseUrl),
    });
  }
}

function circleSdkBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/v1\/w3s\/?$/, "");
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

function mockTransactionHash(...values: string[]): `0x${string}` {
  return `0x${createHash("sha256").update(values.join(":")).digest("hex")}`;
}

async function parseJsonPayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractFirstWallet(payload: unknown): CircleWalletResponseWallet | undefined {
  const [wallet] = extractWallets(payload);
  return wallet;
}

function extractWallets(payload: unknown): CircleWalletResponseWallet[] {
  if (!isRecord(payload)) {
    return [];
  }

  const data = payload.data;
  if (!isRecord(data) || !Array.isArray(data.wallets)) {
    return [];
  }

  return data.wallets.filter(isRecord);
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

function normalizeCircleWalletRecord(
  wallet: CircleWalletResponseWallet,
  payload: unknown,
): CircleWalletRecord {
  if (typeof wallet.id !== "string" || typeof wallet.address !== "string") {
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

  return {
    agentName:
      typeof wallet.name === "string" && wallet.name.trim()
        ? wallet.name.trim()
        : typeof wallet.refId === "string" && wallet.refId.trim()
          ? wallet.refId.trim()
          : wallet.id,
    circleWalletId: wallet.id,
    circleWalletSetId:
      typeof wallet.walletSetId === "string"
        ? wallet.walletSetId
        : (isRecord(payload) &&
            isRecord(payload.data) &&
            typeof payload.data.walletSetId === "string"
          ? payload.data.walletSetId
          : null) ?? null,
    blockchain: "BASE-SEPOLIA",
    walletAddress: normalizeCircleAddress(wallet.address, payload),
    mode: "circle",
  };
}
