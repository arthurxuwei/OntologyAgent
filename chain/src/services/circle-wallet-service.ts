import { createHash, randomBytes, randomUUID } from "node:crypto";
import {
  initiateDeveloperControlledWalletsClient,
  type CircleDeveloperControlledWalletsClient,
} from "@circle-fin/developer-controlled-wallets";
import { BatchFacilitatorClient } from "@circle-fin/x402-batching/server";
import { formatUnits, parseUnits } from "ethers";

import type { AppConfig } from "../config.js";
import { AppError } from "../domain/errors.js";
import type { CircleBlockchain } from "../domain/types.js";
import { normalizeAddress } from "../security.js";

export type CircleWalletCreateResult = {
  circleWalletId: string;
  circleWalletSetId: string;
  blockchain: CircleBlockchain;
  walletAddress: string;
  mode: "mock" | "circle";
  accountType?: "SCA" | "EOA";
};

export type CircleWalletRecord = Omit<CircleWalletCreateResult, "circleWalletSetId"> & {
  agentName: string;
  circleWalletSetId: string | null;
  accountType?: "SCA" | "EOA";
};

type CircleWalletResponseWallet = {
  id?: unknown;
  address?: unknown;
  blockchain?: unknown;
  walletSetId?: unknown;
  name?: unknown;
  refId?: unknown;
  accountType?: unknown;
};

type CircleWalletTokenBalance = {
  token?: unknown;
  amount?: unknown;
};

export type CircleGatewayBalance = {
  total: bigint;
  available: bigint;
  withdrawing: bigint;
  withdrawable: bigint;
  pendingDeposits: bigint;
  pendingBatch: bigint;
  formattedTotal: string;
  formattedAvailable: string;
  formattedWithdrawing: string;
  formattedWithdrawable: string;
  formattedPendingDeposits: string;
  formattedPendingBatch: string;
};

type GatewayPaymentPayload = {
  x402Version: number;
  resource?: {
    url: string;
    description: string;
    mimeType: string;
  };
  accepted?: Record<string, unknown>;
  payload: unknown;
};

type GatewayPaymentRequirements = {
  scheme: string;
  network: string;
  asset: string;
  amount: string;
  payTo: string;
  maxTimeoutSeconds: number;
  extra?: Record<string, unknown>;
};

type GatewayVerifyResponse = {
  isValid: boolean;
  invalidReason?: string;
  payer?: string;
};

type GatewaySettleResponse = {
  success: boolean;
  errorReason?: string;
  payer?: string;
  transaction?: string;
  network?: string;
};

export type CircleGatewaySettlementResult = {
  verify: GatewayVerifyResponse;
  settle: GatewaySettleResponse;
};

const MAX_UINT256 = (2n ** 256n - 1n).toString();
const GATEWAY_TRANSFER_AUTH_RETRY_DELAYS_MS = [5_000, 15_000, 30_000, 60_000] as const;

type GatewayBurnIntent = {
  maxBlockHeight: string;
  maxFee: string;
  spec: {
    version: number;
    sourceDomain: number;
    destinationDomain: number;
    sourceContract: string;
    destinationContract: string;
    sourceToken: string;
    destinationToken: string;
    sourceDepositor: string;
    destinationRecipient: string;
    sourceSigner: string;
    destinationCaller: string;
    value: string;
    salt: string;
    hookData: string;
  };
};

export type CircleEntitySecretCiphertextFactory = (
  entitySecret: string,
) => string | Promise<string>;

type CircleWalletClient =
  Pick<
    CircleDeveloperControlledWalletsClient,
    | "createContractExecutionTransaction"
    | "createTransaction"
    | "getTransaction"
    | "requestTestnetTokens"
  > &
    Partial<Pick<CircleDeveloperControlledWalletsClient, "signTypedData">>;

export type CircleWalletServiceOptions = {
  createEntitySecretCiphertext?: CircleEntitySecretCiphertextFactory;
  fetchImpl?: typeof fetch;
  sleepImpl?: (ms: number) => Promise<void>;
  client?: CircleWalletClient | {
    createContractExecutionTransaction?(input: unknown): Promise<any>;
    createTransaction(input: unknown): Promise<any>;
    getTransaction(input: unknown): Promise<any>;
    requestTestnetTokens(input: unknown): Promise<any>;
    signTypedData?(input: unknown): Promise<any>;
  };
};

