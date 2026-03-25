import type { AppConfig } from "../config.js";
import type { PolicyAction, PolicyDecision, PolicySnapshot } from "../domain/types.js";
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
  private readonly whitelist: Set<string>;

  constructor(private readonly policyConfig: AppConfig["policy"]) {
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

  snapshot(): PolicySnapshot {
    this.rollDayIfNeeded();
    return {
      dayKey: this.currentDayKey,
      spentTodayWei: this.spentTodayWei.toString(),
      dailyLimitWei: this.policyConfig.dailyLimitWei.toString(),
    };
  }

  private rollDayIfNeeded(): void {
    const nowDayKey = this.buildDayKey();
    if (nowDayKey !== this.currentDayKey) {
      this.currentDayKey = nowDayKey;
      this.spentTodayWei = 0n;
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
