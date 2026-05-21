import type { AppConfig } from "../config.js";
import {
  CIRCLE_BATCHING_NAME,
  CIRCLE_BATCHING_VERSION,
  type BatchEvmSigner,
} from "@circle-fin/x402-batching";
import { BatchEvmScheme, CHAIN_CONFIGS } from "@circle-fin/x402-batching/client";
import { AppError } from "../domain/errors.js";
import type {
  AgentWalletBinding,
  AgentWalletCallX402ServiceCommand,
  AgentWalletCallX402ServiceResult,
  AgentWalletFaucetCommand,
  AgentWalletFaucetResult,
  AgentWalletGatewayDepositCommand,
  AgentWalletGatewayDepositResult,
  AgentWalletGatewayWithdrawCommand,
  AgentWalletGatewayWithdrawResult,
  AgentWalletGetOrCreateCommand,
  AgentWalletGetOrCreateResult,
  AgentWalletInitCommand,
  AgentWalletInitResult,
  AgentWalletRegisterX402ServiceCommand,
  AgentWalletRegisterX402ServiceResult,
  AgentWalletStatusCommand,
  AgentWalletStatusResult,
  AgentWalletTransactionStatusCommand,
  AgentWalletTransactionStatusResult,
  AgentWalletTransferCommand,
  AgentWalletTransferResult,
} from "../domain/types.js";
import { normalizeAddress } from "../security.js";
import { AgentWalletStateStore } from "./agent-wallet-state-store.js";
import { CircleWalletService } from "./circle-wallet-service.js";
import type { X402FetchService } from "./x402-fetch-service.js";

const MOCK_CIRCLE_WALLET_SET_ID = "mock-circle-wallet-set";
const MOCK_WALLET_ADDRESS = "0x3333333333333333333333333333333333333333";
const USDC_DECIMALS = 6n;
const GATEWAY_AUTH_VALIDITY_SECONDS = 7 * 24 * 60 * 60 + 10 * 60;

export class AgentWalletService {
  constructor(
    private readonly config: AppConfig,
    private readonly x402FetchService?: X402FetchService,
    private readonly circleWalletService = new CircleWalletService(config),
    private readonly stateStore = new AgentWalletStateStore(config.agentWallet.statePath),
  ) {}

  async init(command: AgentWalletInitCommand): Promise<AgentWalletInitResult> {
    const agentName = command.agentName.trim();
    if (agentName.length === 0) {
      throw new AppError("VALIDATION_ERROR", "agentName is required", 400);
    }

    const created = await this.circleWalletService.createWallet(agentName);
    const binding = await this.stateStore.saveBinding({
      agentName,
      agentId: command.agentId,
      email: command.email,
      circleWalletId: created.circleWalletId,
      circleWalletSetId: created.circleWalletSetId,
      blockchain: created.blockchain,
      walletAddress: created.walletAddress,
      mode: created.mode,
      accountType: created.accountType,
    });
    return {
      ...created,
      ...(binding ? { binding } : {}),
    };
  }

