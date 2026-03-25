import { Wallet, type TransactionRequest, keccak256 } from "ethers";

import type { AppConfig } from "../../config.js";
import { AppError } from "../../domain/errors.js";
import { NetworkClient } from "../network-client.js";

export class PrivateKeySigner {
  constructor(
    private readonly signerConfig: AppConfig["signer"],
    private readonly networkClient: NetworkClient,
  ) {}

  getAddress(): string {
    return this.requireWallet().address;
  }

  async signTransaction(transaction: TransactionRequest): Promise<{ signedTx: string; txHash: string }> {
    const wallet = this.requireWallet().connect(this.networkClient.getProvider());
    const signedTx = await wallet.signTransaction(transaction);
    return {
      signedTx,
      txHash: keccak256(signedTx),
    };
  }

  async sendTransaction(transaction: TransactionRequest): Promise<{ txHash: string }> {
    const wallet = this.requireWallet().connect(this.networkClient.getProvider());
    const response = await wallet.sendTransaction(transaction);
    return {
      txHash: response.hash,
    };
  }

  private requireWallet(): Wallet {
    if (!this.signerConfig.privateKey) {
      throw new AppError("SIGNER_UNAVAILABLE", "PRIVATE_KEY is required for signing transactions", 400);
    }
    return new Wallet(this.signerConfig.privateKey);
  }
}
