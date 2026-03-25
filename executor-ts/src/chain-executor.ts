import { parseEther } from "ethers";

import type { AppConfig } from "./config.js";
import { NetworkClient } from "./infra/network-client.js";
import { EoaTransactionSender } from "./infra/senders/eoa-transaction-sender.js";
import { MockTransactionSender } from "./infra/senders/mock-transaction-sender.js";
import type { TransactionSender } from "./infra/senders/types.js";
import { PrivateKeySigner } from "./infra/signers/private-key-signer.js";

export function createTransactionSender(config: AppConfig): TransactionSender {
  if (config.network.mockChain) {
    return new MockTransactionSender(config.network.expectedChainId);
  }

  const networkClient = new NetworkClient(config.network);
  const signer = new PrivateKeySigner(config.signer, networkClient);
  return new EoaTransactionSender(signer, networkClient, config.network);
}

export function parseEthAmount(amountEth: string): bigint {
  try {
    return parseEther(amountEth);
  } catch {
    throw new Error(`Invalid ETH amount: ${amountEth}`);
  }
}
