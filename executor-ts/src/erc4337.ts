import { config } from "./config.js";

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

export async function sendUserOperation(userOperation: UserOperation): Promise<string> {
  if (!config.bundlerRpcUrl) {
    throw new Error("BUNDLER_RPC_URL is required for ERC-4337 execution");
  }

  const payload = {
    jsonrpc: "2.0",
    id: Date.now(),
    method: "eth_sendUserOperation",
    params: [userOperation, config.entryPointAddress],
  };

  const response = await fetch(config.bundlerRpcUrl, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Bundler HTTP error: ${response.status} ${text}`);
  }

  const rpcResponse = (await response.json()) as RpcSuccess<string> | RpcError;
  if ("error" in rpcResponse) {
    throw new Error(
      `Bundler RPC error: ${rpcResponse.error.code} ${rpcResponse.error.message}`,
    );
  }

  return rpcResponse.result;
}