  async getOrCreate(
    command: AgentWalletGetOrCreateCommand,
  ): Promise<AgentWalletGetOrCreateResult> {
    const agentName = normalizeAgentName(command.agentName);
    if (hasNonEmptyValue(command.walletAddress) || hasNonEmptyValue(command.circleWalletId)) {
      const status = await this.status({
        walletAddress: command.walletAddress,
        circleWalletId: command.circleWalletId,
      });
      const binding = await this.stateStore.saveBinding({
        agentName,
        agentId: command.agentId,
        email: command.email,
        circleWalletId: status.circleWalletId,
        circleWalletSetId: status.circleWalletSetId,
        blockchain: status.blockchain,
        walletAddress: status.walletAddress,
        mode: status.mode,
        accountType: status.accountType,
      });
      return {
        ...(await this.withLiveBalances(status)),
        reused: true,
        ...(binding ? { binding } : {}),
      };
    }

    if (hasNonEmptyValue(command.agentId)) {
      const existingBinding = await this.stateStore.findBindingByAgentId(command.agentId);
      if (existingBinding && isUsableBinding(existingBinding)) {
        const binding = await this.stateStore.saveBinding({
          agentName,
          agentId: command.agentId,
          email: command.email ?? existingBinding.email ?? undefined,
          circleWalletId: existingBinding.circleWalletId,
          circleWalletSetId: existingBinding.circleWalletSetId,
          blockchain: existingBinding.blockchain,
          walletAddress: existingBinding.walletAddress,
          mode: existingBinding.mode,
          accountType: existingBinding.accountType,
          gatewayDelegateWalletId: existingBinding.gatewayDelegateWalletId,
          gatewayDelegateAddress: existingBinding.gatewayDelegateAddress,
        });
        return {
          ...(await this.withLiveBalances(statusFromBinding(existingBinding))),
          reused: true,
          ...(binding ? { binding } : {}),
        };
      }
    }

    const localWallet = await this.stateStore.findByAgentName(agentName);
    if (localWallet) {
      const binding = await this.stateStore.saveBinding({
        agentName,
        agentId: command.agentId,
        email: command.email,
        circleWalletId: localWallet.circleWalletId,
        circleWalletSetId: localWallet.circleWalletSetId,
        blockchain: localWallet.blockchain,
        walletAddress: localWallet.walletAddress,
        mode: localWallet.mode,
        accountType: localWallet.accountType,
      });
      return {
        ...(await this.withLiveBalances(localWallet)),
        reused: true,
        ...(binding ? { binding } : {}),
      };
    }

    const importedWallet = await this.importUnusedCircleWallet();
    if (importedWallet) {
      const binding = await this.stateStore.saveBinding({
        agentName,
        agentId: command.agentId,
        email: command.email,
        circleWalletId: importedWallet.circleWalletId,
        circleWalletSetId: importedWallet.circleWalletSetId,
        blockchain: importedWallet.blockchain,
        walletAddress: importedWallet.walletAddress,
        mode: importedWallet.mode,
        accountType: importedWallet.accountType,
      });
      return {
        ...importedWallet,
        reused: true,
        ...(binding ? { binding } : {}),
      };
    }

    const created = await this.init({
      agentName,
      agentDescription: command.agentDescription,
      agentId: command.agentId,
      email: command.email,
    });
    const binding =
      created.binding ??
      (await this.stateStore.saveBinding({
        agentName,
        agentId: command.agentId,
        email: command.email,
        circleWalletId: created.circleWalletId,
        circleWalletSetId: created.circleWalletSetId,
        blockchain: created.blockchain,
        walletAddress: created.walletAddress,
        mode: created.mode,
        accountType: created.accountType,
      }));
    return {
      ...created,
      reused: false,
      ...(binding ? { binding } : {}),
    };
  }

  async listCircleWallets() {
    return this.circleWalletService.listWallets();
  }

  async saveLocalWallets(wallets: Awaited<ReturnType<CircleWalletService["listWallets"]>>) {
    return this.stateStore.saveWallets(wallets);
  }

  private async importUnusedCircleWallet(): Promise<AgentWalletStatusResult | null> {
    if (this.config.network.mockChain || !this.config.agentWallet.statePath) {
      return null;
    }

    const wallets = await this.circleWalletService.listWallets();
    await this.stateStore.saveWallets(wallets);
    return this.stateStore.findUnboundWallet();
  }

  async status(command: AgentWalletStatusCommand): Promise<AgentWalletStatusResult> {
    if (!hasNonEmptyValue(command.walletAddress) && !hasNonEmptyValue(command.circleWalletId)) {
      throw new AppError(
        "VALIDATION_ERROR",
        "walletAddress or circleWalletId is required",
        400,
      );
    }

    const localWallet = await this.stateStore.findByWallet({
      walletAddress: command.walletAddress,
      circleWalletId: command.circleWalletId,
    });
    if (localWallet) {
      return this.withLiveBalances(localWallet);
    }
    const boundWallet = await this.stateStore.findBindingByWallet({
      walletAddress: command.walletAddress,
      circleWalletId: command.circleWalletId,
    });
    if (boundWallet && isUsableBinding(boundWallet)) {
      return this.withLiveBalances(statusFromBinding(boundWallet));
    }

    return {
      circleWalletId: hasNonEmptyValue(command.circleWalletId)
        ? command.circleWalletId.trim()
        : null,
      circleWalletSetId: MOCK_CIRCLE_WALLET_SET_ID,
      blockchain: "BASE-SEPOLIA",
      walletAddress: hasNonEmptyValue(command.walletAddress)
        ? normalizeRequestAddress(command.walletAddress, "walletAddress")
        : MOCK_WALLET_ADDRESS,
      status: "available",
      balances: {
        USDC: this.config.network.mockUsdcBalanceAtomic.toString(),
      },
      mode: "mock",
    };
  }

