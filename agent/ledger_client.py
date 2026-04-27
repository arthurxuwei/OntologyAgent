from __future__ import annotations

from typing import Any, Optional

import httpx


class LedgerClientError(Exception):
    pass


class LedgerClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def get_state(self) -> dict[str, Any]:
        return await self._request("GET", "/ledger/state")

    async def credit_balance(
        self,
        agent_id: str,
        *,
        amount_atomic: str,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"amountAtomic": amount_atomic}
        if reason is not None:
            payload["reason"] = reason
        return await self._request(
            "POST",
            f"/ledger/accounts/{agent_id}/credit",
            json=payload,
        )

    async def create_escrow(
        self,
        *,
        buyer_agent_id: str,
        seller_agent_id: str,
        amount_atomic: str,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "buyerAgentId": buyer_agent_id,
            "sellerAgentId": seller_agent_id,
            "amountAtomic": amount_atomic,
        }
        if task_id is not None:
            payload["taskId"] = task_id
        if description is not None:
            payload["description"] = description
        return await self._request("POST", "/ledger/escrows", json=payload)

    async def release_escrow(self, escrow_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/ledger/escrows/{escrow_id}/release")

    async def refund_escrow(self, escrow_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/ledger/escrows/{escrow_id}/refund")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, json=json)

        if response.status_code >= 400:
            raise LedgerClientError(
                f"Ledger request failed: {response.status_code} {self._error_detail(response)}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise LedgerClientError("Ledger response must be a JSON object")
        return payload

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
        return response.text
