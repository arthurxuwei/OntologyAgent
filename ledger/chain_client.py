from __future__ import annotations

from typing import Any

import httpx


class ChainHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def submit_execution(
        self,
        *,
        to: str,
        value_eth: str,
        data: str,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chain/executions",
                json={"to": to, "valueEth": value_eth, "data": data},
            )
        return self._json_or_error(response)

    @staticmethod
    def _json_or_error(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(
                f"chain REST request failed: HTTP {response.status_code} {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("chain REST response was not a JSON object")
        error = payload.get("error")
        if isinstance(error, dict):
            raise RuntimeError(str(error.get("message") or error))
        return payload