  async registerX402Service(
    command: AgentWalletRegisterX402ServiceCommand,
  ): Promise<AgentWalletRegisterX402ServiceResult> {
    const name = command.name.trim();
    if (name.length === 0) {
      throw new AppError("VALIDATION_ERROR", "name is required", 400);
    }

    if (!command.path.startsWith("/")) {
      throw new AppError("VALIDATION_ERROR", "path must start with /", 400);
    }

    const priceAtomic = parsePositiveBigInt(command.priceAtomic, "priceAtomic");

    return {
      name,
      path: command.path,
      priceAtomic: priceAtomic.toString(),
      assetAddress: normalizeRequestAddress(
        this.config.x402.usdcAssetAddress,
        "X402_USDC_ASSET_ADDRESS",
      ),
      network: this.config.x402.network,
      payTo: normalizeRequestAddress(command.payTo, "payTo"),
      active: true,
    };
  }

  async callX402Service(
    command: AgentWalletCallX402ServiceCommand,
  ): Promise<AgentWalletCallX402ServiceResult> {
    if (!this.x402FetchService) {
      throw new AppError(
        "VALIDATION_ERROR",
        "x402 service calls are available through chain_x402_fetch, not circle wallet tools",
        400,
      );
    }
    const result = await this.x402FetchService.execute(command);
    return {
      ...result,
      agentWalletTool: "agent_wallet_call_x402_service",
    };
  }

  async transfer(command: AgentWalletTransferCommand): Promise<AgentWalletTransferResult> {
    const source = await this.resolveBinding({
      agentId: command.fromAgentId,
      agentName: command.fromAgentName,
    });
    const destination = await this.resolveBinding({
      agentId: command.toAgentId,
      agentName: command.toAgentName,
    });
    const fromCircleWalletId = firstNonEmpty(command.fromCircleWalletId, source?.circleWalletId);
    if (!fromCircleWalletId) {
      throw new AppError(
        "VALIDATION_ERROR",
        "fromAgentId/fromAgentName must resolve to a real Circle wallet id, or fromCircleWalletId must be provided",
        400,
      );
    }

    const toAddress = firstNonEmpty(command.toAddress, destination?.walletAddress);
    if (!toAddress) {
      throw new AppError(
        "VALIDATION_ERROR",
        "toAgentId/toAgentName must resolve to a wallet address, or toAddress must be provided",
        400,
      );
    }

    const asset = command.asset ?? (command.amountAtomic ? "USDC" : "ETH");
    const amount =
      asset === "USDC"
        ? atomicUsdcToDecimal(command.amountAtomic)
        : normalizePositiveDecimal(command.amountEth, "amountEth");
    if (asset === "USDC") {
      if (!source?.walletAddress) {
        throw new AppError(
          "VALIDATION_ERROR",
          "fromAgentId/fromAgentName must resolve to a wallet address for Gateway payments",
          400,
        );
      }
      return this.gatewayUsdcTransfer({
        command,
        source,
        destination,
        fromCircleWalletId,
        fromAddress: source.walletAddress,
        toAddress,
        amount,
        amountAtomic: parsePositiveBigInt(command.amountAtomic ?? "", "amountAtomic"),
      });
    }

    const tokenAddress = "";
    const tokenId = null;
    const raw = await this.circleWalletService.createTransfer({
      walletId: fromCircleWalletId,
      destinationAddress: toAddress,
      amount,
      tokenId: tokenId ?? undefined,
      tokenAddress,
      refId: command.refId,
    });
    const transaction = extractTransaction(raw);
    return {
      fromAgentId: source?.agentId ?? command.fromAgentId ?? null,
      fromAgentName: source?.agentName ?? command.fromAgentName ?? null,
      fromCircleWalletId,
      fromAddress: source?.walletAddress ?? "",
      toAgentId: destination?.agentId ?? command.toAgentId ?? null,
      toAgentName: destination?.agentName ?? command.toAgentName ?? null,
      toAddress: normalizeRequestAddress(toAddress, "toAddress"),
      asset,
      amount,
      amountEth: amount,
      amountAtomic: null,
      tokenId,
      tokenAddress,
      blockchain: "BASE-SEPOLIA",
      transactionId: transaction.id,
      transactionHash: transaction.txHash,
      state: transaction.state,
      mode: "circle",
      raw,
    };
  }

