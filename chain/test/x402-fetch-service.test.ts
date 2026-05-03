import test from "node:test";
import assert from "node:assert/strict";

import { encodePaymentRequiredHeader, encodePaymentResponseHeader } from "@x402/core/http";
import { verifyTypedData } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import { loadConfig } from "../src/config.js";
import { PolicyGuard } from "../src/policies/policy-guard.js";
import { X402FetchService } from "../src/services/x402-fetch-service.js";

const TEST_PRIVATE_KEY =
  "0x59c6995e998f97a5a0044966f0945382d8f6d5b40f5f0c6d9c0a0f6f6b6b6b6b";
const BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
const BASE_SEPOLIA_GATEWAY_WALLET_BATCHED =
  "0x0077777d7EBA4688BDeF3E311b846F25870A19B9";

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

test("X402FetchService can prefer the circle gateway payment requirement", async () => {
  const config = loadConfig({
    PRIVATE_KEY: TEST_PRIVATE_KEY,
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  let selectedExtraName: string | null = null;
  const fetchImpl: typeof fetch = async (_input, init) => {
    const headers = lowercaseHeaders(init?.headers as Record<string, string> | undefined);
    if (!headers["payment-signature"]) {
      return new Response(
        JSON.stringify({ error: "payment_required" }),
        {
          status: 402,
          headers: {
            "content-type": "application/json",
            "PAYMENT-REQUIRED": encodePaymentRequiredHeader({
              x402Version: 2,
              resource: {
                url: "http://x402-seller:8000/x402/agent-services/research-summary/nanopayments",
                description: "Demo resource",
                mimeType: "application/json",
              },
              accepts: [
                {
                  scheme: "exact",
                  network: "eip155:84532",
                  asset: BASE_SEPOLIA_USDC,
                  amount: "10000",
                  payTo: "0x2222222222222222222222222222222222222222",
                  maxTimeoutSeconds: 300,
                  extra: { name: "USDC", version: "2" },
                },
                {
                  scheme: "exact",
                  network: "eip155:84532",
                  asset: BASE_SEPOLIA_USDC,
                  amount: "10000",
                  payTo: "0x2222222222222222222222222222222222222222",
                  maxTimeoutSeconds: 605400,
                  extra: {
                    name: "GatewayWalletBatched",
                    version: "1",
                    verifyingContract: BASE_SEPOLIA_GATEWAY_WALLET_BATCHED,
                  },
                },
              ],
            }),
          },
        },
      );
    }

    const paymentSignature = headers["payment-signature"];
    assert.ok(paymentSignature);
    const paymentPayload = JSON.parse(Buffer.from(paymentSignature, "base64").toString("utf8"));
    selectedExtraName = paymentPayload.accepted.extra.name;
    assert.equal(
      await verifiesGatewayWalletBatchedSignature(paymentPayload),
      true,
    );

    return new Response(
      JSON.stringify({ ok: true }),
      {
        status: 200,
        headers: {
          "content-type": "application/json",
          "PAYMENT-RESPONSE": encodePaymentResponseHeader({
            success: true,
            transaction: "0xgatewaysettled",
            network: "eip155:84532",
          }),
        },
      },
    );
  };

  const service = new X402FetchService(config, guard, fetchImpl);

  const result = await service.execute({
    url: "http://x402-seller:8000/x402/agent-services/research-summary/nanopayments",
    method: "GET",
    paymentPreference: "circle-gateway",
  });

  assert.equal(selectedExtraName, "GatewayWalletBatched");
  assert.equal(result.payment?.selected.extra.name, "GatewayWalletBatched");
  assert.equal(
    result.payment?.selected.extra.verifyingContract,
    BASE_SEPOLIA_GATEWAY_WALLET_BATCHED,
  );
  assert.equal(result.payment?.response.transaction, "0xgatewaysettled");
});

test("X402FetchService rejects circle gateway preference when seller does not advertise it", async () => {
  const config = loadConfig({
    PRIVATE_KEY: TEST_PRIVATE_KEY,
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  let paidRetryAttempted = false;
  const fetchImpl: typeof fetch = async (_input, init) => {
    const headers = lowercaseHeaders(init?.headers as Record<string, string> | undefined);
    if (headers["payment-signature"]) {
      paidRetryAttempted = true;
    }

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
  };

  const service = new X402FetchService(config, guard, fetchImpl);

  await assert.rejects(
    () =>
      service.execute({
        url: "http://x402-seller:8000/x402/demo-resource",
        method: "GET",
        paymentPreference: "circle-gateway",
      }),
    /GatewayWalletBatched/,
  );
  assert.equal(paidRetryAttempted, false);
});

test("X402FetchService preserves upstream error details from paid retry failures", async () => {
  const config = loadConfig({
    PRIVATE_KEY: TEST_PRIVATE_KEY,
    WHITELISTED_RECIPIENTS: "0x2222222222222222222222222222222222222222",
  });
  const guard = new PolicyGuard(config.policy, config.x402);

  let requestCount = 0;
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
                url: "http://x402-seller:8000/x402/agent-services/research-summary/nanopayments",
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
    assert.ok(headers["payment-signature"]);
    return new Response(
      JSON.stringify({
        detail: "Facilitator request failed: invalid signature",
        errorReason: "INVALID_SIGNATURE",
      }),
      {
        status: 502,
        headers: {
          "content-type": "application/json",
        },
      },
    );
  };

  const service = new X402FetchService(config, guard, fetchImpl);

  await assert.rejects(
    () =>
      service.execute({
        url: "http://x402-seller:8000/x402/agent-services/research-summary/nanopayments",
        method: "GET",
      }),
    (error: any) => {
      assert.equal(error.code, "UPSTREAM_REQUEST_FAILED");
      assert.match(error.message, /paid x402 retry failed/);
      assert.equal(error.details.status, 502);
      assert.equal(error.details.payload.errorReason, "INVALID_SIGNATURE");
      assert.match(error.details.payload.detail, /invalid signature/);
      return true;
    },
  );
});

async function verifiesGatewayWalletBatchedSignature(paymentPayload: {
  accepted: {
    network: string;
    asset: string;
    amount: string;
    payTo: string;
    maxTimeoutSeconds: number;
    extra: {
      name: string;
      version: string;
      verifyingContract: string;
    };
  };
  payload: {
    authorization: {
      from: string;
      to: string;
      value: string;
      validAfter: string;
      validBefore: string;
      nonce: `0x${string}`;
    };
    signature: `0x${string}`;
  };
}): Promise<boolean> {
  const account = privateKeyToAccount(TEST_PRIVATE_KEY);
  return verifyTypedData({
    address: account.address,
    domain: {
      name: paymentPayload.accepted.extra.name,
      version: paymentPayload.accepted.extra.version,
      chainId: 84532,
      verifyingContract: paymentPayload.accepted.extra.verifyingContract as `0x${string}`,
    },
    types: {
      TransferWithAuthorization: [
        { name: "from", type: "address" },
        { name: "to", type: "address" },
        { name: "value", type: "uint256" },
        { name: "validAfter", type: "uint256" },
        { name: "validBefore", type: "uint256" },
        { name: "nonce", type: "bytes32" },
      ],
    },
    primaryType: "TransferWithAuthorization",
    message: {
      from: paymentPayload.payload.authorization.from as `0x${string}`,
      to: paymentPayload.payload.authorization.to as `0x${string}`,
      value: BigInt(paymentPayload.payload.authorization.value),
      validAfter: BigInt(paymentPayload.payload.authorization.validAfter),
      validBefore: BigInt(paymentPayload.payload.authorization.validBefore),
      nonce: paymentPayload.payload.authorization.nonce,
    },
    signature: paymentPayload.payload.signature,
  });
}
