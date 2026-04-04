import { formatEther } from "ethers";

import type { AppConfig } from "../config.js";
import type { WalletStateResult } from "../domain/types.js";
import { NetworkClient } from "../infra/network-client.js";
import { PrivateKeySigner } from "../infra/signers/private-key-signer.js";
import type { PolicyGuard } from "../policies/policy-guard.js";

export class WalletStateService {
  constructor(
    private readonly config: Pick<AppConfig, "network" | "signer" | "x402">,
    private readonly policyGuard: PolicyGuard,
    private readonly networkClient: NetworkClient | null,
    private readonly signer: PrivateKeySigner | null,
  ) {}

  async execute(): Promise<WalletStateResult> {
    const address = this.getAddress();
    const chain = this.networkClient
      ? await this.networkClient.getHealth()
      : {
          blockNumber: -1,
          rpcUrl: "mock-chain",
          chainId: null,
          expectedChainId: this.config.network.expectedChainId,
          mockChain: true,
        };

    const balanceWei = await this.getBalanceWei(address);

    return {
      wallet: {
        address,
        signerConfigured: this.config.signer.privateKey !== undefined,
        balanceWei: balanceWei.toString(),
        balanceEth: formatEther(balanceWei),
        mockChain: this.config.network.mockChain,
      },
      chain,
      policy: this.policyGuard.snapshot(),
      x402: {
        network: this.config.x402.network,
        asset: this.config.x402.usdcAssetAddress,
        buyerSignerConfigured: this.config.x402.buyerPrivateKey !== undefined,
      },
    };
  }

  private getAddress(): string | null {
    if (this.signer === null) {
      return null;
    }

    try {
      return this.signer.getAddress();
    } catch {
      return null;
    }
  }

  private async getBalanceWei(address: string | null): Promise<bigint> {
    if (address === null) {
      return 0n;
    }

    if (this.config.network.mockChain || this.networkClient === null) {
      return this.config.network.mockBalanceWei;
    }

    await this.networkClient.assertExpectedChain();
    return this.networkClient.getBalance(address);
  }
}
