import Fastify, { type FastifyInstance } from "fastify";
import { z } from "zod";

import { createTransactionSender } from "./chain-executor.js";
import { loadConfig, type AppConfig } from "./config.js";
import { normalizeError } from "./domain/errors.js";
import { errorResponse, successResponse } from "./domain/results.js";
import type {
  ExecutionCommand,
  TransferSignCommand,
  X402FetchCommand,
  UserOperationCommand,
} from "./domain/types.js";
import { BundlerClient } from "./erc4337.js";
import { PolicyGuard } from "./policies/policy-guard.js";
import { ExecutionService } from "./services/execution-service.js";
import { SettlementService } from "./services/settlement-service.js";
import { SignTransferService } from "./services/sign-transfer-service.js";
import { UserOperationService } from "./services/user-operation-service.js";
import { X402FetchService } from "./services/x402-fetch-service.js";

const transferSignSchema = z.object({
  to: z.string(),
  amountEth: z.string(),
});

const executionSchema = z.object({
  to: z.string(),
  valueEth: z.string().optional().default("0"),
  data: z.string().optional(),
});

const userOperationSchema = z.object({
  target: z.string(),
  maxCostEth: z.string(),
  raw: z.record(z.string(), z.unknown()),
});

const x402FetchSchema = z.object({
  url: z.string().url(),
  method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).optional().default("GET"),
  headers: z.record(z.string(), z.string()).optional(),
  body: z.unknown().optional(),
});

export function buildApp(
  config: AppConfig = loadConfig(),
  overrides?: {
    x402FetchService?: X402FetchService;
  },
): FastifyInstance {
  const app = Fastify({ logger: true });

  const sender = createTransactionSender(config);
  const policyGuard = new PolicyGuard(config.policy, config.x402);
  const settlementService = new SettlementService();
  const signTransferService = new SignTransferService(sender, policyGuard, settlementService);
  const executionService = new ExecutionService(sender, policyGuard, settlementService);
  const userOperationService = new UserOperationService(
    new BundlerClient(config),
    policyGuard,
    settlementService,
  );
  const x402FetchService =
    overrides?.x402FetchService ?? new X402FetchService(config, policyGuard);

  app.get("/health", async (request, reply) => {
    try {
      const chain = await sender.getHealth();
      return reply.send(
        successResponse(String(request.id), {
          service: "OntologyAgent-executor-ts",
          status: "ok" as const,
          chain,
          policy: policyGuard.snapshot(),
          x402: {
            facilitatorUrl: config.x402.facilitatorUrl,
            network: config.x402.network,
            asset: config.x402.usdcAssetAddress,
            buyerSignerConfigured: Boolean(config.x402.buyerPrivateKey),
          },
        }),
      );
    } catch (error) {
      return sendError(reply, request.id, error, request.log, "/health");
    }
  });

  app.post("/transfers/sign", async (request, reply) => {
    try {
      const command = transferSignSchema.parse(request.body) as TransferSignCommand;
      const result = await signTransferService.execute(command);
      return reply.send(successResponse(String(request.id), result));
    } catch (error) {
      return sendError(reply, request.id, error, request.log, "/transfers/sign");
    }
  });

  app.post("/executions/submit", async (request, reply) => {
    try {
      const command = executionSchema.parse(request.body) as ExecutionCommand;
      const result = await executionService.execute(command);
      return reply.send(successResponse(String(request.id), result));
    } catch (error) {
      return sendError(reply, request.id, error, request.log, "/executions/submit");
    }
  });

  app.post("/user-operations/submit", async (request, reply) => {
    try {
      const command = userOperationSchema.parse(request.body) as UserOperationCommand;
      const result = await userOperationService.execute(command);
      return reply.send(successResponse(String(request.id), result));
    } catch (error) {
      return sendError(reply, request.id, error, request.log, "/user-operations/submit");
    }
  });

  app.post("/x402/fetch", async (request, reply) => {
    try {
      const command = x402FetchSchema.parse(request.body) as X402FetchCommand;
      const result = await x402FetchService.execute(command);
      return reply.send(successResponse(String(request.id), result));
    } catch (error) {
      return sendError(reply, request.id, error, request.log, "/x402/fetch");
    }
  });

  return app;
}

function sendError(
  reply: { status: (code: number) => { send: (body: unknown) => unknown } },
  requestId: string | number,
  error: unknown,
  log: { error: (payload: unknown, message: string) => void },
  route: string,
) {
  const normalized = normalizeError(error);
  log.error({ error: normalized }, `failed ${route}`);
  return reply.status(normalized.statusCode).send(errorResponse(String(requestId), normalized));
}
