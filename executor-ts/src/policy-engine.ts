import { config } from "./config.js";
import { assertAmountWithinSingleTxCap, assertWhitelistedAddress } from "./security.js";

type PolicyAction = "sign-transfer" | "execute-swap" | "x402-payment" | "erc4337-userop";

export class PolicyEngine {
  private currentDayKey = this.buildDayKey();
  private spentTodayWei = 0n;

  authorize(action: PolicyAction, to: string, amountWei: bigint): void {
    this.rollDayIfNeeded();
    assertWhitelistedAddress(to);
    assertAmountWithinSingleTxCap(amountWei);

    const nextSpend = this.spentTodayWei + amountWei;
    if (nextSpend > config.dailyLimitWei) {
      throw new Error(
        `DAILY_LIMIT exceeded for ${action}: ${nextSpend.toString()} > ${config.dailyLimitWei.toString()} wei`,
      );
    }
  }

  record(amountWei: bigint): void {
    this.rollDayIfNeeded();
    this.spentTodayWei += amountWei;
  }

  snapshot(): { dayKey: string; spentTodayWei: string; dailyLimitWei: string } {
    this.rollDayIfNeeded();
    return {
      dayKey: this.currentDayKey,
      spentTodayWei: this.spentTodayWei.toString(),
      dailyLimitWei: config.dailyLimitWei.toString(),
    };
  }

  private rollDayIfNeeded() {
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
