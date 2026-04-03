import type { AppConfig } from "../../config.js";
import type { SignedTransfer, SubmittedTransaction } from "../../domain/types.js";
import { buildNativeTransferRequest } from "../native-transfer-builder.js";
import { NetworkClient } from "../network-client.js";
import { PrivateKeySigner } from "../signers/private-key-signer.js";
import type { TransactionSender } from "./types.js";

export class EoaTransactionSender implements TransactionSender {
  constructor(
    private readonly signer: PrivateKeySigner,
    private readonly networkClient: NetworkClient,
    private readonly networkConfig: AppConfig["network"],
  ) {}

  async signTransfer(to: string, amountWei: bigint): Promise<SignedTransfer> {
    const address = this.signer.getAddress();
    const nonce = await this.networkClient.getPendingNonce(address);
    const chainId = await this.networkClient.assertExpectedChain();
    const feeData = await this.networkClient.getFeeData();
    const transaction = buildNativeTransferRequest({
      to,
      value: amountWei,
      nonce,
      chainId,
      feeData,
    });
    const signed = await this.signer.signTransaction(transaction);

    return {
      from: address,
      to,
      amountWei: amountWei.toString(),
      txHash: signed.txHash,
      signedTx: signed.signedTx,
      mode: "network",
    };
  }

  async sendTransaction(to: string, amountWei: bigint, data?: string): Promise<SubmittedTransaction> {
    await this.networkClient.assertExpectedChain();
    const address = this.signer.getAddress();
    const sent = await this.signer.sendTransaction({
      to,
      value: amountWei,
      data,
    });

    return {
      from: address,
      to,
      amountWei: amountWei.toString(),
      txHash: sent.txHash,
      mode: "network",
    };
  }

  async getHealth() {
    return this.networkClient.getHealth();
  }
}