export class CircleWalletService {
  private readonly createEntitySecretCiphertext?: CircleEntitySecretCiphertextFactory;
  private readonly fetchImpl: typeof fetch;
  private readonly sleepImpl: (ms: number) => Promise<void>;
  private readonly client?: CircleWalletClient | {
    createContractExecutionTransaction?(input: unknown): Promise<any>;
    createTransaction(input: unknown): Promise<any>;
    getTransaction(input: unknown): Promise<any>;
    requestTestnetTokens(input: unknown): Promise<any>;
    signTypedData?(input: unknown): Promise<any>;
  };

  constructor(
    private readonly config: AppConfig,
    options: CircleWalletServiceOptions = {},
  ) {
    this.createEntitySecretCiphertext = options.createEntitySecretCiphertext;
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.sleepImpl = options.sleepImpl ?? sleep;
    this.client = options.client;
  }

  async createWallet(
    agentName: string,
    accountType: "SCA" | "EOA" = "EOA",
  ): Promise<CircleWalletCreateResult> {
    if (this.config.network.mockChain) {
      return {
        circleWalletId: `mock-circle-wallet-${slug(agentName)}`,
        circleWalletSetId: "mock-circle-wallet-set",
        blockchain: this.config.circle.blockchain,
        walletAddress: mockAddress(agentName),
        mode: "mock",
        ...(accountType === "EOA" ? { accountType } : {}),
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
        accountType,
        blockchains: [this.config.circle.blockchain],
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

    if (wallet.blockchain !== undefined && wallet.blockchain !== this.config.circle.blockchain) {
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
      blockchain: this.config.circle.blockchain,
      walletAddress,
      mode: "circle",
      accountType:
        wallet.accountType === "SCA" || wallet.accountType === "EOA"
          ? wallet.accountType
          : accountType,
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

    return extractWallets(payload).map((wallet) =>
      normalizeCircleWalletRecord(wallet, payload, this.config.circle.blockchain),
    );
  }

  async getWalletBalances(circleWalletId: string): Promise<Record<string, string>> {
    if (this.config.network.mockChain) {
      return {
        USDC: this.config.network.mockUsdcBalanceAtomic.toString(),
      };
    }

    const apiKey = this.config.circle.apiKey;
    if (!apiKey) {
      throw new AppError("CONFIG_ERROR", "CIRCLE_API_KEY is required", 500);
    }

    const response = await this.fetchImpl(
      `${this.config.circle.baseUrl}/wallets/${encodeURIComponent(circleWalletId)}/balances`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
      },
    );
    const payload = await parseJsonPayload(response);

    if (!response.ok) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle wallet balance lookup failed with HTTP ${response.status}`,
        response.status,
        payload,
      );
    }

    return extractTokenBalances(payload);
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

  async signTypedData(command: {
    walletId: string;
    data: unknown;
    memo?: string;
  }): Promise<`0x${string}`> {
    if (this.config.network.mockChain) {
      return mockSignature(command.walletId, stringifyJsonSafe(command.data), command.memo ?? "");
    }

    const client = this.requireClient();
    if (typeof client.signTypedData !== "function") {
      throw new AppError(
        "CONFIG_ERROR",
        "Circle signTypedData client support is required for Gateway payments",
        500,
      );
    }

    const response = await client.signTypedData({
      walletId: command.walletId,
      data: typeof command.data === "string"
        ? command.data
        : stringifyJsonSafe(addEip712DomainType(command.data)),
      memo: command.memo,
    } as any);
    const signature = extractSignature(response);
    if (!signature) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle signTypedData response did not include a signature",
        502,
        response,
      );
    }
    return signature;
  }

  async getGatewayBalance(walletAddress: string, domain: number): Promise<CircleGatewayBalance> {
    const normalizedAddress = normalizeAddress(walletAddress);
    if (this.config.network.mockChain) {
      const available = this.config.network.mockUsdcBalanceAtomic;
      return formatGatewayBalance({
        available,
        withdrawing: 0n,
        withdrawable: available,
        pendingDeposits: 0n,
        pendingBatch: 0n,
      });
    }

    const response = await this.fetchImpl(`${gatewayApiBaseUrl(this.config.x402.facilitatorUrl, this.config.x402.network)}/balances`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: "USDC",
        sources: [{ depositor: normalizedAddress, domain }],
      }),
    });
    const payload = await parseJsonPayload(response);
    if (!response.ok) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle Gateway balance lookup failed with HTTP ${response.status}`,
        response.status,
        payload,
      );
    }

    const balance = extractGatewayBalance(payload);
    if (!balance) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle Gateway balance response did not include a balance",
        502,
        payload,
      );
    }
    return balance;
  }

  async settleGatewayPayment(command: {
    paymentPayload: GatewayPaymentPayload;
    paymentRequirements: GatewayPaymentRequirements;
  }): Promise<CircleGatewaySettlementResult> {
    if (this.config.network.mockChain) {
      const transaction = mockTransactionHash(
        command.paymentRequirements.payTo,
        command.paymentRequirements.amount,
        command.paymentRequirements.asset,
      );
      return {
        verify: { isValid: true },
        settle: {
          success: true,
          transaction,
          network: command.paymentRequirements.network,
        },
      };
    }

    const facilitator = new BatchFacilitatorClient({
      url: gatewayFacilitatorBaseUrl(this.config.x402.facilitatorUrl, this.config.x402.network),
    });
    const paymentPayload = withGatewayPaymentEnvelope(
      command.paymentPayload,
      command.paymentRequirements,
    );
    const verify = await facilitator.verify(
      paymentPayload as any,
      command.paymentRequirements as any,
    );
    if (!verify.isValid) {
      return {
        verify,
        settle: {
          success: false,
          errorReason: verify.invalidReason ?? "Gateway payment verification failed",
          network: command.paymentRequirements.network,
        },
      };
    }
    const settle = await facilitator.settle(
      paymentPayload as any,
      command.paymentRequirements as any,
    );
    return { verify, settle };
  }

  async depositToGateway(command: {
    walletId: string;
    walletAddress: string;
    tokenAddress: string;
    gatewayWallet: string;
    amountAtomic: string;
    refId?: string;
  }): Promise<{
    approval: unknown;
    approvalFinal: unknown;
    deposit: unknown;
    depositFinal: unknown;
  }> {
    if (this.config.network.mockChain) {
      const approvalTx = mockTransactionHash("approve", command.walletId, command.amountAtomic);
      const depositTx = mockTransactionHash("deposit", command.walletId, command.amountAtomic);
      return {
        approval: { transaction: { id: approvalTx, state: "COMPLETE" } },
        approvalFinal: { transaction: { id: approvalTx, state: "COMPLETE" } },
        deposit: { transaction: { id: depositTx, state: "COMPLETE" } },
        depositFinal: { transaction: { id: depositTx, state: "COMPLETE" } },
      };
    }

    const client = this.requireClient();
    if (typeof client.createContractExecutionTransaction !== "function") {
      throw new AppError(
        "CONFIG_ERROR",
        "Circle contract execution support is required for Gateway deposits",
        500,
      );
    }

    const approval = (
      await client.createContractExecutionTransaction({
        walletId: command.walletId,
        contractAddress: normalizeAddress(command.tokenAddress),
        abiFunctionSignature: "approve(address,uint256)",
        abiParameters: [normalizeAddress(command.gatewayWallet), command.amountAtomic],
        refId: appendRef(command.refId, "gateway-approve"),
        fee: {
          type: "level",
          config: {
            feeLevel: "MEDIUM",
          },
        },
      } as any)
    ).data;
    const approvalTransaction = extractCircleTransaction(approval);
    if (!approvalTransaction.id) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle approval response did not include a transaction id",
        502,
        approval,
      );
    }
    const approvalFinal = await this.waitForCircleTransaction(approvalTransaction.id);

    const deposit = (
      await client.createContractExecutionTransaction({
        walletId: command.walletId,
        contractAddress: normalizeAddress(command.gatewayWallet),
        abiFunctionSignature: "deposit(address,uint256)",
        abiParameters: [normalizeAddress(command.tokenAddress), command.amountAtomic],
        refId: appendRef(command.refId, "gateway-deposit"),
        fee: {
          type: "level",
          config: {
            feeLevel: "MEDIUM",
          },
        },
      } as any)
    ).data;
    const depositTransaction = extractCircleTransaction(deposit);
    if (!depositTransaction.id) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle Gateway deposit response did not include a transaction id",
        502,
        deposit,
      );
    }
    const depositFinal = await this.waitForCircleTransaction(depositTransaction.id);

    return {
      approval,
      approvalFinal,
      deposit,
      depositFinal,
    };
  }

  async addGatewayDelegate(command: {
    walletId: string;
    tokenAddress: string;
    gatewayWallet: string;
    delegateAddress: string;
    refId?: string;
  }): Promise<{
    transaction: unknown;
    transactionFinal: unknown;
  }> {
    if (this.config.network.mockChain) {
      const tx = mockTransactionHash(
        "gateway-add-delegate",
        command.walletId,
        command.delegateAddress,
      );
      return {
        transaction: { transaction: { id: tx, state: "COMPLETE" } },
        transactionFinal: { transaction: { id: tx, state: "COMPLETE" } },
      };
    }

    const client = this.requireClient();
    if (typeof client.createContractExecutionTransaction !== "function") {
      throw new AppError(
        "CONFIG_ERROR",
        "Circle contract execution support is required for Gateway delegate setup",
        500,
      );
    }

    const transaction = (
      await client.createContractExecutionTransaction({
        walletId: command.walletId,
        contractAddress: normalizeAddress(command.gatewayWallet),
        abiFunctionSignature: "addDelegate(address,address)",
        abiParameters: [
          normalizeAddress(command.tokenAddress),
          normalizeAddress(command.delegateAddress),
        ],
        refId: appendRef(command.refId, "gateway-add-delegate"),
        fee: {
          type: "level",
          config: {
            feeLevel: "MEDIUM",
          },
        },
      } as any)
    ).data;
    const circleTransaction = extractCircleTransaction(transaction);
    if (!circleTransaction.id) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle Gateway delegate response did not include a transaction id",
        502,
        transaction,
      );
    }
    const transactionFinal = await this.waitForCircleTransaction(circleTransaction.id);
    return { transaction, transactionFinal };
  }

  async withdrawFromGateway(command: {
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
  }): Promise<{
    burnIntent: GatewayBurnIntent;
    gatewayTransfer: unknown;
    gatewayTransferId: string | null;
    mint: unknown;
    mintFinal: unknown;
  }> {
    if (this.config.network.mockChain) {
      const mintTx = mockTransactionHash(
        "gateway-withdraw",
        command.walletId,
        command.recipientAddress,
        command.amountAtomic,
      );
      return {
        burnIntent: createGatewayBurnIntent(command),
        gatewayTransfer: {
          id: `mock-gateway-transfer-${Date.now().toString(16)}`,
          attestation: "0x00",
          signature: mockSignature("gateway-transfer", command.walletId),
        },
        gatewayTransferId: `mock-gateway-transfer-${Date.now().toString(16)}`,
        mint: { transaction: { id: mintTx, txHash: mintTx, state: "COMPLETE" } },
        mintFinal: { transaction: { id: mintTx, txHash: mintTx, state: "COMPLETE" } },
      };
    }

    const client = this.requireClient();
    if (typeof client.createContractExecutionTransaction !== "function") {
      throw new AppError(
        "CONFIG_ERROR",
        "Circle contract execution support is required for Gateway withdrawals",
        500,
      );
    }

    const burnIntent = createGatewayBurnIntent(command);
    const signature = await this.signTypedData({
      walletId: command.signerWalletId ?? command.walletId,
      data: gatewayBurnIntentTypedData(burnIntent),
      memo: appendRef(command.refId, "gateway-withdraw-sign"),
    });
    const gatewayTransferBody = stringifyJsonSafe([{ burnIntent, signature }]);
    let gatewayTransfer: unknown;
    let gatewayTransferRecord: Record<string, unknown> | null = null;
    let gatewayStatus = 0;
    for (let attempt = 0; ; attempt += 1) {
      const gatewayResponse = await this.fetchImpl(
        `${gatewayApiBaseUrl(this.config.x402.facilitatorUrl, this.config.x402.network)}/transfer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: gatewayTransferBody,
        },
      );
      gatewayStatus = gatewayResponse.status;
      gatewayTransfer = await parseJsonPayload(gatewayResponse);
      if (
        gatewayResponse.ok &&
        isRecord(gatewayTransfer) &&
        gatewayTransfer.success !== false &&
        !gatewayTransfer.error
      ) {
        gatewayTransferRecord = gatewayTransfer;
        break;
      }
      const retryDelay = GATEWAY_TRANSFER_AUTH_RETRY_DELAYS_MS[attempt];
      if (!isGatewaySignerAuthorizationLag(gatewayTransfer) || retryDelay === undefined) {
        if (!gatewayResponse.ok) {
          throw new AppError(
            "UPSTREAM_REQUEST_FAILED",
            `Circle Gateway transfer request failed with HTTP ${gatewayStatus}`,
            gatewayStatus,
            gatewayTransfer,
          );
        }
        throw new AppError(
          "UPSTREAM_REQUEST_FAILED",
          `Circle Gateway transfer request failed: ${gatewayTransferErrorMessage(gatewayTransfer)}`,
          424,
          gatewayTransfer,
        );
      }
      await this.sleepImpl(retryDelay);
    }
    if (!gatewayTransferRecord) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle Gateway transfer response was not accepted",
        502,
        gatewayTransfer,
      );
    }
    const attestation = typeof gatewayTransferRecord.attestation === "string"
      ? gatewayTransferRecord.attestation
      : null;
    const attestationSignature = typeof gatewayTransferRecord.signature === "string"
      ? gatewayTransferRecord.signature
      : null;
    if (!attestation || !attestationSignature) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle Gateway transfer response did not include attestation and signature",
        502,
        gatewayTransfer,
      );
    }

    const mint = (
      await client.createContractExecutionTransaction({
        walletId: command.walletId,
        contractAddress: normalizeAddress(command.gatewayMinter),
        abiFunctionSignature: "gatewayMint(bytes,bytes)",
        abiParameters: [attestation, attestationSignature],
        refId: appendRef(command.refId, "gateway-mint"),
        fee: {
          type: "level",
          config: {
            feeLevel: "MEDIUM",
          },
        },
      } as any)
    ).data;
    const mintTransaction = extractCircleTransaction(mint);
    if (!mintTransaction.id) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        "Circle Gateway mint response did not include a transaction id",
        502,
        mint,
      );
    }
    const mintFinal = await this.waitForCircleTransaction(mintTransaction.id);

    return {
      burnIntent,
      gatewayTransfer: gatewayTransferRecord,
      gatewayTransferId: typeof gatewayTransferRecord.id === "string" ? gatewayTransferRecord.id : null,
      mint,
      mintFinal,
    };
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
    if (this.config.circle.blockchain !== "BASE-SEPOLIA") {
      throw new AppError(
        "NETWORK_MISMATCH",
        "Circle testnet faucet is only available for BASE-SEPOLIA",
        400,
        { blockchain: this.config.circle.blockchain },
      );
    }
    const client = this.requireClient();
    await client.requestTestnetTokens({
      address: normalizeAddress(command.walletAddress),
      blockchain: "BASE-SEPOLIA",
      native: command.native,
      usdc: command.usdc,
    });
  }

  private requireClient(): CircleWalletClient | {
    createContractExecutionTransaction?(input: unknown): Promise<any>;
    createTransaction(input: unknown): Promise<any>;
    getTransaction(input: unknown): Promise<any>;
    requestTestnetTokens(input: unknown): Promise<any>;
    signTypedData?(input: unknown): Promise<any>;
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

  private async waitForCircleTransaction(transactionId: string): Promise<unknown> {
    const deadline = Date.now() + 120_000;
    let lastPayload: unknown = null;

    while (Date.now() < deadline) {
      lastPayload = await this.getTransaction(transactionId);
      const transaction = extractCircleTransaction(lastPayload);
      const state = transaction.state?.toUpperCase() ?? null;
      if (state && ["COMPLETE", "CONFIRMED", "SUCCESS", "MINED"].includes(state)) {
        return lastPayload;
      }
      if (state && ["FAILED", "CANCELLED", "CANCELED", "DENIED"].includes(state)) {
        throw new AppError(
          "UPSTREAM_REQUEST_FAILED",
          `Circle transaction ${transactionId} failed with state ${state}`,
          424,
          lastPayload,
        );
      }
      await sleep(5_000);
    }

    throw new AppError(
      "UPSTREAM_REQUEST_FAILED",
      `Timed out waiting for Circle transaction ${transactionId} to complete`,
      504,
      lastPayload,
    );
  }
}

function circleSdkBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/v1\/w3s\/?$/, "");
}

function gatewayFacilitatorBaseUrl(configuredUrl: string, network = "eip155:84532"): string {
  return gatewayRootBaseUrl(configuredUrl, network);
}

function gatewayApiBaseUrl(configuredUrl: string, network = "eip155:84532"): string {
  return `${gatewayRootBaseUrl(configuredUrl, network)}/v1`;
}

function gatewayRootBaseUrl(configuredUrl: string, network = "eip155:84532"): string {
  const trimmed = configuredUrl.trim().replace(/\/+$/, "");
  if (!trimmed || trimmed.includes("x402.org")) {
    return network === "eip155:8453"
      ? "https://gateway-api.circle.com"
      : "https://gateway-api-testnet.circle.com";
  }
  return trimmed.replace(/\/v1(?:\/x402(?:\/[^/]+)?)?$/, "");
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

function mockSignature(...values: string[]): `0x${string}` {
  const digest = createHash("sha512").update(values.join(":")).digest("hex");
  return `0x${digest}${digest.slice(0, 2)}`;
}

function appendRef(refId: string | undefined, suffix: string): string {
  return refId ? `${refId}:${suffix}` : suffix;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function stringifyJsonSafe(value: unknown): string {
  return JSON.stringify(value, (_, nestedValue) =>
    typeof nestedValue === "bigint" ? nestedValue.toString() : nestedValue,
  );
}

function createGatewayBurnIntent(command: {
  walletAddress: string;
  signerAddress?: string;
  recipientAddress: string;
  tokenAddress: string;
  gatewayWallet: string;
  gatewayMinter: string;
  sourceDomain: number;
  destinationDomain: number;
  amountAtomic: string;
}): GatewayBurnIntent {
  return {
    maxBlockHeight: MAX_UINT256,
    maxFee: "2010000",
    spec: {
      version: 1,
      sourceDomain: command.sourceDomain,
      destinationDomain: command.destinationDomain,
      sourceContract: addressToBytes32(command.gatewayWallet),
      destinationContract: addressToBytes32(command.gatewayMinter),
      sourceToken: addressToBytes32(command.tokenAddress),
      destinationToken: addressToBytes32(command.tokenAddress),
      sourceDepositor: addressToBytes32(command.walletAddress),
      destinationRecipient: addressToBytes32(command.recipientAddress),
      sourceSigner: addressToBytes32(command.signerAddress ?? command.walletAddress),
      destinationCaller: addressToBytes32("0x0000000000000000000000000000000000000000"),
      value: command.amountAtomic,
      salt: `0x${randomBytes(32).toString("hex")}`,
      hookData: "0x",
    },
  };
}

function gatewayBurnIntentTypedData(burnIntent: GatewayBurnIntent): unknown {
  return {
    domain: { name: "GatewayWallet", version: "1" },
    types: {
      EIP712Domain: [
        { name: "name", type: "string" },
        { name: "version", type: "string" },
      ],
      TransferSpec: [
        { name: "version", type: "uint32" },
        { name: "sourceDomain", type: "uint32" },
        { name: "destinationDomain", type: "uint32" },
        { name: "sourceContract", type: "bytes32" },
        { name: "destinationContract", type: "bytes32" },
        { name: "sourceToken", type: "bytes32" },
        { name: "destinationToken", type: "bytes32" },
        { name: "sourceDepositor", type: "bytes32" },
        { name: "destinationRecipient", type: "bytes32" },
        { name: "sourceSigner", type: "bytes32" },
        { name: "destinationCaller", type: "bytes32" },
        { name: "value", type: "uint256" },
        { name: "salt", type: "bytes32" },
        { name: "hookData", type: "bytes" },
      ],
      BurnIntent: [
        { name: "maxBlockHeight", type: "uint256" },
        { name: "maxFee", type: "uint256" },
        { name: "spec", type: "TransferSpec" },
      ],
    },
    primaryType: "BurnIntent",
    message: burnIntent,
  };
}

function addressToBytes32(address: string): string {
  const normalized = normalizeAddress(address).toLowerCase();
  return `0x${"0".repeat(24)}${normalized.slice(2)}`;
}

function gatewayTransferErrorMessage(payload: unknown): string {
  if (!isRecord(payload)) {
    return "unknown error";
  }
  if (typeof payload.message === "string") {
    return payload.message;
  }
  if (typeof payload.error === "string") {
    return payload.error;
  }
  return stringifyJsonSafe(payload);
}

function isGatewaySignerAuthorizationLag(payload: unknown): boolean {
  return gatewayTransferErrorMessage(payload)
    .toLowerCase()
    .includes("signer is not authorized to spend funds from sourcedepositor");
}

function addEip712DomainType(value: unknown): unknown {
  if (!isRecord(value) || !isRecord(value.domain) || !isRecord(value.types)) {
    return value;
  }
  if (Array.isArray(value.types.EIP712Domain)) {
    return value;
  }

  const eip712Domain = eip712DomainFields(value.domain);
  if (eip712Domain.length === 0) {
    return value;
  }
  return {
    ...value,
    types: {
      ...value.types,
      EIP712Domain: eip712Domain,
    },
  };
}

function eip712DomainFields(domain: Record<string, unknown>): Array<{ name: string; type: string }> {
  const fields: Array<{ name: string; type: string }> = [];
  if (domain.name !== undefined) {
    fields.push({ name: "name", type: "string" });
  }
  if (domain.version !== undefined) {
    fields.push({ name: "version", type: "string" });
  }
  if (domain.chainId !== undefined) {
    fields.push({ name: "chainId", type: "uint256" });
  }
  if (domain.verifyingContract !== undefined) {
    fields.push({ name: "verifyingContract", type: "address" });
  }
  if (domain.salt !== undefined) {
    fields.push({ name: "salt", type: "bytes32" });
  }
  return fields;
}

function withGatewayPaymentEnvelope(
  paymentPayload: GatewayPaymentPayload,
  paymentRequirements: GatewayPaymentRequirements,
): GatewayPaymentPayload {
  return {
    ...paymentPayload,
    resource: paymentPayload.resource ?? {
      url: "agent-wallet://gateway-transfer",
      description: "Agent Wallet Gateway transfer",
      mimeType: "application/json",
    },
    accepted: paymentPayload.accepted ?? paymentRequirements,
  };
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

function extractTokenBalances(payload: unknown): Record<string, string> {
  if (!isRecord(payload) || !isRecord(payload.data)) {
    return {};
  }
  const tokenBalances = payload.data.tokenBalances;
  if (!Array.isArray(tokenBalances)) {
    return {};
  }
  const balances: Record<string, string> = {};
  for (const balance of tokenBalances.filter(isTokenBalance)) {
    if (!isRecord(balance.token)) {
      continue;
    }
    const symbol = balance.token.symbol;
    if (typeof symbol === "string" && symbol.trim() && typeof balance.amount === "string") {
      balances[symbol.trim()] = balance.amount;
    }
  }
  return balances;
}

function extractGatewayBalance(payload: unknown): CircleGatewayBalance | null {
  if (!isRecord(payload) || !Array.isArray(payload.balances) || payload.balances.length === 0) {
    return null;
  }
  const [first] = payload.balances.filter(isRecord);
  if (!first || typeof first.balance !== "string") {
    return null;
  }
  const available = parseGatewayAmount(first.balance, payload);
  const withdrawing = parseGatewayAmount(
    typeof first.withdrawing === "string" ? first.withdrawing : "0",
    payload,
  );
  const withdrawable = parseGatewayAmount(
    typeof first.withdrawable === "string" ? first.withdrawable : "0",
    payload,
  );
  const pendingDeposits = extractPendingGatewayDeposits(first, payload);
  const pendingBatch = parseGatewayAmount(
    typeof first.pendingBatch === "string" ? first.pendingBatch : "0",
    payload,
  );
  return formatGatewayBalance({ available, withdrawing, withdrawable, pendingDeposits, pendingBatch });
}

function extractPendingGatewayDeposits(balance: Record<string, unknown>, payload: unknown): bigint {
  for (const key of ["pendingDeposits", "pendingDeposit", "pending", "pendingBalance"]) {
    const value = balance[key];
    if (typeof value === "string") {
      return parseGatewayAmount(value, payload);
    }
  }

  const pendingDeposits = balance.pendingDeposits;
  if (Array.isArray(pendingDeposits)) {
    return pendingDeposits.filter(isRecord).reduce((sum, item) => {
      const value = typeof item.amount === "string"
        ? item.amount
        : typeof item.balance === "string"
          ? item.balance
          : "0";
      return sum + parseGatewayAmount(value, payload);
    }, 0n);
  }

  if (isRecord(pendingDeposits)) {
    const value = typeof pendingDeposits.amount === "string"
      ? pendingDeposits.amount
      : typeof pendingDeposits.balance === "string"
        ? pendingDeposits.balance
        : "0";
    return parseGatewayAmount(value, payload);
  }

  return 0n;
}

function parseGatewayAmount(value: string, payload: unknown): bigint {
  try {
    return parseUnits(value, 6);
  } catch (error) {
    throw new AppError(
      "UPSTREAM_REQUEST_FAILED",
      "Circle Gateway balance response included an invalid amount",
      502,
      {
        payload,
        cause: error instanceof Error ? error.message : String(error),
      },
    );
  }
}

function formatGatewayBalance(parts: {
  available: bigint;
  withdrawing: bigint;
  withdrawable: bigint;
  pendingDeposits: bigint;
  pendingBatch: bigint;
}): CircleGatewayBalance {
  const total = parts.available + parts.withdrawing;
  return {
    total,
    available: parts.available,
    withdrawing: parts.withdrawing,
    withdrawable: parts.withdrawable,
    pendingDeposits: parts.pendingDeposits,
    pendingBatch: parts.pendingBatch,
    formattedTotal: formatUnits(total, 6),
    formattedAvailable: formatUnits(parts.available, 6),
    formattedWithdrawing: formatUnits(parts.withdrawing, 6),
    formattedWithdrawable: formatUnits(parts.withdrawable, 6),
    formattedPendingDeposits: formatUnits(parts.pendingDeposits, 6),
    formattedPendingBatch: formatUnits(parts.pendingBatch, 6),
  };
}

function extractSignature(payload: unknown): `0x${string}` | null {
  const data = isRecord(payload) && isRecord(payload.data) ? payload.data : payload;
  const signature = isRecord(data) ? data.signature : null;
  return typeof signature === "string" && signature.startsWith("0x")
    ? (signature as `0x${string}`)
    : null;
}

function extractCircleTransaction(payload: unknown): {
  id: string | null;
  state: string | null;
} {
  const container = isRecord(payload) && isRecord(payload.transaction)
    ? payload.transaction
    : isRecord(payload) && isRecord(payload.data) && isRecord(payload.data.transaction)
      ? payload.data.transaction
      : isRecord(payload) && typeof payload.id === "string"
        ? payload
        : isRecord(payload) && isRecord(payload.data) && typeof payload.data.id === "string"
          ? payload.data
          : null;
  return {
    id: typeof container?.id === "string" ? container.id : null,
    state: typeof container?.state === "string" ? container.state : null,
  };
}

function isTokenBalance(value: unknown): value is CircleWalletTokenBalance {
  return isRecord(value);
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
  expectedBlockchain: CircleBlockchain,
): CircleWalletRecord {
  if (typeof wallet.id !== "string" || typeof wallet.address !== "string") {
    throw new AppError(
      "UPSTREAM_REQUEST_FAILED",
      "Circle wallet response did not include id and address",
      502,
      payload,
    );
  }

  if (wallet.blockchain !== undefined && wallet.blockchain !== expectedBlockchain) {
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
    blockchain: expectedBlockchain,
    walletAddress: normalizeCircleAddress(wallet.address, payload),
    mode: "circle",
    ...(wallet.accountType === "SCA" || wallet.accountType === "EOA"
      ? { accountType: wallet.accountType }
      : {}),
  };
}
