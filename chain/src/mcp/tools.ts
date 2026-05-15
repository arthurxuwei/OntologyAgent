import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod/v4";

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
import { UserOperationStatusService } from "../services/user-operation-status-service.js";
import { UserOperationService } from "../services/user-operation-service.js";
import { WalletStateService } from "../services/wallet-state-service.js";
import { X402FetchService } from "../services/x402-fetch-service.js";
import { runTool } from "./result.js";

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
  const x402FetchService =
    overrides?.x402FetchService ?? new X402FetchService(config, policyGuard);

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
    x402FetchService,
  };
}

export function createChainMcpServer(runtime: ChainRuntime): McpServer {
  const server = new McpServer({
    name: "ontologyagent-chain",
    version: "1.0.0",
  });

  server.registerTool(
    "chain_get_wallet_state",
    {
      description:
        "Return the configured signer address, native ETH balance, configured USDC token balance, and chain policy snapshot.",
      inputSchema: {},
    },
    async () => runTool(() => runtime.walletStateService.execute()),
  );

  server.registerTool(
    "chain_sign_transfer",
    {
      description: "Sign an ETH transfer without broadcasting it.",
      inputSchema: {
        to: z.string().describe("Destination address"),
        amountEth: z.string().describe("ETH amount as a decimal string"),
      },
    },
    async ({ to, amountEth }) =>
      runTool(() => runtime.signTransferService.execute({ to, amountEth })),
  );

  server.registerTool(
    "chain_submit_execution",
    {
      description: "Submit a normal on-chain transaction.",
      inputSchema: {
        to: z.string().describe("Transaction destination address"),
        valueEth: z.string().default("0").describe("ETH amount as a decimal string"),
        data: z.string().optional().describe("Optional calldata hex string"),
      },
    },
    async ({ to, valueEth, data }) =>
      runTool(() => runtime.executionService.execute({ to, valueEth, data })),
  );

  server.registerTool(
    "chain_get_transaction_receipt",
    {
      description: "Return the current settlement status for a submitted transaction hash.",
      inputSchema: {
        txHash: z.string().describe("Transaction hash to look up"),
      },
    },
    async ({ txHash }) => runTool(() => runtime.transactionReceiptService.execute(txHash)),
  );

  server.registerTool(
    "chain_get_user_operation_status",
    {
      description: "Return the current settlement status for a submitted ERC-4337 user operation.",
      inputSchema: {
        userOpHash: z.string().describe("User operation hash to look up"),
      },
    },
    async ({ userOpHash }) =>
      runTool(() => runtime.userOperationStatusService.execute(userOpHash)),
  );

  server.registerTool(
    "chain_submit_user_operation",
    {
      description: "Submit an ERC-4337 user operation through the configured bundler.",
      inputSchema: {
        target: z.string().describe("Target address for policy evaluation"),
        maxCostEth: z.string().describe("Maximum allowed ETH cost as a decimal string"),
        raw: z.record(z.string(), z.unknown()).describe("Raw UserOperation object"),
      },
    },
    async ({ target, maxCostEth, raw }) =>
      runTool(() => runtime.userOperationService.execute({ target, maxCostEth, raw })),
  );

  server.registerTool(
    "chain_x402_fetch",
    {
      description: "Execute an x402 fetch flow against a paid upstream endpoint.",
      inputSchema: {
        url: z.string().url().describe("Target x402 upstream URL"),
        method: z
          .enum(["GET", "POST", "PUT", "PATCH", "DELETE"])
          .default("GET")
          .describe("HTTP method"),
        headers: z.record(z.string(), z.string()).optional().describe("Optional request headers"),
        body: z.unknown().optional().describe("Optional request body"),
        paymentPreference: z
          .enum(["standard", "circle-gateway"])
          .optional()
          .describe("Optional x402 payment requirement preference"),
      },
    },
    async ({ url, method, headers, body, paymentPreference }) =>
      runTool(() =>
        runtime.x402FetchService.execute({
          url,
          method,
          headers,
          body,
          paymentPreference,
        }),
      ),
  );

  return server;
}
