import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { generateEntitySecretCiphertext } from "@circle-fin/developer-controlled-wallets";
import { z } from "zod/v4";

import { createTransactionSender } from "../chain-executor.js";
import { loadConfig, type AppConfig } from "../config.js";
import { normalizeError } from "../domain/errors.js";
import { BundlerClient } from "../erc4337.js";
import { NetworkClient } from "../infra/network-client.js";
import { PrivateKeySigner } from "../infra/signers/private-key-signer.js";
import { PolicyGuard } from "../policies/policy-guard.js";
import { AgentWalletService } from "../services/agent-wallet-service.js";
import { CircleWalletService } from "../services/circle-wallet-service.js";
import { ExecutionService } from "../services/execution-service.js";
import { SettlementService } from "../services/settlement-service.js";
import { SignTransferService } from "../services/sign-transfer-service.js";
import { TradeIntentExecutionService } from "../services/trade-intent-execution-service.js";
import { TransactionReceiptService } from "../services/transaction-receipt-service.js";
import { UserOperationStatusService } from "../services/user-operation-status-service.js";
import { UserOperationService } from "../services/user-operation-service.js";
import { WalletStateService } from "../services/wallet-state-service.js";
import { X402FetchService } from "../services/x402-fetch-service.js";

type ChainRuntime = {
  walletStateService: WalletStateService;
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  tradeIntentExecutionService: TradeIntentExecutionService;
  transactionReceiptService: TransactionReceiptService;
  userOperationStatusService: UserOperationStatusService;
  userOperationService: UserOperationService;
  x402FetchService: X402FetchService;
  agentWalletService: AgentWalletService;
};

type ChainRuntimeOverrides = {
  transactionReceiptService?: TransactionReceiptService;
  userOperationStatusService?: UserOperationStatusService;
  x402FetchService?: X402FetchService;
};

type StructuredPayload = Record<string, unknown>;

function structuredText(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function successResult(result: StructuredPayload) {
  return {
    content: [
      {
        type: "text" as const,
        text: structuredText(result),
      },
    ],
    structuredContent: result,
  };
}

function errorResult(error: unknown) {
  const normalized = normalizeError(error);
  const payload = {
    error: {
      code: normalized.code,
      message: normalized.message,
      details: normalized.details,
    },
  };

  return {
    content: [
      {
        type: "text" as const,
        text: structuredText(payload),
      },
    ],
    structuredContent: payload,
    isError: true,
  };
}

async function runTool<Result>(
  handler: () => Promise<Result>,
): Promise<ReturnType<typeof successResult> | ReturnType<typeof errorResult>> {
  try {
    return successResult((await handler()) as StructuredPayload);
  } catch (error) {
    return errorResult(error);
  }
}

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
  const circleWalletService = new CircleWalletService(config, {
    createEntitySecretCiphertext: (entitySecret) =>
      generateEntitySecretCiphertext({
        apiKey: config.circle.apiKey ?? "",
        entitySecret,
      }),
  });

  return {
    walletStateService: new WalletStateService(config, policyGuard, networkClient, signer),
    signTransferService: new SignTransferService(sender, policyGuard, settlementService),
    executionService: new ExecutionService(sender, policyGuard, settlementService),
    tradeIntentExecutionService: new TradeIntentExecutionService(config),
    transactionReceiptService:
      overrides?.transactionReceiptService ?? new TransactionReceiptService(config, networkClient),
    userOperationStatusService:
      overrides?.userOperationStatusService ??
      new UserOperationStatusService(config, bundlerClient),
    userOperationService: new UserOperationService(bundlerClient, policyGuard, settlementService),
    x402FetchService,
    agentWalletService: new AgentWalletService(
      config,
      x402FetchService,
      circleWalletService,
    ),
  };
}

