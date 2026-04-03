import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod/v4";

import { createTransactionSender } from "../chain-executor.js";
import { loadConfig, type AppConfig } from "../config.js";
import { normalizeError } from "../domain/errors.js";
import { BundlerClient } from "../erc4337.js";
import { PolicyGuard } from "../policies/policy-guard.js";
import { ExecutionService } from "../services/execution-service.js";
import { SettlementService } from "../services/settlement-service.js";
import { SignTransferService } from "../services/sign-transfer-service.js";
import { UserOperationService } from "../services/user-operation-service.js";
import { X402FetchService } from "../services/x402-fetch-service.js";

type ExecutorRuntime = {
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  userOperationService: UserOperationService;
  x402FetchService: X402FetchService;
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

export function createExecutorRuntime(
  config: AppConfig = loadConfig(),
  overrides?: {
    x402FetchService?: X402FetchService;
  },
): ExecutorRuntime {
  const sender = createTransactionSender(config);
  const policyGuard = new PolicyGuard(config.policy, config.x402);
  const settlementService = new SettlementService();

  return {
    signTransferService: new SignTransferService(sender, policyGuard, settlementService),
    executionService: new ExecutionService(sender, policyGuard, settlementService),
    userOperationService: new UserOperationService(
      new BundlerClient(config),
      policyGuard,
      settlementService,
    ),
    x402FetchService:
      overrides?.x402FetchService ?? new X402FetchService(config, policyGuard),
  };
}

export function createExecutorMcpServer(runtime: ExecutorRuntime): McpServer {
  const server = new McpServer({
    name: "ontologyagent-executor-ts",
    version: "1.0.0",
  });

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
