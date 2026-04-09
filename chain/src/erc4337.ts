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

export type BundlerUserOperationStatus = {
  status: "pending" | "success" | "failed";
  finalized: boolean;
  success: boolean;
  txHash: string | null;
  receipt: Record<string, unknown> | null;
};

export class BundlerClient {
  readonly mode: "mock" | "network";

  constructor(private readonly config: Pick<AppConfig, "execution" | "network">) {
    this.mode = config.network.mockChain ? "mock" : "network";
  }

  async send(userOperation: UserOperation): Promise<string> {
    if (this.config.network.mockChain) {
      return `0xmock_userop_${Date.now().toString(16)}`;
    }

    return this.request<string>("eth_sendUserOperation", [userOperation, this.config.network.entryPointAddress]);
  }

  async getUserOperationStatus(userOpHash: string): Promise<BundlerUserOperationStatus | null> {
    const receipt = await this.request<Record<string, unknown> | null>("eth_getUserOperationReceipt", [userOpHash]);
    if (receipt === null) {
      return null;
    }

    const bundledReceipt = this.asRecord(receipt.receipt);
    const txHash =
      this.asString(receipt.transactionHash) ?? this.asString(bundledReceipt?.transactionHash) ?? null;
    const receiptStatus = bundledReceipt?.status;
    const success = receiptStatus === 1 || receiptStatus === "0x1" || receiptStatus === "1";

    return {
      status: success ? "success" : "failed",
      finalized: true,
      success,
      txHash,
      receipt: bundledReceipt ?? receipt,
    };
  }

  private async request<T>(method: string, params: unknown[]): Promise<T> {
    if (!this.config.execution.bundlerRpcUrl) {
      throw new AppError("BUNDLER_ERROR", "BUNDLER_RPC_URL is required for ERC-4337 execution", 400);
    }

    const payload = {
      jsonrpc: "2.0" as const,
      id: Date.now(),
      method,
      params,
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

    const rpcResponse = (await response.json()) as RpcSuccess<T> | RpcError;
    if ("error" in rpcResponse) {
      throw new AppError(
        "BUNDLER_ERROR",
        `Bundler RPC error: ${rpcResponse.error.code} ${rpcResponse.error.message}`,
        502,
      );
    }

    return rpcResponse.result;
  }

  private asRecord(value: unknown): Record<string, unknown> | null {
    return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
  }

  private asString(value: unknown): string | null {
    return typeof value === "string" ? value : null;
  }
}
