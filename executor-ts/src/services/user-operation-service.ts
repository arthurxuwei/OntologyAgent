import { parseEthAmount } from "../chain-executor.js";
import type { UserOperationCommand, UserOperationResult } from "../domain/types.js";
import type { PolicyGuard } from "../policies/policy-guard.js";
import type { SettlementService } from "./settlement-service.js";
import type { BundlerClient } from "../erc4337.js";

export class UserOperationService {
  constructor(
    private readonly bundlerClient: BundlerClient,
    private readonly policyGuard: PolicyGuard,
    private readonly settlementService: SettlementService,
  ) {}

  async execute(command: UserOperationCommand): Promise<UserOperationResult> {
    const maxCostWei = parseEthAmount(command.maxCostEth);
    const decision = this.policyGuard.authorize(
      "user-operation-submit",
      command.target,
      maxCostWei,
    );
    const userOpHash = await this.bundlerClient.send(command.raw);
    this.policyGuard.record(maxCostWei);

    return {
      userOperation: {
        target: decision.normalizedTo,
        maxCostWei: maxCostWei.toString(),
        userOpHash,
      },
      settlement: this.settlementService.forUserOperation(
        userOpHash,
        this.bundlerClient.mode,
      ),
      decision,
      policy: this.policyGuard.snapshot(),
    };
  }
}
