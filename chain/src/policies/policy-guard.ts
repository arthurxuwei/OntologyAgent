import type { AppConfig } from "../config.js";
import type {
  PolicyAction,
  PolicyDecision,
  PolicySnapshot,
  X402PolicyDecision,
} from "../domain/types.js";
import { AppError } from "../domain/errors.js";
import {
  assertAmountWithinSingleTxCap,
  assertWhitelistedAddress,
  buildWhitelist,
  normalizeAddress,
} from "../security.js";

export class PolicyGuard {
  private currentDayKey = this.buildDayKey();
  private spentTodayWei = 0n;
  private spentTodayUsdcAtomic = 0n;
  private readonly whitelist: Set<string>;

  constructor(
    private readonly policyConfig: AppConfig["policy"],
    private readonly x402Config: AppConfig["x402"],
  ) {
    this.whitelist = buildWhitelist(policyConfig.whitelist);
  }

  authorize(action: PolicyAction, to: string, amountWei: bigint): PolicyDecision {
    this.rollDayIfNeeded();

    try {
      assertWhitelistedAddress(to, this.whitelist);
      assertAmountWithinSingleTxCap(amountWei, this.policyConfig.singleTxCapWei);
    } catch (error) {
      throw new AppError("POLICY_VIOLATION", normalizePolicyError(error), 400);
    }

    const normalizedTo = normalizeAddress(to);
    const nextSpend = this.spentTodayWei + amountWei;
    if (nextSpend > this.policyConfig.dailyLimitWei) {
      throw new AppError(
        "POLICY_VIOLATION",
        `DAILY_LIMIT exceeded for ${action}: ${nextSpend.toString()} > ${this.policyConfig.dailyLimitWei.toString()} wei`,
        400,
      );
    }

    return {
      action,
      normalizedTo,
      amountWei: amountWei.toString(),
      allowed: true,
    };
  }

  record(amountWei: bigint): void {
    this.rollDayIfNeeded();
    this.spentTodayWei += amountWei;
  }

  authorizeX402(
    to: string,
    amountAtomic: bigint,
    network: string,
    asset: string,
  ): X402PolicyDecision {
    this.rollDayIfNeeded();

    try {
      assertWhitelistedAddress(to, this.whitelist);
    } catch (error) {
      throw new AppError("POLICY_VIOLATION", normalizePolicyError(error), 400);
    }

    if (network !== this.x402Config.network) {
      throw new AppError(
        "POLICY_VIOLATION",
        `x402 network not allowed: expected ${this.x402Config.network}, got ${network}`,
        400,
      );
    }

    if (normalizeAddress(asset) !== normalizeAddress(this.x402Config.usdcAssetAddress)) {
      throw new AppError(
        "POLICY_VIOLATION",
        `x402 asset not allowed: expected ${this.x402Config.usdcAssetAddress}, got ${asset}`,
        400,
      );
    }

    if (amountAtomic > this.x402Config.usdcSingleCapAtomic) {
      throw new AppError(
        "POLICY_VIOLATION",
        `x402 amount exceeds single cap (${this.x402Config.usdcSingleCapAtomic.toString()} atomic units)`,
        400,
      );
    }

    const nextSpend = this.spentTodayUsdcAtomic + amountAtomic;
    if (nextSpend > this.x402Config.usdcDailyCapAtomic) {
      throw new AppError(
        "POLICY_VIOLATION",
        `x402 DAILY_LIMIT exceeded: ${nextSpend.toString()} > ${this.x402Config.usdcDailyCapAtomic.toString()} atomic units`,
        400,
      );
    }

    return {
      action: "x402-fetch",
      normalizedTo: normalizeAddress(to),
      network,
      asset: normalizeAddress(asset),
      amountAtomic: amountAtomic.toString(),
      allowed: true,
    };
  }

  recordX402(amountAtomic: bigint): void {
    this.rollDayIfNeeded();
    this.spentTodayUsdcAtomic += amountAtomic;
  }

  snapshot(): PolicySnapshot {
    this.rollDayIfNeeded();
    return {
      dayKey: this.currentDayKey,
      spentTodayWei: this.spentTodayWei.toString(),
      dailyLimitWei: this.policyConfig.dailyLimitWei.toString(),
      spentTodayUsdcAtomic: this.spentTodayUsdcAtomic.toString(),
      dailyLimitUsdcAtomic: this.x402Config.usdcDailyCapAtomic.toString(),
    };
  }

  private rollDayIfNeeded(): void {
    const nowDayKey = this.buildDayKey();
    if (nowDayKey !== this.currentDayKey) {
      this.currentDayKey = nowDayKey;
      this.spentTodayWei = 0n;
      this.spentTodayUsdcAtomic = 0n;
    }
  }

  private buildDayKey(): string {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = String(now.getUTCMonth() + 1).padStart(2, "0");
    const day = String(now.getUTCDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }
}

function normalizePolicyError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Policy validation failed";
}
