import { Contract, JsonRpcProvider, type FeeData } from "ethers";

import type { AppConfig } from "../config.js";
import { AppError } from "../domain/errors.js";

export class NetworkClient {
  private readonly provider: JsonRpcProvider;
  private static readonly ERC20_BALANCE_ABI = [
    "function balanceOf(address account) view returns (uint256)",
  ] as const;

  constructor(private readonly networkConfig: AppConfig["network"]) {
    this.provider = new JsonRpcProvider(networkConfig.rpcUrl);
  }

  getProvider(): JsonRpcProvider {
    return this.provider;
  }

  async getPendingNonce(address: string): Promise<number> {
    return this.provider.getTransactionCount(address, "pending");
  }

  async getBalance(address: string): Promise<bigint> {
    return this.provider.getBalance(address);
  }

  async getErc20Balance(tokenAddress: string, walletAddress: string): Promise<bigint> {
    const contract = new Contract(
      tokenAddress,
      NetworkClient.ERC20_BALANCE_ABI,
      this.provider,
    );
    return contract.balanceOf(walletAddress);
  }

  async getTransactionReceipt(address: string) {
    return this.provider.getTransactionReceipt(address);
  }

  async getFeeData(): Promise<FeeData> {
    return this.provider.getFeeData();
  }

  async assertExpectedChain(): Promise<number> {
    const network = await this.provider.getNetwork();
    const chainId = Number(network.chainId);
    if (chainId !== this.networkConfig.expectedChainId) {
      throw new AppError(
        "NETWORK_MISMATCH",
        `RPC chainId mismatch: expected ${this.networkConfig.expectedChainId}, got ${chainId}`,
        400,
      );
    }
    return chainId;
  }

  async getHealth() {
    if (this.networkConfig.mockChain) {
      return {
        blockNumber: -1,
        rpcUrl: "mock-chain",
        chainId: null,
        expectedChainId: this.networkConfig.expectedChainId,
        mockChain: true,
      };
    }

    const network = await this.provider.getNetwork();
    const blockNumber = await this.provider.getBlockNumber();
    return {
      blockNumber,
      rpcUrl: this.networkConfig.rpcUrl,
      chainId: Number(network.chainId),
      expectedChainId: this.networkConfig.expectedChainId,
      mockChain: false,
    };
  }
}
