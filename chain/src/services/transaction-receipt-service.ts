import type { TransactionReceipt } from "ethers";

import type { AppConfig } from "../config.js";
import type { TransactionReceiptStatusResult } from "../domain/types.js";
import type { NetworkClient } from "../infra/network-client.js";

export class TransactionReceiptService {
  constructor(
    private readonly config: Pick<AppConfig, "network">,
    private readonly networkClient: NetworkClient | null,
  ) {}

  async execute(txHash: string): Promise<TransactionReceiptStatusResult> {
    if (this.config.network.mockChain || this.networkClient === null) {
      return {
        txHash,
        found: true,
        finalized: true,
        success: true,
        status: "success",
        blockNumber: 1,
        receipt: {
          transactionHash: txHash,
          blockNumber: 1,
          status: 1,
        },
        mode: "mock",
      };
    }

    const receipt = await this.networkClient.getTransactionReceipt(txHash);
    if (receipt === null) {
      return {
        txHash,
        found: false,
        finalized: false,
        success: false,
        status: "pending",
        blockNumber: null,
        receipt: null,
        mode: "network",
      };
    }

    const success = receipt.status === 1;

    return {
      txHash,
      found: true,
      finalized: true,
      success,
      status: success ? "success" : "reverted",
      blockNumber: receipt.blockNumber,
      receipt: this.serializeReceipt(receipt),
      mode: "network",
    };
  }

  private serializeReceipt(receipt: TransactionReceipt): Record<string, unknown> {
    const json = receipt.toJSON() as Record<string, unknown>;
    return json;
  }
}