  async withdrawUsdc(command: AgentWalletTransferCommand): Promise<AgentWalletTransferResult> {
    const source = await this.resolveBinding({
      agentId: command.fromAgentId,
      agentName: command.fromAgentName,
    });
    const fromCircleWalletId = firstNonEmpty(command.fromCircleWalletId, source?.circleWalletId);
    if (!fromCircleWalletId) {
      throw new AppError(
        "VALIDATION_ERROR",
        "fromAgentId/fromAgentName must resolve to a real Circle wallet id, or fromCircleWalletId must be provided",
        400,
      );
    }

    const toAddress = firstNonEmpty(command.toAddress);
    if (!toAddress) {
      throw new AppError("VALIDATION_ERROR", "toAddress is required", 400);
    }

    const amount = atomicUsdcToDecimal(command.amountAtomic);
    const amountAtomic = parsePositiveBigInt(command.amountAtomic ?? "", "amountAtomic");
    const tokenAddress = normalizeRequestAddress(
      this.config.x402.usdcAssetAddress,
      "X402_USDC_ASSET_ADDRESS",
    );
    const tokenId = this.config.circle.usdcTokenId ?? null;
    const raw = await this.circleWalletService.createTransfer({
      walletId: fromCircleWalletId,
      destinationAddress: toAddress,
      amount,
      tokenId: tokenId ?? undefined,
      tokenAddress,
      refId: command.refId,
    });
    const transaction = extractTransaction(raw);
    return {
      fromAgentId: source?.agentId ?? command.fromAgentId ?? null,
      fromAgentName: source?.agentName ?? command.fromAgentName ?? null,
      fromCircleWalletId,
      fromAddress: source?.walletAddress ?? "",
      toAgentId: null,
      toAgentName: null,
      toAddress: normalizeRequestAddress(toAddress, "toAddress"),
      asset: "USDC",
      amount,
      amountEth: null,
      amountAtomic: amountAtomic.toString(),
      tokenId,
      tokenAddress,
      blockchain: "BASE-SEPOLIA",
      transactionId: transaction.id,
      transactionHash: transaction.txHash,
      state: transaction.state,
      mode: "circle",
      raw,
    };
  }

  async depositToGateway(
    command: AgentWalletGatewayDepositCommand,
  ): Promise<AgentWalletGatewayDepositResult> {
    const binding = await this.resolveBinding({
      agentId: command.agentId,
      agentName: command.agentName,
    });
    const circleWalletId = firstNonEmpty(command.circleWalletId, binding?.circleWalletId);
    if (!circleWalletId) {
      throw new AppError(
        "VALIDATION_ERROR",
        "agentId/agentName must resolve to a real Circle wallet id, or circleWalletId must be provided",
        400,
      );
    }

    const walletAddress = firstNonEmpty(command.walletAddress, binding?.walletAddress);
    if (!walletAddress) {
      throw new AppError(
        "VALIDATION_ERROR",
        "agentId/agentName must resolve to a wallet address, or walletAddress must be provided",
        400,
      );
    }

    const amountAtomic = parsePositiveBigInt(command.amountAtomic, "amountAtomic");
    const amount = atomicUsdcToDecimal(command.amountAtomic);
    const tokenAddress = normalizeRequestAddress(
      this.config.x402.usdcAssetAddress,
      "X402_USDC_ASSET_ADDRESS",
    );
    const chainConfig = gatewayChainConfig(this.config.x402.network);
    const gatewayWallet = normalizeRequestAddress(
      chainConfig.gatewayWallet,
      "Gateway verifying contract",
    );
    const normalizedWalletAddress = normalizeRequestAddress(walletAddress, "walletAddress");

    const raw = await this.circleWalletService.depositToGateway({
      walletId: circleWalletId,
      walletAddress: normalizedWalletAddress,
      tokenAddress,
      gatewayWallet,
      amountAtomic: amountAtomic.toString(),
      refId: command.refId,
    });
    const gatewayBalance = await this.circleWalletService.getGatewayBalance(
      normalizedWalletAddress,
      chainConfig.domain,
    );
    const approvalTransaction = extractTransaction(raw.approvalFinal ?? raw.approval);
    const depositTransaction = extractTransaction(raw.depositFinal ?? raw.deposit);

    return {
      agentId: binding?.agentId ?? command.agentId ?? null,
      agentName: binding?.agentName ?? command.agentName ?? null,
      circleWalletId,
      walletAddress: normalizedWalletAddress,
      asset: "USDC",
      amount,
      amountAtomic: amountAtomic.toString(),
      tokenAddress,
      gatewayWallet,
      blockchain: "BASE-SEPOLIA",
      approvalTransactionId: approvalTransaction.id,
      approvalState: approvalTransaction.state,
      depositTransactionId: depositTransaction.id,
      depositState: depositTransaction.state,
      gatewayBalance: {
        availableAtomic: gatewayBalance.available.toString(),
        totalAtomic: gatewayBalance.total.toString(),
        formattedAvailable: gatewayBalance.formattedAvailable,
        formattedTotal: gatewayBalance.formattedTotal,
      },
      mode: "gateway_deposit",
      raw,
    };
  }

