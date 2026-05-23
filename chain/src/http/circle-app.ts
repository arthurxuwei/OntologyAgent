import { generateEntitySecretCiphertext } from "@circle-fin/developer-controlled-wallets";
import express from "express";

import { loadConfig, type AppConfig } from "../config.js";
import { AgentWalletService } from "../services/agent-wallet-service.js";
import { CircleWalletService } from "../services/circle-wallet-service.js";
import { asyncRoute, sendResult } from "./result.js";

type CircleRuntime = {
  agentWalletService: AgentWalletService;
};

type CircleRuntimeOverrides = {
  agentWalletService?: AgentWalletService;
  circleWalletService?: CircleWalletService;
};

type CircleHttpApp = {
  app: express.Express;
  runtime: CircleRuntime;
};

const ROUTES = [
  "GET /circle/transactions/:transactionId",
  "GET /circle/wallets/status",
  "POST /circle/gateway/deposits",
  "POST /circle/gateway/withdrawals",
  "POST /circle/gas-topups/resume",
  "POST /circle/gas-topups/webhook",
  "POST /circle/settlements",
  "POST /circle/wallets/get-or-create",
  "POST /circle/wallets/import",
  "POST /circle/wallets/init",
];

function optionalQueryString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

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

export function createCircleHttpApp(
  config: AppConfig = loadConfig(),
  overrides?: CircleRuntimeOverrides,
): CircleHttpApp {
  const runtime = createCircleRuntime(config, overrides);
  const app = express();

  app.use(express.json({ limit: "1mb" }));

  app.get("/health", (_req, res) => {
    res.json({
      service: "chief-circle",
      status: "ok",
      mockChain: config.network.mockChain,
      circleBaseUrl: config.circle.baseUrl,
      circleWalletSetId: config.circle.walletSetId,
      agentWalletStatePath: config.agentWallet.statePath,
    });
  });

  app.get("/circle/routes", (_req, res) => {
    res.json({ routes: ROUTES });
  });

  app.post(
    "/circle/wallets/import",
    asyncRoute(async (_req, res) =>
      sendResult(res, async () => {
        const wallets = await runtime.agentWalletService.listCircleWallets();
        return runtime.agentWalletService.saveLocalWallets(wallets);
      }),
    ),
  );

  app.post(
    "/circle/wallets/get-or-create",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.agentWalletService.getOrCreate(req.body)),
    ),
  );

  app.post(
    "/circle/wallets/init",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.agentWalletService.init(req.body)),
    ),
  );

  app.get(
    "/circle/wallets/status",
    asyncRoute(async (req, res) =>
      sendResult(res, () =>
        runtime.agentWalletService.status({
          walletAddress: optionalQueryString(req.query.walletAddress),
          circleWalletId: optionalQueryString(req.query.circleWalletId),
        }),
      ),
    ),
  );

  app.post(
    "/circle/settlements",
    asyncRoute(async (req, res) =>
      sendResult(res, () => {
        if (req.body.toAddress && !req.body.toAgentId && !req.body.toAgentName) {
          return runtime.agentWalletService.withdrawUsdc(req.body);
        }
        return runtime.agentWalletService.transfer({ ...req.body, asset: "USDC" });
      }),
    ),
  );

  app.post(
    "/circle/gateway/deposits",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.agentWalletService.depositToGateway(req.body)),
    ),
  );

  app.post(
    "/circle/gateway/withdrawals",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.agentWalletService.withdrawFromGateway(req.body)),
    ),
  );

  app.post(
    "/circle/gas-topups/webhook",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.agentWalletService.handleGasTopUpWebhook(req.body)),
    ),
  );

  app.post(
    "/circle/gas-topups/resume",
    asyncRoute(async (req, res) =>
      sendResult(res, () => runtime.agentWalletService.resumeGasTopUpGatewayDeposit(req.body)),
    ),
  );

  app.get(
    "/circle/transactions/:transactionId",
    asyncRoute(async (req, res) =>
      sendResult(res, () =>
        runtime.agentWalletService.transactionStatus({
          transactionId: String(req.params.transactionId),
        }),
      ),
    ),
  );

  return { app, runtime };
}
