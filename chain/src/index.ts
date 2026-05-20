import { loadConfig } from "./config.js";
import { createChainHttpApp } from "./http/chain-app.js";

async function start() {
  const config = loadConfig();
  const { app } = createChainHttpApp(config);

  app.listen(config.http.port, "0.0.0.0", (error?: Error) => {
    if (error) {
      console.error("chain HTTP startup failed", error);
      process.exit(1);
    }

    console.log(
      JSON.stringify({
        message: "chain HTTP server started",
        httpPort: config.http.port,
        rpcUrl: config.network.rpcUrl,
        expectedChainId: config.network.expectedChainId,
        mockChain: config.network.mockChain,
        dailyLimitWei: config.policy.dailyLimitWei.toString(),
        x402Network: config.x402.network,
        x402FacilitatorUrl: config.x402.facilitatorUrl,
      }),
    );
  });
}

void start();
