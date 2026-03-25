type RequestShape = {
  url: string;
  method: string;
  headers?: Record<string, string>;
  body?: unknown;
};

type X402Options = {
  maxRetries: number;
  sendPayment: (attempt: number) => Promise<string>;
};

type X402Result = {
  status: number;
  payload: unknown;
  paymentTxHashes: string[];
};

export async function requestWithX402Retry(
  requestShape: RequestShape,
  options: X402Options,
): Promise<X402Result> {
  const paymentTxHashes: string[] = [];
  let headers = new Headers(requestShape.headers ?? {});

  for (let attempt = 0; attempt <= options.maxRetries; attempt += 1) {
    const response = await fetch(requestShape.url, {
      method: requestShape.method,
      headers,
      body: encodeRequestBody(requestShape.body),
    });

    const parsedPayload = await parseResponsePayload(response);
    if (response.status !== 402) {
      return {
        status: response.status,
        payload: parsedPayload,
        paymentTxHashes,
      };
    }

    if (attempt === options.maxRetries) {
      throw new Error("x402 retry exhausted: upstream still returns 402");
    }

    const txHash = await options.sendPayment(attempt + 1);
    paymentTxHashes.push(txHash);
    headers = new Headers(headers);
    headers.set("x-payment-tx-hash", txHash);
  }

  throw new Error("x402 retry loop terminated unexpectedly");
}

function encodeRequestBody(body: unknown): string | undefined {
  if (body === undefined) {
    return undefined;
  }
  return JSON.stringify(body);
}

async function parseResponsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}
