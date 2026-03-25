import Fastify from "fastify";
import { z } from "zod";

import { ChainExecutor, parseEthAmount } from "./chain-executor.js";
import { config } from "./config.js";
import { sendUserOperation } from "./erc4337.js";
import { PolicyEngine } from "./policy-engine.js";
import { requestWithX402Retry } from "./x402.js";

const signTransferSchema = z.object({
  to: z.string(),
  amountEth: z.string(),
});

const executeSwapSchema = z.object({
  apiUrl: z.string().url(),
  apiMethod: z.string().optional().default("POST"),
  apiHeaders: z.record(z.string(), z.string()).optional(),
  apiBody: z.unknown().optional(),
  payment: z.object({
    to: z.string(),
    amountEth: z.string(),
    maxRetries: z.number().int().min(0).max(3).optional(),
  }),
  swapTx: z
    .object({
      to: z.string(),
      valueEth: z.string().optional().default("0"),
      data: z.string().optional(),
    })
    .optional(),
  userOperation: z
    .object({
      target: z.string(),
      maxCostEth: z.string(),
      raw: z.record(z.string(), z.unknown()),
    })
    .optional(),
});

const policyEngine = new PolicyEngine();
const chainExecutor = new ChainExecutor(policyEngine);
const app = Fastify({ logger: true });

app.get("/health", async () => {
  const chainState = await chainExecutor.getHealth();
  return {
    service: "OntologyAgent-executor-ts",
    status: "ok",
    chain: chainState,
    policy: policyEngine.snapshot(),
  };
});

app.post("/sign-transfer", async (request, reply) => {
  try {
    const payload = signTransferSchema.parse(request.body);
    const signed = await chainExecutor.signTransfer(payload.to, payload.amountEth);
    return reply.send({
      ok: true,
      ...signed,
      policy: policyEngine.snapshot(),
    });
  } catch (error) {
    request.log.error({ error }, "failed /sign-transfer");
    return reply.status(400).send({
      ok: false,
      error: normalizeError(error),
    });
  }
});

app.post("/execute-swap", async (request, reply) => {
  try {
    const payload = executeSwapSchema.parse(request.body);
    const paymentAmountWei = parseEthAmount(payload.payment.amountEth);
    const maxRetries = payload.payment.maxRetries ?? config.x402DefaultRetries;

    const upstreamResult = await requestWithX402Retry(
      {
        url: payload.apiUrl,
        method: payload.apiMethod,
        headers: payload.apiHeaders,
        body: payload.apiBody,
      },
      {
        maxRetries,
        sendPayment: async () => {
          const payment = await chainExecutor.sendTransaction(
            "x402-payment",
            payload.payment.to,
            paymentAmountWei,
          );
          return payment.txHash;
        },
      },
    );

    let executionResult: Record<string, string> | undefined;
    let userOpHash: string | undefined;

    if (payload.swapTx) {
      const swapTx = await chainExecutor.sendTransaction(
        "execute-swap",
        payload.swapTx.to,
        parseEthAmount(payload.swapTx.valueEth),
        payload.swapTx.data,
      );
      executionResult = swapTx;
    }

    if (payload.userOperation) {
      const userOpMaxCostWei = parseEthAmount(payload.userOperation.maxCostEth);
      policyEngine.authorize(
        "erc4337-userop",
        payload.userOperation.target,
        userOpMaxCostWei,
      );
      userOpHash = await sendUserOperation(payload.userOperation.raw);
      policyEngine.record(userOpMaxCostWei);
    }

    return reply.send({
      ok: true,
      upstream: upstreamResult,
      swapTx: executionResult,
      userOpHash,
      policy: policyEngine.snapshot(),
    });
  } catch (error) {
    request.log.error({ error }, "failed /execute-swap");
    return reply.status(400).send({
      ok: false,
      error: normalizeError(error),
    });
  }
});

async function start() {
  try {
    await app.listen({ port: config.servicePort, host: "0.0.0.0" });
    app.log.info(
      {
        port: config.servicePort,
        rpcUrl: config.rpcUrl,
        dailyLimitWei: config.dailyLimitWei.toString(),
      },
      "executor-ts started",
    );
  } catch (error) {
    app.log.error({ error }, "executor-ts startup failed");
    process.exit(1);
  }
}

function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

void start();
