import type { AppConfig } from "../config.js";
import type { UserOperationStatusResult } from "../domain/types.js";
import type { BundlerClient } from "../erc4337.js";

export class UserOperationStatusService {
  constructor(
    private readonly config: Pick<AppConfig, "network">,
    private readonly bundlerClient: BundlerClient,
  ) {}

  async execute(userOpHash: string): Promise<UserOperationStatusResult> {
    if (this.config.network.mockChain) {
      const txHash = `0xmock_tx_${userOpHash.slice(2)}`;

      return {
        userOpHash,
        found: true,
        finalized: true,
        success: true,
        status: "success",
        txHash,
        receipt: {
          userOpHash,
          transactionHash: txHash,
          blockNumber: 1,
          status: 1,
        },
        mode: "mock",
      };
    }

    const result = await this.bundlerClient.getUserOperationStatus(userOpHash);
    if (result === null) {
      return {
        userOpHash,
        found: false,
        finalized: false,
        success: false,
        status: "pending",
        txHash: null,
        receipt: null,
        mode: "network",
      };
    }

    return {
      userOpHash,
      found: true,
      finalized: result.finalized,
      success: result.success,
      status: result.status,
      txHash: result.txHash,
      receipt: result.receipt,
      mode: "network",
    };
  }
}
