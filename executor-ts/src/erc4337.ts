import type { AppConfig } from "./config.js";
import { AppError } from "./domain/errors.js";

type RpcSuccess<T> = {
  jsonrpc: "2.0";
  id: number;
  result: T;
};

type RpcError = {
  jsonrpc: "2.0";
  id: number;
  error: { code: number; message: string; data?: unknown };
};

type UserOperation = Record<string, unknown>;

export class BundlerClient {
  readonly mode: "mock" | "network";

  constructor(private readonly config: Pick<AppConfig, "execution" | "network">) {
    this.mode = config.network.mockChain ? "mock" : "network";
  }

  async send(userOperation: UserOperation): Promise<string> {
    if (this.config.network.mockChain) {
      return `0xmock_userop_${Date.now().toString(16)}`;
    }

    if (!this.config.execution.bundlerRpcUrl) {
      throw new AppError("BUNDLER_ERROR", "BUNDLER_RPC_URL is required for ERC-4337 execution", 400);
    }

    const payload = {
      jsonrpc: "2.0",
      id: Date.now(),
      method: "eth_sendUserOperation",
      params: [userOperation, this.config.network.entryPointAddress],
    };

    const response = await fetch(this.config.execution.bundlerRpcUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const text = await response.text();
      throw new AppError("BUNDLER_ERROR", `Bundler HTTP error: ${response.status} ${text}`, 502);
    }

    const rpcResponse = (await response.json()) as RpcSuccess<string> | RpcError;
    if ("error" in rpcResponse) {
      throw new AppError(
        "BUNDLER_ERROR",
        `Bundler RPC error: ${rpcResponse.error.code} ${rpcResponse.error.message}`,
        502,
      );
    }

    return rpcResponse.result;
  }
}
