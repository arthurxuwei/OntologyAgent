import express from "express";

import { createTransactionSender } from "../chain-executor.js";
import { loadConfig, type AppConfig } from "../config.js";
import { BundlerClient } from "../erc4337.js";
import { NetworkClient } from "../infra/network-client.js";
import { PrivateKeySigner } from "../infra/signers/private-key-signer.js";
import { PolicyGuard } from "../policies/policy-guard.js";
import { ExecutionService } from "../services/execution-service.js";
import { SettlementService } from "../services/settlement-service.js";
import { SignTransferService } from "../services/sign-transfer-service.js";
import { TransactionReceiptService } from "../services/transaction-receipt-service.js";
import { UserOperationService } from "../services/user-operation-service.js";
import { UserOperationStatusService } from "../services/user-operation-status-service.js";
import { WalletStateService } from "../services/wallet-state-service.js";
import { X402FetchService } from "../services/x402-fetch-service.js";
import { asyncRoute, sendResult } from "./result.js";

type ChainRuntime = {
  walletStateService: WalletStateService;
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  transactionReceiptService: TransactionReceiptService;
  userOperationStatusService: UserOperationStatusService;
  userOperationService: UserOperationService;
  x402FetchService: X402FetchService;
};

type ChainRuntimeOverrides = {
  transactionReceiptService?: TransactionReceiptService;
  userOperationStatusService?: UserOperationStatusService;
  x402FetchService?: X402FetchService;
};

type ChainHttpApp = {
  app: express.Express;
  runtime: ChainRuntime;
};

export function createChainRuntime(
  config: AppConfig = loadConfig(),
  overrides?: ChainRuntimeOverrides,
): ChainRuntime {
  const networkClient = config.network.mockChain ? null : new NetworkClient(config.network);
  const signer = config.signer.privateKey
    ? new PrivateKeySigner(
        config.signer,
        networkClient ?? new NetworkClient(config.network),
      )
    : null;
  const sender = createTransactionSender(config);
  const bundlerClient = new BundlerClient(config);
  const policyGuard = new PolicyGuard(config.policy, config.x402);
  const settlementService = new SettlementService();

  return {
    walletStateService: new WalletStateService(config, policyGuard, networkClient, signer),
    signTransferService: new SignTransferService(sender, policyGuard, settlementService),
    executionService: new ExecutionService(sender, policyGuard, settlementService),
    transactionReceiptService:
      overrides?.transactionReceiptService ?? new TransactionReceiptService(config, networkClient),
    userOperationStatusService:
      overrides?.userOperationStatusService ??
      new UserOperationStatusService(config, bundlerClient),
    userOperationService: new UserOperationService(bundlerClient, policyGuard, settlementService),
    x402FetchService:
      overrides?.x402FetchService ?? new X402FetchService(config, policyGuard),
  };
}

export function createChainHttpApp(
  config: AppConfig = loadConfig(),
  overrides?: ChainRuntimeOverrides,
): ChainHttpApp {
  const runtime = createChainRuntime(config, overrides);
  const app = express();

  app.use(express.json({ limit: "1mb" }));

  app.get(
    "/health",
    asyncRoute(async (_req, res) =>
      sendResult(res, async () => {
        const walletState = await runtime.walletStateService.execute();
        return {
          service: "kovaloop-chain",
          status: "ok" as const,
          mockChain: config.network.mockChain,
          chain: walletState.chain,
          policy: walletState.policy,
          x402: {
            facilitatorUrl: config.x402.facilitatorUrl,
            network: config.x402.network,
            asset: config.x402.usdcAssetAddress,
            buyerSignerConfigured: config.x402.buyerPrivateKey !== undefined,
          },
        };
      }),
    ),
  );

  app.get(
    "/chain/wallet-state",
    asyncRoute(async (_req, res) =>
      sendResult(res, () => runtime.walletStateService.execute()),
    ),
  );

  app.post(
    "/chain/transfers/sign",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.signTransferService.execute(req.body)),
    ),
  );

  app.post(
    "/chain/executions",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.executionService.execute(req.body)),
    ),
  );

  app.post(
    "/chain/user-operations",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.userOperationService.execute(req.body)),
    ),
  );

  app.get(
    "/chain/transactions/:txHash",
    asyncRoute(async (req, res) =>
      sendResult(res, () =>
        runtime.transactionReceiptService.execute(String(req.params.txHash)),
      ),
    ),
  );

  app.get(
    "/chain/user-operations/:userOpHash",
    asyncRoute(async (req, res) =>
      sendResult(res, () =>
        runtime.userOperationStatusService.execute(String(req.params.userOpHash)),
      ),
    ),
  );

  app.post(
    "/x402/fetch",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.x402FetchService.execute(req.body)),
    ),
  );

  return { app, runtime };
}
