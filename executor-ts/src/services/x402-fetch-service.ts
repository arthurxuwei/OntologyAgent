import { createPublicClient, http } from "viem";
import { baseSepolia } from "viem/chains";
import { privateKeyToAccount } from "viem/accounts";
import { x402Client, x402HTTPClient } from "@x402/core/client";
import { ExactEvmScheme, toClientEvmSigner } from "@x402/evm";

import type { AppConfig } from "../config.js";
import { AppError } from "../domain/errors.js";
import type { X402FetchCommand, X402FetchResult } from "../domain/types.js";
import type { PolicyGuard } from "../policies/policy-guard.js";

export class X402FetchService {
  private readonly fetchImpl: typeof fetch;

  constructor(
    private readonly config: Pick<AppConfig, "x402" | "network">,
    private readonly policyGuard: PolicyGuard,
    fetchImpl: typeof fetch = fetch,
  ) {
    this.fetchImpl = fetchImpl;
  }

  async execute(command: X402FetchCommand): Promise<X402FetchResult> {
    const requestInit = this.buildRequestInit(command);
    const initialResponse = await this.fetchImpl(command.url, requestInit);
    const initialPayload = await parseResponsePayload(initialResponse);

    if (initialResponse.status !== 402) {
      return {
        upstream: initialPayload,
        payment: null,
        decision: null,
        policy: this.policyGuard.snapshot(),
      };
    }

    const httpClient = this.buildHttpClient();
    const paymentRequired = httpClient.getPaymentRequiredResponse(
      (name) => initialResponse.headers.get(name),
      initialPayload.payload,
    );

    const paymentPayload = await httpClient.createPaymentPayload(paymentRequired);
    const accepted = paymentPayload.accepted;
    const decision = this.policyGuard.authorizeX402(
      accepted.payTo,
      BigInt(accepted.amount),
      accepted.network,
      accepted.asset,
    );

    const paidResponse = await this.fetchImpl(command.url, {
      ...requestInit,
      headers: {
        ...normalizeHeaders(requestInit.headers),
        ...httpClient.encodePaymentSignatureHeader(paymentPayload),
      },
    });
    const paidPayload = await parseResponsePayload(paidResponse);
    const paymentResponseHeader = paidResponse.headers.get("PAYMENT-RESPONSE");
    if (!paymentResponseHeader) {
      throw new AppError(
        "X402_PROTOCOL_ERROR",
        "Upstream x402 response is missing PAYMENT-RESPONSE",
        502,
      );
    }

    const settleResponse = httpClient.getPaymentSettleResponse(
      (name) => paidResponse.headers.get(name),
    );
    this.policyGuard.recordX402(BigInt(accepted.amount));

    return {
      upstream: paidPayload,
      payment: {
        requiredVersion: paymentRequired.x402Version,
        selected: {
          scheme: accepted.scheme,
          network: accepted.network,
          asset: accepted.asset,
          amount: accepted.amount,
          payTo: accepted.payTo,
          maxTimeoutSeconds: accepted.maxTimeoutSeconds,
          extra: accepted.extra,
        },
        response: {
          success: settleResponse.success,
          transaction: settleResponse.transaction,
          network: settleResponse.network,
          payer: settleResponse.payer,
          errorReason: settleResponse.errorReason,
          errorMessage: settleResponse.errorMessage,
          extensions: settleResponse.extensions,
        },
      },
      decision,
      policy: this.policyGuard.snapshot(),
    };
  }

  private buildHttpClient(): x402HTTPClient {
    if (!this.config.x402.buyerPrivateKey) {
      throw new AppError(
        "SIGNER_UNAVAILABLE",
        "X402_BUYER_PRIVATE_KEY or PRIVATE_KEY is required for x402 fetch",
        400,
      );
    }

    const account = privateKeyToAccount(this.config.x402.buyerPrivateKey as `0x${string}`);
    const publicClient = createPublicClient({
      chain: baseSepolia,
      transport: http(this.config.network.rpcUrl),
    });
    const signer = toClientEvmSigner(account, publicClient);
    const client = new x402Client().register(
      "eip155:*",
      new ExactEvmScheme(signer, {
        84532: {
          rpcUrl: this.config.network.rpcUrl,
        },
      }),
    );

    return new x402HTTPClient(client);
  }

  private buildRequestInit(command: X402FetchCommand): RequestInit {
    const method = (command.method ?? "GET").toUpperCase();
    const headers = normalizeHeaders(command.headers);
    const init: RequestInit = { method, headers };

    if (command.body !== undefined && method !== "GET" && method !== "HEAD") {
      if (typeof command.body === "string") {
        init.body = command.body;
      } else {
        init.body = JSON.stringify(command.body);
        if (!Object.keys(headers).some((name) => name.toLowerCase() === "content-type")) {
          headers["content-type"] = "application/json";
        }
      }
    }

    return init;
  }
}

function normalizeHeaders(headers?: Record<string, string>): Record<string, string> {
  return { ...(headers ?? {}) };
}

async function parseResponsePayload(response: Response): Promise<{
  status: number;
  contentType: string;
  payload: unknown;
}> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return {
      status: response.status,
      contentType,
      payload: await response.json(),
    };
  }

  return {
    status: response.status,
    contentType,
    payload: await response.text(),
  };
}
