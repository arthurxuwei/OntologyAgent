import type { SignedTransfer, SubmittedTransaction } from "../../domain/types.js";

export interface TransactionSender {
  signTransfer(to: string, amountWei: bigint): Promise<SignedTransfer>;
  sendTransaction(to: string, amountWei: bigint, data?: string): Promise<SubmittedTransaction>;
  getHealth(): Promise<{
    blockNumber: number;
    rpcUrl: string;
    chainId: number | null;
    expectedChainId: number;
    mockChain: boolean;
  }>;
}
