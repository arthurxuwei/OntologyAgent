import type { AppConfig } from "../config.js";
import type { TradeIntentCommand, TradeIntentExecutionResult } from "../domain/types.js";

export class TradeIntentExecutionService {
  constructor(private readonly config: AppConfig) {}

  async execute(command: TradeIntentCommand): Promise<TradeIntentExecutionResult> {
    if (command.orderType === "limit") {
      return this.rejected(command, this.mode(), "intent", "limit orders are unsupported in v1");
    }

    if (command.limitPrice) {
      return this.rejected(
        command,
        this.mode(),
        "intent",
        "limitPrice is unsupported for market orders in v1",
      );
    }

    if (command.pair !== this.config.execution.tradeIntentPair) {
      return this.rejected(command, this.mode(), "intent", `unsupported pair: ${command.pair}`);
    }

    if (command.side !== "long") {
      return this.rejected(command, this.mode(), "intent", `unsupported side: ${command.side}`);
    }

    if (command.amountType !== "quote") {
      return this.rejected(
        command,
        this.mode(),
        "intent",
        `unsupported amountType: ${command.amountType}`,
      );
    }

    if (!this.config.network.mockChain) {
      return this.rejected(
        command,
        "network",
        "quote",
        "network execution source is not configured yet",
      );
    }

    return {
      intentId: command.intentId,
      pair: command.pair,
      side: command.side,
      status: "submitted",
      mode: "mock",
      sellToken: this.config.execution.tradeIntentSellToken,
      buyToken: this.config.execution.tradeIntentBuyToken,
      sellAmount: command.amount,
      buyAmount: "0.0005",
      txHash: `0xmock_trade_${command.intentId}`,
      failureStage: null,
      failureReason: null,
    };
  }

  private mode(): "mock" | "network" {
    return this.config.network.mockChain ? "mock" : "network";
  }

  private rejected(
    command: TradeIntentCommand,
    mode: "mock" | "network",
    failureStage: "intent" | "quote",
    failureReason: string,
  ): TradeIntentExecutionResult {
    return {
      intentId: command.intentId,
      pair: command.pair,
      side: command.side,
      status: "rejected",
      mode,
      sellToken: this.config.execution.tradeIntentSellToken,
      buyToken: this.config.execution.tradeIntentBuyToken,
      sellAmount: command.amount,
      buyAmount: null,
      txHash: null,
      failureStage,
      failureReason,
    };
  }
}
