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
import { CircleWalletService } from "./circle-wallet-service.js";
import type { X402FetchService } from "./x402-fetch-service.js";

const MOCK_CIRCLE_WALLET_SET_ID = "mock-circle-wallet-set";
const MOCK_WALLET_ADDRESS = "0x3333333333333333333333333333333333333333";

export class AgentWalletService {
  constructor(
    private readonly config: AppConfig,
    private readonly x402FetchService: X402FetchService,
    private readonly circleWalletService = new CircleWalletService(config),
  ) {}

  async init(command: AgentWalletInitCommand): Promise<AgentWalletInitResult> {
    const agentName = command.agentName.trim();
    if (agentName.length === 0) {
      throw new AppError("VALIDATION_ERROR", "agentName is required", 400);
    }

    return this.circleWalletService.createWallet(agentName);
  }

  async status(command: AgentWalletStatusCommand): Promise<AgentWalletStatusResult> {
    if (!hasNonEmptyValue(command.walletAddress) && !hasNonEmptyValue(command.circleWalletId)) {
      throw new AppError(
        "VALIDATION_ERROR",
        "walletAddress or circleWalletId is required",
        400,
      );
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
    const result = await this.x402FetchService.execute(command);
    return {
      ...result,
      agentWalletTool: "agent_wallet_call_x402_service",
    };
  }
}

function hasNonEmptyValue(value: string | undefined): value is string {
  return value !== undefined && value.trim().length > 0;
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