export function createChainMcpServer(runtime: ChainRuntime): McpServer {
  const server = new McpServer({
    name: "ontologyagent-chain",
    version: "1.0.0",
  });

  server.registerTool(
    "agent_wallet_import_circle_wallets",
    {
      description:
        "Import existing Circle wallets for the configured wallet set into local Agent Wallet state.",
      inputSchema: {},
    },
    async () =>
      runTool(async () => {
        const wallets = await runtime.agentWalletService.listCircleWallets();
        return runtime.agentWalletService.saveLocalWallets(wallets);
      }),
  );

  server.registerTool(
    "agent_wallet_get_or_create",
    {
      description:
        "Get an existing Agent Wallet when an address or Circle wallet id is supplied; otherwise create one for a named agent.",
      inputSchema: {
        agentName: z.string().describe("Agent name used when a new wallet must be created"),
        agentDescription: z.string().optional().describe("Optional agent description"),
        walletAddress: z.string().optional().describe("Existing Agent wallet address to reuse"),
        circleWalletId: z.string().optional().describe("Existing Circle wallet id to reuse"),
      },
    },
    async ({ agentName, agentDescription, walletAddress, circleWalletId }) =>
      runTool(() =>
        runtime.agentWalletService.getOrCreate({
          agentName,
          agentDescription,
          walletAddress,
          circleWalletId,
        }),
      ),
  );

  server.registerTool(
    "agent_wallet_init",
    {
      description: "Initialize an Agent Wallet for a named agent.",
      inputSchema: {
        agentName: z.string().describe("Agent name used to derive the mock wallet id"),
        agentDescription: z.string().optional().describe("Optional agent description"),
      },
    },
    async ({ agentName, agentDescription }) =>
      runTool(() => runtime.agentWalletService.init({ agentName, agentDescription })),
  );

  server.registerTool(
    "agent_wallet_status",
    {
      description: "Return Agent Wallet status by wallet address or Circle wallet id.",
      inputSchema: {
        walletAddress: z.string().optional().describe("Agent wallet address"),
        circleWalletId: z.string().optional().describe("Circle wallet id"),
      },
    },
    async ({ walletAddress, circleWalletId }) =>
      runTool(() => runtime.agentWalletService.status({ walletAddress, circleWalletId })),
  );

  server.registerTool(
    "agent_wallet_register_x402_service",
    {
      description: "Register an x402 paid service exposed by an Agent Wallet.",
      inputSchema: {
        name: z.string().describe("Service name"),
        path: z.string().describe("HTTP path beginning with /"),
        priceAtomic: z.string().describe("USDC price in atomic units"),
        payTo: z.string().describe("Payment recipient address"),
      },
    },
    async ({ name, path, priceAtomic, payTo }) =>
      runTool(() =>
        runtime.agentWalletService.registerX402Service({ name, path, priceAtomic, payTo }),
      ),
  );

  server.registerTool(
    "agent_wallet_call_x402_service",
    {
      description: "Call an x402 service through the Agent Wallet flow.",
      inputSchema: {
        url: z.string().url().describe("Target x402 upstream URL"),
        method: z
          .enum(["GET", "POST", "PUT", "PATCH", "DELETE"])
          .default("GET")
          .describe("HTTP method"),
        headers: z.record(z.string(), z.string()).optional().describe("Optional request headers"),
        body: z.unknown().optional().describe("Optional request body"),
      },
    },
    async ({ url, method, headers, body }) =>
      runTool(() =>
        runtime.agentWalletService.callX402Service({ url, method, headers, body }),
      ),
  );

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
    "chain_execute_trade_intent",
    {
      description: "Execute a trade intent through the configured trade runtime.",
      inputSchema: {
        intentId: z.string().describe("Unique trade intent identifier"),
        pair: z.string().describe("Trading pair symbol"),
        side: z.enum(["long", "short"]).default("long").describe("Trade direction"),
        amount: z.string().describe("Trade amount as a decimal string"),
        amountType: z
          .enum(["quote", "base"])
          .default("quote")
          .describe("Whether amount is quoted in quote or base units"),
        maxSlippageBps: z
          .int()
          .nonnegative()
          .default(100)
          .describe("Maximum slippage in basis points"),
        strategy: z.string().optional().describe("Optional strategy label"),
        orderType: z
          .enum(["market", "limit"])
          .default("market")
          .describe("Requested order type"),
        limitPrice: z.string().optional().describe("Optional limit price"),
        reason: z.string().optional().describe("Optional execution reason"),
      },
    },
    async (args) => runTool(() => runtime.tradeIntentExecutionService.execute(args)),
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
      },
    },
    async ({ url, method, headers, body }) =>
      runTool(() => runtime.x402FetchService.execute({ url, method, headers, body })),
  );

  return server;
}
