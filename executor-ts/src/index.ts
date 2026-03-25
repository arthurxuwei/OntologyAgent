import { buildApp } from "./app.js";
import { loadConfig } from "./config.js";

async function start() {
  const config = loadConfig();
  const app = buildApp(config);

  try {
    await app.listen({ port: config.service.port, host: "0.0.0.0" });
    app.log.info(
      {
        port: config.service.port,
        rpcUrl: config.network.rpcUrl,
        expectedChainId: config.network.expectedChainId,
        mockChain: config.network.mockChain,
        dailyLimitWei: config.policy.dailyLimitWei.toString(),
        x402Network: config.x402.network,
        x402FacilitatorUrl: config.x402.facilitatorUrl,
      },
      "executor-ts started",
    );
  } catch (error) {
    app.log.error({ error }, "executor-ts startup failed");
    process.exit(1);
  }
}

void start();
