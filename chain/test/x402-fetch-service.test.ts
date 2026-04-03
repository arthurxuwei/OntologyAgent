import test from "node:test";
import assert from "node:assert/strict";

import { encodePaymentRequiredHeader, encodePaymentResponseHeader } from "@x402/core/http";

import { loadConfig } from "../src/config.js";
import { PolicyGuard } from "../src/policies/policy-guard.js";
import { X402FetchService } from "../src/services/x402-fetch-service.js";

const TEST_PRIVATE_KEY =
  "0x59c6995e998f97a5a0044966f0945382d8f6d5b40f5f0c6d9c0a0f6f6b6b6b6b";

function lowercaseHeaders(headers?: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(headers ?? {}).map(([name, value]) => [name.toLowerCase(), value]),
  );
}

test("X402FetchService performs 402 -> payment-signature -> success flow", async () => {
  const config = loadConfig({
    PRIVATE_KEY: TEST_PRIVATE_KEY,
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  let requestCount = 0;
  let seenPaymentSignature = false;
  let seenContentTypeOnPaidRetry = false;
  const fetchImpl: typeof fetch = async (_input, init) => {
    requestCount += 1;

    if (requestCount === 1) {
      return new Response(
        JSON.stringify({ error: "payment_required" }),
        {
          status: 402,
          headers: {
            "content-type": "application/json",
            "PAYMENT-REQUIRED": encodePaymentRequiredHeader({
              x402Version: 2,
              resource: {
                url: "http://x402-seller:8000/x402/demo-resource",
                description: "Demo resource",
                mimeType: "application/json",
              },
              accepts: [
                {
                  scheme: "exact",
                  network: "eip155:84532",
                  asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                  amount: "10000",
                  payTo: "0x2222222222222222222222222222222222222222",
                  maxTimeoutSeconds: 300,
                  extra: { name: "USDC", version: "2" },
                },
              ],
            }),
          },
        },
      );
    }

    const headers = lowercaseHeaders(init?.headers as Record<string, string> | undefined);
    seenPaymentSignature = Boolean(headers["payment-signature"]);
    seenContentTypeOnPaidRetry = headers["content-type"] === "application/json";

    return new Response(
      JSON.stringify({ ok: true }),
      {
        status: 200,
        headers: {
          "content-type": "application/json",
          "PAYMENT-RESPONSE": encodePaymentResponseHeader({
            success: true,
            transaction: "0xsettled",
            network: "eip155:84532",
          }),
        },
      },
    );
  };

  const service = new X402FetchService(config, guard, fetchImpl);
  const result = await service.execute({
    url: "http://x402-seller:8000/x402/demo-resource",
    method: "POST",
    body: {
      url: "https://example.com",
      markdown: true,
    },
  });

  assert.equal(result.payment?.selected.network, "eip155:84532");
  assert.equal(result.payment?.response.transaction, "0xsettled");
  assert.equal(result.upstream.status, 200);
  assert.equal(seenPaymentSignature, true);
  assert.equal(seenContentTypeOnPaidRetry, true);
});
