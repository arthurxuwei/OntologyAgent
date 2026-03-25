import {
  getBytes,
  JsonRpcProvider,
  Wallet,
  type TransactionRequest,
  keccak256,
  parseEther,
  parseUnits,
} from "ethers";

import { config } from "./config.js";
import { PolicyEngine } from "./policy-engine.js";
import { normalizeAddress } from "./security.js";

export class ChainExecutor {
  private provider: JsonRpcProvider;

  constructor(private policyEngine: PolicyEngine) {
    this.provider = new JsonRpcProvider(config.rpcUrl);
  }

  async signTransfer(to: string, amountEth: string) {
    const amountWei = parseEthAmount(amountEth);
    const normalizedTo = normalizeAddress(to);
    this.policyEngine.authorize("sign-transfer", normalizedTo, amountWei);

    if (config.mockChain) {
      const pseudoSignedTx = `0xmock_signed_${Date.now().toString(16)}`;
      const txHash = keccak256(getBytes(new TextEncoder().encode(pseudoSignedTx)));
      this.policyEngine.record(amountWei);
      return {
        from: "0x0000000000000000000000000000000000000000",
        to: normalizedTo,
        amountWei: amountWei.toString(),
        txHash,
        signedTx: pseudoSignedTx,
      };
    }

    const wallet = this.requireSigner().connect(this.provider);
    const nonce = await this.provider.getTransactionCount(wallet.address, "pending");
    const network = await this.provider.getNetwork();
    this.assertExpectedChain(network.chainId);
    const feeData = await this.provider.getFeeData();

    const transaction: TransactionRequest = {
      type: 2,
      to: normalizedTo,
      value: amountWei,
      nonce,
      chainId: Number(network.chainId),
      gasLimit: 21_000n,
      maxFeePerGas: feeData.maxFeePerGas ?? parseUnits("20", "gwei"),
      maxPriorityFeePerGas: feeData.maxPriorityFeePerGas ?? parseUnits("2", "gwei"),
    };

    const signedTx = await wallet.signTransaction(transaction);
    const txHash = keccak256(signedTx);
    this.policyEngine.record(amountWei);

    return {
      from: wallet.address,
      to: normalizedTo,
      amountWei: amountWei.toString(),
      txHash,
      signedTx,
    };
  }

  async sendTransaction(
    action: "execute-swap" | "x402-payment",
    to: string,
    amountWei: bigint,
    data?: string,
  ) {
    const normalizedTo = normalizeAddress(to);
    this.policyEngine.authorize(action, normalizedTo, amountWei);

    if (config.mockChain) {
      const entropy = `${action}_${normalizedTo}_${amountWei.toString()}_${Date.now()}`;
      const txHash = keccak256(getBytes(new TextEncoder().encode(entropy)));
      this.policyEngine.record(amountWei);
      return {
        from: "0x0000000000000000000000000000000000000000",
        to: normalizedTo,
        amountWei: amountWei.toString(),
        txHash,
      };
    }

    const wallet = this.requireSigner().connect(this.provider);
    const network = await this.provider.getNetwork();
    this.assertExpectedChain(network.chainId);
    const tx = await wallet.sendTransaction({
      to: normalizedTo,
      value: amountWei,
      data,
    });
    this.policyEngine.record(amountWei);

    return {
      from: wallet.address,
      to: normalizedTo,
      amountWei: amountWei.toString(),
      txHash: tx.hash,
    };
  }

  async getHealth() {
    if (config.mockChain) {
      return {
        blockNumber: -1,
        rpcUrl: "mock-chain",
        chainId: null,
        expectedChainId: config.expectedChainId,
        mockChain: true,
      };
    }
    const network = await this.provider.getNetwork();
    const blockNumber = await this.provider.getBlockNumber();
    return {
      blockNumber,
      rpcUrl: config.rpcUrl,
      chainId: Number(network.chainId),
      expectedChainId: config.expectedChainId,
      mockChain: false,
    };
  }

  private requireSigner(): Wallet {
    if (!config.privateKey) {
      throw new Error("PRIVATE_KEY is required for signing transactions");
    }
    return new Wallet(config.privateKey);
  }

  private assertExpectedChain(chainId: bigint): void {
    if (Number(chainId) !== config.expectedChainId) {
      throw new Error(
        `RPC chainId mismatch: expected ${config.expectedChainId}, got ${chainId.toString()}`,
      );
    }
  }
}

export function parseEthAmount(amountEth: string): bigint {
  try {
    return parseEther(amountEth);
  } catch {
    throw new Error(`Invalid ETH amount: ${amountEth}`);
  }
}