  async withdrawFromGateway(
    command: AgentWalletGatewayWithdrawCommand,
  ): Promise<AgentWalletGatewayWithdrawResult> {
    const binding = await this.resolveBinding({
      agentId: command.agentId,
      agentName: command.agentName,
    });
    const circleWalletId = firstNonEmpty(command.circleWalletId, binding?.circleWalletId);
    if (!circleWalletId) {
      throw new AppError(
        "VALIDATION_ERROR",
        "agentId/agentName must resolve to a real Circle wallet id, or circleWalletId must be provided",
        400,
      );
    }

    const walletAddress = firstNonEmpty(command.walletAddress, binding?.walletAddress);
    if (!walletAddress) {
      throw new AppError(
        "VALIDATION_ERROR",
        "agentId/agentName must resolve to a wallet address, or walletAddress must be provided",
        400,
      );
    }

    const amountAtomic = parsePositiveBigInt(command.amountAtomic, "amountAtomic");
    const amount = atomicUsdcToDecimal(command.amountAtomic);
    const sourceAddress = normalizeRequestAddress(walletAddress, "walletAddress");
    const recipientAddressInput = firstNonEmpty(command.recipientAddress, walletAddress);
    if (!recipientAddressInput) {
      throw new AppError("VALIDATION_ERROR", "recipientAddress is required", 400);
    }
    const recipientAddress = normalizeRequestAddress(recipientAddressInput, "recipientAddress");
    const tokenAddress = normalizeRequestAddress(
      this.config.x402.usdcAssetAddress,
      "X402_USDC_ASSET_ADDRESS",
    );
    const chainConfig = gatewayChainConfig(this.config.x402.network);
    const gatewayWallet = normalizeRequestAddress(
      chainConfig.gatewayWallet,
      "Gateway wallet contract",
    );
    const gatewayMinter = normalizeRequestAddress(
      chainConfig.gatewayMinter,
      "Gateway minter contract",
    );

    const gatewayBalance = await this.circleWalletService.getGatewayBalance(
      sourceAddress,
      chainConfig.domain,
    );
    if (gatewayBalance.available < amountAtomic) {
      throw new AppError(
        "INSUFFICIENT_GATEWAY_BALANCE",
        `Gateway available balance is insufficient. Have ${gatewayBalance.formattedAvailable} USDC, need ${amount}.`,
        424,
        {
          availableAtomic: gatewayBalance.available.toString(),
          requiredAtomic: amountAtomic.toString(),
          gatewayBalance: {
            formattedAvailable: gatewayBalance.formattedAvailable,
            formattedTotal: gatewayBalance.formattedTotal,
          },
        },
      );
    }

    const raw = await this.circleWalletService.withdrawFromGateway({
      walletId: circleWalletId,
      walletAddress: sourceAddress,
      recipientAddress,
      tokenAddress,
      gatewayWallet,
      gatewayMinter,
      sourceDomain: chainConfig.domain,
      destinationDomain: chainConfig.domain,
      amountAtomic: amountAtomic.toString(),
      refId: command.refId,
    });
    const mintTransaction = extractCircleTransactionFromRaw(raw, "mintFinal")
      ?? extractCircleTransactionFromRaw(raw, "mint");
    const gatewayTransferId = extractStringFromRaw(raw, "gatewayTransferId");
    const refreshedGatewayBalance = await this.circleWalletService.getGatewayBalance(
      sourceAddress,
      chainConfig.domain,
    );

    return {
      agentId: binding?.agentId ?? command.agentId ?? null,
      agentName: binding?.agentName ?? command.agentName ?? null,
      circleWalletId,
      walletAddress: sourceAddress,
      recipientAddress,
      asset: "USDC",
      amount,
      amountAtomic: amountAtomic.toString(),
      tokenAddress,
      gatewayWallet,
      gatewayMinter,
      blockchain: "BASE-SEPOLIA",
      gatewayTransferId,
      mintTransactionId: mintTransaction?.id ?? null,
      mintTransactionHash: mintTransaction?.txHash ?? null,
      mintState: mintTransaction?.state ?? null,
      gatewayBalance: {
        availableAtomic: refreshedGatewayBalance.available.toString(),
        totalAtomic: refreshedGatewayBalance.total.toString(),
        formattedAvailable: refreshedGatewayBalance.formattedAvailable,
        formattedTotal: refreshedGatewayBalance.formattedTotal,
      },
      mode: "gateway_withdraw",
      raw,
    };
  }

