import { loadConfig } from "./config.js";
import { createExecutorMcpApp } from "./mcp/server.js";

async function start() {
  const config = loadConfig();
  const { app } = createExecutorMcpApp(config);

  app.listen(config.mcp.port, "0.0.0.0", (error?: Error) => {
    if (error) {
      console.error("executor-ts MCP startup failed", error);
      process.exit(1);
    }

    console.log(
      JSON.stringify({
        message: "executor-ts MCP server started",
        mcpPort: config.mcp.port,
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
