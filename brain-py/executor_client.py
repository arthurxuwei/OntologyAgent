from __future__ import annotations

from typing import Any

import httpx


class ExecutorClientError(Exception):
    pass


class ExecutorClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def sign_transfer(self, *, to: str, amount_eth: str) -> dict[str, Any]:
        return self._post(
            "/transfers/sign",
            {
                "to": to,
                "amountEth": amount_eth,
            },
        )

    def submit_execution(
        self,
        *,
        to: str,
        value_eth: str = "0",
        data: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "to": to,
            "valueEth": value_eth,
        }
        if data is not None:
            payload["data"] = data

        return self._post(
            "/executions/submit",
            payload,
        )

    def submit_user_operation(
        self,
        *,
        target: str,
        max_cost_eth: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            "/user-operations/submit",
            {
                "target": target,
                "maxCostEth": max_cost_eth,
                "raw": raw,
            },
        )

    def x402_fetch(
        self,
        *,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": url,
            "method": method,
        }
        if headers is not None:
            payload["headers"] = headers
        if body is not None:
            payload["body"] = body

        return self._post(
            "/x402/fetch",
            payload,
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(url, json=payload)

        if response.status_code >= 400:
            raise ExecutorClientError(
                f"executor-ts request failed: path={path}, "
                f"status={response.status_code}, body={response.text}"
            )

        body = response.json()
        if not body.get("ok"):
            raise ExecutorClientError(
                f"executor-ts responded with ok=false: path={path}, body={body}"
            )

        result = body.get("result")
        if not isinstance(result, dict):
            raise ExecutorClientError(
                f"executor-ts response missing result object: path={path}, body={body}"
            )
        return result