  private async gatewayUsdcTransfer(input: {
    command: AgentWalletTransferCommand;
    source: AgentWalletBinding;
    destination: AgentWalletBinding | null;
    fromCircleWalletId: string;
    fromAddress: string;
    toAddress: string;
    amount: string;
    amountAtomic: bigint;
  }): Promise<AgentWalletTransferResult> {
    const fromAddress = normalizeRequestAddress(input.fromAddress, "fromAddress") as `0x${string}`;
    const toAddress = normalizeRequestAddress(input.toAddress, "toAddress") as `0x${string}`;
    const tokenAddress = normalizeRequestAddress(
      this.config.x402.usdcAssetAddress,
      "X402_USDC_ASSET_ADDRESS",
    ) as `0x${string}`;
    const chainConfig = gatewayChainConfig(this.config.x402.network);
    const verifyingContract = normalizeRequestAddress(
      chainConfig.gatewayWallet,
      "Gateway verifying contract",
    ) as `0x${string}`;
    const gatewayBalance = await this.circleWalletService.getGatewayBalance(
      fromAddress,
      chainConfig.domain,
    );
    if (gatewayBalance.available < input.amountAtomic) {
      throw new AppError(
        "INSUFFICIENT_GATEWAY_BALANCE",
        `Gateway available balance is insufficient. Have ${gatewayBalance.formattedAvailable} USDC, need ${input.amount}. Deposit USDC to Circle Gateway before retrying.`,
        424,
        {
          availableAtomic: gatewayBalance.available.toString(),
          requiredAtomic: input.amountAtomic.toString(),
          gatewayBalance: {
            formattedAvailable: gatewayBalance.formattedAvailable,
            formattedTotal: gatewayBalance.formattedTotal,
            formattedWithdrawing: gatewayBalance.formattedWithdrawing,
            formattedWithdrawable: gatewayBalance.formattedWithdrawable,
          },
        },
      );
    }

    if (input.source.mode === "circle" && input.source.accountType !== "EOA") {
      throw new AppError(
        "VALIDATION_ERROR",
        "Gateway batching transfers require an EOA Circle wallet",
        400,
        {
          accountType: input.source.accountType ?? null,
          circleWalletId: input.fromCircleWalletId,
          walletAddress: fromAddress,
        },
      );
    }

    const paymentRequirements = {
      scheme: "exact",
      network: this.config.x402.network,
      asset: tokenAddress,
      amount: input.amountAtomic.toString(),
      payTo: toAddress,
      maxTimeoutSeconds: GATEWAY_AUTH_VALIDITY_SECONDS,
      extra: {
        name: CIRCLE_BATCHING_NAME,
        version: CIRCLE_BATCHING_VERSION,
        verifyingContract,
      },
    };
    const signer: BatchEvmSigner = {
      address: fromAddress,
      signTypedData: (params) =>
        this.circleWalletService.signTypedData({
          walletId: input.fromCircleWalletId,
          data: params,
          memo: input.command.refId,
        }),
    };
    const paymentPayload = await new BatchEvmScheme(signer).createPaymentPayload(
      2,
      paymentRequirements,
    );
    const gatewaySettlement = await this.circleWalletService.settleGatewayPayment({
      paymentPayload,
      paymentRequirements,
    });
    if (!gatewaySettlement.verify.isValid) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle Gateway payment verification failed: ${gatewaySettlement.verify.invalidReason ?? "unknown reason"}`,
        424,
        gatewaySettlement,
      );
    }
    if (!gatewaySettlement.settle.success) {
      throw new AppError(
        "UPSTREAM_REQUEST_FAILED",
        `Circle Gateway settlement failed: ${gatewaySettlement.settle.errorReason ?? "unknown reason"}`,
        424,
        gatewaySettlement,
      );
    }

    const transaction = gatewaySettlement.settle.transaction ?? null;
    return {
      fromAgentId: input.source.agentId ?? input.command.fromAgentId ?? null,
      fromAgentName: input.source.agentName ?? input.command.fromAgentName ?? null,
      fromCircleWalletId: input.fromCircleWalletId,
      fromAddress,
      toAgentId: input.destination?.agentId ?? input.command.toAgentId ?? null,
      toAgentName: input.destination?.agentName ?? input.command.toAgentName ?? null,
      toAddress,
      asset: "USDC",
      amount: input.amount,
      amountEth: null,
      amountAtomic: input.amountAtomic.toString(),
      tokenId: null,
      tokenAddress,
      blockchain: "BASE-SEPOLIA",
      transactionId: transaction,
      transactionHash: transaction,
      state: "SETTLED",
      mode: "gateway",
      raw: {
        gatewaySettlement,
        paymentRequirements,
        paymentPayload,
      },
    };
  }

  async transactionStatus(
    command: AgentWalletTransactionStatusCommand,
  ): Promise<AgentWalletTransactionStatusResult> {
    const transactionId = command.transactionId.trim();
    if (!transactionId) {
      throw new AppError("VALIDATION_ERROR", "transactionId is required", 400);
    }

    const raw = await this.circleWalletService.getTransaction(transactionId);
    const transaction = extractTransaction(raw);
    return {
      transactionId: transaction.id ?? transactionId,
      transactionHash: transaction.txHash,
      state: transaction.state,
      raw,
    };
  }

  async requestTestnetFunds(command: AgentWalletFaucetCommand): Promise<AgentWalletFaucetResult> {
    const binding = await this.resolveBinding({
      agentId: command.agentId,
      agentName: command.agentName,
    });
    const address = firstNonEmpty(command.walletAddress, binding?.walletAddress);
    if (!address) {
      throw new AppError(
        "VALIDATION_ERROR",
        "agentId/agentName must resolve to a wallet address, or walletAddress must be provided",
        400,
      );
    }

    const native = command.native ?? true;
    const usdc = command.usdc ?? false;
    await this.circleWalletService.requestTestnetFunds({
      walletAddress: address,
      native,
      usdc,
    });
    return {
      address: normalizeRequestAddress(address, "walletAddress"),
      blockchain: "BASE-SEPOLIA",
      native,
      usdc,
      status: "requested",
    };
  }

  private async resolveBinding(command: {
    agentId?: string;
    agentName?: string;
  }) {
    if (hasNonEmptyValue(command.agentId)) {
      const binding = await this.stateStore.findBindingByAgentId(command.agentId);
      if (binding) {
        return binding;
      }
    }

    if (hasNonEmptyValue(command.agentName)) {
      const binding = await this.stateStore.findBindingByAgentName(command.agentName);
      if (binding) {
        return binding;
      }
    }

    return null;
  }

  private async withLiveBalances(
    status: AgentWalletStatusResult,
  ): Promise<AgentWalletStatusResult> {
    if (this.config.network.mockChain || status.mode !== "circle" || !status.circleWalletId) {
      return status;
    }
    const result: AgentWalletStatusResult = {
      ...status,
      balances: await this.circleWalletService.getWalletBalances(status.circleWalletId),
    };

    try {
      const chainConfig = gatewayChainConfig(this.config.x402.network);
      const gatewayBalance = await this.circleWalletService.getGatewayBalance(
        normalizeRequestAddress(status.walletAddress, "walletAddress"),
        chainConfig.domain,
      );
      result.gatewayBalance = {
        availableAtomic: gatewayBalance.available.toString(),
        totalAtomic: gatewayBalance.total.toString(),
        withdrawingAtomic: gatewayBalance.withdrawing.toString(),
        withdrawableAtomic: gatewayBalance.withdrawable.toString(),
        pendingDepositsAtomic: gatewayBalance.pendingDeposits.toString(),
        pendingBatchAtomic: gatewayBalance.pendingBatch.toString(),
        formattedAvailable: gatewayBalance.formattedAvailable,
        formattedTotal: gatewayBalance.formattedTotal,
        formattedWithdrawing: gatewayBalance.formattedWithdrawing,
        formattedWithdrawable: gatewayBalance.formattedWithdrawable,
        formattedPendingDeposits: gatewayBalance.formattedPendingDeposits,
        formattedPendingBatch: gatewayBalance.formattedPendingBatch,
      };
    } catch (error) {
      result.gatewayBalanceError = error instanceof Error ? error.message : String(error);
    }

    return {
      ...result,
    };
  }
}

function hasNonEmptyValue(value: string | null | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizeAgentName(agentName: string): string {
  return agentName.trim();
}

function firstNonEmpty(...values: Array<string | null | undefined>): string | undefined {
  return values.find((value): value is string => hasNonEmptyValue(value ?? undefined))?.trim();
}

function statusFromBinding(binding: AgentWalletBinding): AgentWalletStatusResult {
  return {
    circleWalletId: binding.circleWalletId,
    circleWalletSetId: binding.circleWalletSetId,
    blockchain: binding.blockchain,
    walletAddress: normalizeRequestAddress(binding.walletAddress, "walletAddress"),
    status: "available",
    balances: {},
    mode: binding.mode,
    ...(binding.accountType === "SCA" || binding.accountType === "EOA"
      ? { accountType: binding.accountType }
      : {}),
  };
}

function isUsableBinding(binding: AgentWalletBinding): boolean {
  return binding.mode !== "circle" || binding.accountType === "EOA";
}

function normalizePositiveDecimal(value: string | undefined, fieldName: string): string {
  if (value === undefined) {
    throw new AppError("VALIDATION_ERROR", `${fieldName} is required`, 400);
  }
  const normalized = value.trim();
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(normalized) || Number(normalized) <= 0) {
    throw new AppError("VALIDATION_ERROR", `${fieldName} must be a positive decimal`, 400);
  }
  return normalized;
}

function atomicUsdcToDecimal(value: string | undefined): string {
  if (value === undefined) {
    throw new AppError("VALIDATION_ERROR", "amountAtomic is required for USDC transfers", 400);
  }
  const atomic = parsePositiveBigInt(value, "amountAtomic");
  const base = 10n ** USDC_DECIMALS;
  const whole = atomic / base;
  const fractional = atomic % base;
  if (fractional === 0n) {
    return whole.toString();
  }
  return `${whole.toString()}.${fractional
    .toString()
    .padStart(Number(USDC_DECIMALS), "0")
    .replace(/0+$/, "")}`;
}

function extractTransaction(payload: unknown): {
  id: string | null;
  txHash: string | null;
  state: string | null;
} {
  const container = isRecord(payload) && isRecord(payload.transaction)
    ? payload.transaction
    : isRecord(payload) && isRecord(payload.data) && isRecord(payload.data.transaction)
      ? payload.data.transaction
      : isRecord(payload) && typeof payload.id === "string"
        ? payload
      : null;
  return {
    id: typeof container?.id === "string" ? container.id : null,
    txHash:
      typeof container?.txHash === "string"
        ? container.txHash
        : typeof container?.transactionHash === "string"
          ? container.transactionHash
          : null,
    state: typeof container?.state === "string" ? container.state : null,
  };
}

function extractCircleTransactionFromRaw(
  payload: unknown,
  key: string,
): { id: string | null; txHash: string | null; state: string | null } | null {
  if (!isRecord(payload) || !isRecord(payload[key])) {
    return null;
  }
  return extractTransaction(payload[key]);
}

function extractStringFromRaw(payload: unknown, key: string): string | null {
  return isRecord(payload) && typeof payload[key] === "string" ? payload[key] : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeRequestAddress(address: string, fieldName: string): string {
  try {
    return normalizeAddress(address).toLowerCase();
  } catch (error) {
    throw new AppError("VALIDATION_ERROR", `${fieldName} must be a valid address`, 400, {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
}

function parsePositiveBigInt(value: string, fieldName: string): bigint {
  try {
    const parsed = BigInt(value);
    if (parsed <= 0n) {
      throw new Error("not positive");
    }
    return parsed;
  } catch {
    throw new AppError("VALIDATION_ERROR", `${fieldName} must be a positive integer`, 400);
  }
}

function gatewayChainConfig(network: string): {
  domain: number;
  gatewayWallet: string;
  gatewayMinter: string;
} {
  if (network === "eip155:84532") {
    return CHAIN_CONFIGS.baseSepolia;
  }
  throw new AppError(
    "NETWORK_MISMATCH",
    `Circle Gateway agent transfers are only configured for eip155:84532, got ${network}`,
    400,
  );
}
