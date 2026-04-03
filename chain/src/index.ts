import { loadConfig } from "./config.js";
import { createChainMcpApp } from "./mcp/server.js";

async function start() {
  const config = loadConfig();
  const { app } = createChainMcpApp(config);

  app.listen(config.mcp.port, "0.0.0.0", (error?: Error) => {
    if (error) {
      console.error("chain MCP startup failed", error);
      process.exit(1);
    }

    console.log(
      JSON.stringify({
        message: "chain MCP server started",
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
