from __future__ import annotations

from typing import Any, Callable

import httpx


class PaidRequestFlowError(Exception):
    pass


def request_with_payment_retry(
    *,
    url: str,
    method: str,
    max_retries: int,
    timeout_seconds: float,
    send_payment: Callable[[int], str],
    headers: dict[str, str] | None = None,
    body: Any | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    payment_tx_hashes: list[str] = []
    request_headers = dict(headers or {})

    with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
        for attempt in range(max_retries + 1):
            response = client.request(
                method=method,
                url=url,
                headers=request_headers,
                json=body,
            )
            payload = _parse_payload(response)

            if response.status_code != 402:
                return {
                    "upstream": {
                        "status": response.status_code,
                        "payload": payload,
                    },
                    "paymentTxHashes": payment_tx_hashes,
                }

            if attempt == max_retries:
                raise PaidRequestFlowError("x402 retry exhausted: upstream still returns 402")

            tx_hash = send_payment(attempt + 1)
            payment_tx_hashes.append(tx_hash)
            request_headers = dict(request_headers)
            request_headers["x-payment-tx-hash"] = tx_hash

    raise PaidRequestFlowError("x402 retry loop terminated unexpectedly")


def _parse_payload(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text
