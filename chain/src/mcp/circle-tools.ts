import { generateEntitySecretCiphertext } from "@circle-fin/developer-controlled-wallets";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod/v4";

import { loadConfig, type AppConfig } from "../config.js";
import { AgentWalletService } from "../services/agent-wallet-service.js";
import { CircleWalletService } from "../services/circle-wallet-service.js";
import { runTool } from "./result.js";

type CircleRuntime = {
  agentWalletService: AgentWalletService;
};

type CircleRuntimeOverrides = {
  agentWalletService?: AgentWalletService;
  circleWalletService?: CircleWalletService;
};

export function createCircleRuntime(
  config: AppConfig = loadConfig(),
  overrides?: CircleRuntimeOverrides,
): CircleRuntime {
  const circleWalletService =
    overrides?.circleWalletService ??
    new CircleWalletService(config, {
      createEntitySecretCiphertext: (entitySecret) =>
        generateEntitySecretCiphertext({
          apiKey: config.circle.apiKey ?? "",
          entitySecret,
        }),
    });

  return {
    agentWalletService:
      overrides?.agentWalletService ??
      new AgentWalletService(config, undefined, circleWalletService),
  };
}

export function createCircleMcpServer(runtime: CircleRuntime): McpServer {
  const server = new McpServer({
    name: "chief-circle",
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
        agentId: z.string().optional().describe("External network agent id to bind to the wallet"),
        email: z.string().optional().describe("Agent account email to bind to the wallet"),
        walletAddress: z.string().optional().describe("Existing Agent wallet address to reuse"),
        circleWalletId: z.string().optional().describe("Existing Circle wallet id to reuse"),
      },
    },
    async ({ agentName, agentDescription, agentId, email, walletAddress, circleWalletId }) =>
      runTool(() =>
        runtime.agentWalletService.getOrCreate({
          agentName,
          agentDescription,
          agentId,
          email,
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
        agentId: z.string().optional().describe("External network agent id to bind to the wallet"),
        email: z.string().optional().describe("Agent account email to bind to the wallet"),
      },
    },
    async ({ agentName, agentDescription, agentId, email }) =>
      runTool(() => runtime.agentWalletService.init({ agentName, agentDescription, agentId, email })),
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
    "agent_wallet_settle_ledger_transfer",
    {
      description:
        "Backend-only settlement for ledger transfers: settle USDC between bound Agent Wallets through Circle Gateway Nanopayments.",
      inputSchema: {
        fromAgentId: z.string().optional().describe("Source agent id bound to a Circle wallet"),
        fromAgentName: z.string().optional().describe("Source agent name bound to a Circle wallet"),
        fromCircleWalletId: z.string().optional().describe("Explicit source Circle wallet id"),
        toAgentId: z.string().optional().describe("Destination agent id bound to a wallet address"),
        toAgentName: z.string().optional().describe("Destination agent name bound to a wallet address"),
        toAddress: z.string().optional().describe("Explicit destination wallet address"),
        amountAtomic: z.string().describe("USDC amount in atomic units"),
        refId: z.string().optional().describe("Ledger escrow or settlement reference id"),
      },
    },
    async (args) =>
      runTool(() =>
        runtime.agentWalletService.transfer({
          ...args,
          asset: "USDC",
        }),
      ),
  );

  server.registerTool(
    "agent_wallet_transaction_status",
    {
      description: "Return Circle transaction status for an Agent Wallet transfer.",
      inputSchema: {
        transactionId: z.string().describe("Circle transaction id"),
      },
    },
    async ({ transactionId }) =>
      runTool(() => runtime.agentWalletService.transactionStatus({ transactionId })),
  );

  return server;
}
