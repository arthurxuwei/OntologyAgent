import type { SettlementResult, SignedTransfer, SubmittedTransaction } from "../domain/types.js";

export class SettlementService {
  forSignedTransfer(transfer: SignedTransfer): SettlementResult {
    return {
      kind: "signed",
      identifier: transfer.txHash,
      mode: transfer.mode,
    };
  }

  forTransaction(execution: SubmittedTransaction): SettlementResult {
    return {
      kind: "submitted",
      identifier: execution.txHash,
      mode: execution.mode,
    };
  }

  forUserOperation(userOpHash: string, mode: "mock" | "network"): SettlementResult {
    return {
      kind: "user-operation",
      identifier: userOpHash,
      mode,
    };
  }
}
