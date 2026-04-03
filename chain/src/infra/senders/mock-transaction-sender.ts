import { getBytes, keccak256 } from "ethers";

import type { SignedTransfer, SubmittedTransaction } from "../../domain/types.js";
import type { TransactionSender } from "./types.js";

export class MockTransactionSender implements TransactionSender {
  constructor(private readonly expectedChainId: number) {}

  async signTransfer(to: string, amountWei: bigint): Promise<SignedTransfer> {
    const signedTx = `0xmock_signed_${Date.now().toString(16)}`;
    return {
      from: "0x0000000000000000000000000000000000000000",
      to,
      amountWei: amountWei.toString(),
      txHash: hashEntropy(signedTx),
      signedTx,
      mode: "mock",
    };
  }

  async sendTransaction(to: string, amountWei: bigint, data?: string): Promise<SubmittedTransaction> {
    return {
      from: "0x0000000000000000000000000000000000000000",
      to,
      amountWei: amountWei.toString(),
      txHash: hashEntropy(`${to}_${amountWei.toString()}_${data ?? ""}_${Date.now()}`),
      mode: "mock",
    };
  }

  async getHealth() {
    return {
      blockNumber: -1,
      rpcUrl: "mock-chain",
      chainId: null,
      expectedChainId: this.expectedChainId,
      mockChain: true,
    };
  }
}

function hashEntropy(entropy: string): string {
  return keccak256(getBytes(new TextEncoder().encode(entropy)));
}
