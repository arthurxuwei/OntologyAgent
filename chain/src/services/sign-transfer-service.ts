import { parseEthAmount } from "../chain-executor.js";
import type { PolicyGuard } from "../policies/policy-guard.js";
import type { SettlementService } from "./settlement-service.js";
import type { SignTransferResult, TransferSignCommand } from "../domain/types.js";
import type { TransactionSender } from "../infra/senders/types.js";

export class SignTransferService {
  constructor(
    private readonly sender: TransactionSender,
    private readonly policyGuard: PolicyGuard,
    private readonly settlementService: SettlementService,
  ) {}

  async execute(command: TransferSignCommand): Promise<SignTransferResult> {
    const amountWei = parseEthAmount(command.amountEth);
    const decision = this.policyGuard.authorize("transfer-sign", command.to, amountWei);
    const transfer = await this.sender.signTransfer(decision.normalizedTo, amountWei);
    this.policyGuard.record(amountWei);

    return {
      transfer,
      settlement: this.settlementService.forSignedTransfer(transfer),
      decision,
      policy: this.policyGuard.snapshot(),
    };
  }
}
