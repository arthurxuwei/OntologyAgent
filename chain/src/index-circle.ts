import { loadConfig } from "./config.js";
import { createCircleMcpApp } from "./mcp/circle-server.js";

function circleMcpPort(): number {
  const raw = process.env.CIRCLE_MCP_PORT ?? "8093";
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`CIRCLE_MCP_PORT must be a positive integer, got ${raw}`);
  }
  return parsed;
}

async function start() {
  const config = loadConfig();
  const port = circleMcpPort();
  const { app } = createCircleMcpApp(config);

  app.listen(port, "0.0.0.0", (error?: Error) => {
    if (error) {
      console.error("circle MCP startup failed", error);
      process.exit(1);
    }

    console.log(
      JSON.stringify({
        message: "circle MCP server started",
        mcpPort: port,
        mockChain: config.network.mockChain,
        circleBaseUrl: config.circle.baseUrl,
        circleWalletSetId: config.circle.walletSetId,
        agentWalletStatePath: config.agentWallet.statePath,
      }),
    );
  });
}

void start();
