import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";

import { loadConfig, type AppConfig } from "../config.js";
import { createChainMcpServer, createChainRuntime } from "./tools.js";
import type { X402FetchService } from "../services/x402-fetch-service.js";

type ChainMcpApp = {
  app: ReturnType<typeof createMcpExpressApp>;
};

function methodNotAllowed(res: any) {
  res.status(405).json({
    jsonrpc: "2.0",
    error: {
      code: -32000,
      message: "Method not allowed.",
    },
    id: null,
  });
}

export function createChainMcpApp(
  config: AppConfig = loadConfig(),
  overrides?: {
    x402FetchService?: X402FetchService;
  },
): ChainMcpApp {
  const runtime = createChainRuntime(config, overrides);
  const app = createMcpExpressApp({ host: "0.0.0.0" });

  app.post("/mcp", async (req: any, res: any) => {
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    const server = createChainMcpServer(runtime);
    res.on("close", () => {
      void transport.close();
      void server.close();
    });

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      console.error("Error handling chain MCP request:", error);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: {
            code: -32603,
            message: "Internal server error",
          },
          id: null,
        });
      }
    }
  });

  app.get("/mcp", async (_req: any, res: any) => {
    methodNotAllowed(res);
  });

  app.delete("/mcp", async (_req: any, res: any) => {
    methodNotAllowed(res);
  });

  return { app };
}
