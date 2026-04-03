import { parseEthAmount } from "../chain-executor.js";
import type { ExecutionCommand, ExecutionResult, PolicyAction } from "../domain/types.js";
import type { TransactionSender } from "../infra/senders/types.js";
import type { PolicyGuard } from "../policies/policy-guard.js";
import type { SettlementService } from "./settlement-service.js";

export class ExecutionService {
  constructor(
    private readonly sender: TransactionSender,
    private readonly policyGuard: PolicyGuard,
    private readonly settlementService: SettlementService,
  ) {}

  async execute(
    command: ExecutionCommand,
    action: PolicyAction = "execution-submit",
  ): Promise<ExecutionResult> {
    const amountWei = parseEthAmount(command.valueEth ?? "0");
    const decision = this.policyGuard.authorize(action, command.to, amountWei);
    const execution = await this.sender.sendTransaction(
      decision.normalizedTo,
      amountWei,
      command.data,
    );
    this.policyGuard.record(amountWei);

    return {
      execution,
      settlement: this.settlementService.forTransaction(execution),
      decision,
      policy: this.policyGuard.snapshot(),
    };
  }

  snapshotPolicy() {
    return this.policyGuard.snapshot();
  }
}
