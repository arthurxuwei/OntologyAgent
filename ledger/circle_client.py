from __future__ import annotations

from typing import Any

import httpx


class CircleHttpClient:
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

    async def get_or_create_wallet(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/wallets/get-or-create", payload)

    async def wallet_status(
        self,
        *,
        wallet_address: str | None,
        circle_wallet_id: str | None,
    ) -> dict[str, Any]:
        params = {
            key: value
            for key, value in {
                "walletAddress": wallet_address,
                "circleWalletId": circle_wallet_id,
            }.items()
            if value is not None
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.get(
                f"{self.base_url}/circle/wallets/status",
                params=params,
            )
        return self._json_or_error(response)

    async def gateway_deposit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/gateway/deposits", payload)

    async def gateway_withdraw(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/gateway/withdrawals", payload)

    async def settle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/circle/settlements", payload)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload)
        return self._json_or_error(response)

    @staticmethod
    def _json_or_error(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(
                f"circle REST request failed: HTTP {response.status_code} {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("circle REST response was not a JSON object")
        error = payload.get("error")
        if isinstance(error, dict):
            raise RuntimeError(str(error.get("message") or error))
        return payload
