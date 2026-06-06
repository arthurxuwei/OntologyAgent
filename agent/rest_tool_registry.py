from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx
from langchain_core.tools import StructuredTool


DEFAULT_LEDGER_HTTP_URL = "http://ledger:8092"
DEFAULT_CHAIN_HTTP_URL = "http://chain:8091"


def build_rest_tools(environ: Mapping[str, str]) -> list[StructuredTool]:
    client = RestActionClient(
        ledger_url=environ.get("LEDGER_HTTP_URL", DEFAULT_LEDGER_HTTP_URL),
        chain_url=environ.get("CHAIN_HTTP_URL", DEFAULT_CHAIN_HTTP_URL),
        timeout_seconds=float(environ.get("AGENT_TOOL_TIMEOUT_SECONDS", "60")),
    )

    return [
        StructuredTool.from_function(
            coroutine=client.route_payment_intent,
            name="route_payment_intent",
            description="Route a payment intent before any paid action or settlement.",
        ),
        StructuredTool.from_function(
            coroutine=client.agent_wallet_get_ledger_state,
            name="agent_wallet_get_ledger_state",
            description="Read ledger-visible Agent Wallet state.",
        ),
        StructuredTool.from_function(
            coroutine=client.agent_wallet_get_or_create,
            name="agent_wallet_get_or_create",
            description="Get or create an Agent Wallet binding and ledger account.",
        ),
        StructuredTool.from_function(
            coroutine=client.agent_wallet_create_onramp_session,
            name="agent_wallet_create_onramp_session",
            description="Create a hosted onramp session for Agent Wallet funding.",
        ),
        StructuredTool.from_function(
            coroutine=client.agent_wallet_transfer,
            name="agent_wallet_transfer",
            description="Transfer settled Agent Wallet funds between internal agents.",
        ),
        StructuredTool.from_function(
            coroutine=client.agent_wallet_settle_ledger_transfer,
            name="agent_wallet_settle_ledger_transfer",
            description="Withdraw Agent Wallet USDC to an external Base address.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_get_wallet_state,
            name="chain_get_wallet_state",
            description="Read chain wallet, policy, and x402 status.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_sign_transfer,
            name="chain_sign_transfer",
            description="Sign a chain transfer through the chain service.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_submit_execution,
            name="chain_submit_execution",
            description="Submit a chain execution through the chain service.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_submit_user_operation,
            name="chain_submit_user_operation",
            description="Submit an ERC-4337 user operation through the chain service.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_get_transaction,
            name="chain_get_transaction",
            description="Read a chain transaction receipt by transaction hash.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_get_user_operation,
            name="chain_get_user_operation",
            description="Read a user operation status by user operation hash.",
        ),
        StructuredTool.from_function(
            coroutine=client.chain_x402_fetch,
            name="chain_x402_fetch",
            description="Perform an x402 paid HTTP/API call through the chain service.",
        ),
    ]


class RestActionClient:
    def __init__(
        self,
        *,
        ledger_url: str,
        chain_url: str,
        timeout_seconds: float,
    ) -> None:
        self.ledger_url = ledger_url.rstrip("/")
        self.chain_url = chain_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def route_payment_intent(
        self,
        purpose: str,
        deliveryMode: str = "unknown",
        requiresAcceptance: bool = False,
        externalService: bool = False,
        serviceUrl: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._post_ledger(
            "/ledger/payment/route",
            {
                "purpose": purpose,
                "deliveryMode": deliveryMode,
                "requiresAcceptance": requiresAcceptance,
                "externalService": externalService,
                "serviceUrl": serviceUrl,
            },
        )

    async def agent_wallet_get_ledger_state(
        self,
        agentId: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._get_ledger(
            "/ledger/state",
            params={"agentId": agentId} if agentId else None,
        )

    async def agent_wallet_get_or_create(
        self,
        agentName: str,
        agentId: str,
        email: Optional[str] = None,
        walletAddress: Optional[str] = None,
        circleWalletId: Optional[str] = None,
        agentDescription: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._post_ledger(
            "/ledger/wallets/get-or-create",
            {
                "agentName": agentName,
                "agentId": agentId,
                "email": email,
                "walletAddress": walletAddress,
                "circleWalletId": circleWalletId,
                "agentDescription": agentDescription,
            },
        )

    async def agent_wallet_create_onramp_session(
        self,
        agentId: str,
        destinationAddress: str,
        paymentAmount: str,
        idempotencyKey: str,
        clientIp: str = "192.0.2.1",
        destinationNetwork: str = "base",
        purchaseCurrency: str = "USDC",
        paymentCurrency: str = "USD",
        partnerUserRef: Optional[str] = None,
        redirectUrl: Optional[str] = None,
        defaultPaymentMethod: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._post_ledger(
            "/onramp/sessions",
            {
                "agentId": agentId,
                "destinationAddress": destinationAddress,
                "paymentAmount": paymentAmount,
                "idempotencyKey": idempotencyKey,
                "clientIp": clientIp,
                "destinationNetwork": destinationNetwork,
                "purchaseCurrency": purchaseCurrency,
                "paymentCurrency": paymentCurrency,
                "partnerUserRef": partnerUserRef,
                "redirectUrl": redirectUrl,
                "defaultPaymentMethod": defaultPaymentMethod,
            },
        )

    async def agent_wallet_transfer(
        self,
        fromAgentId: str,
        toAgentId: str,
        amountAtomic: str,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return await self._post_ledger(
            "/ledger/transfers",
            {
                "fromAgentId": fromAgentId,
                "toAgentId": toAgentId,
                "amountAtomic": amountAtomic,
                "reason": reason,
                "metadata": metadata or {},
            },
        )

    async def agent_wallet_settle_ledger_transfer(
        self,
        agentId: str,
        destinationAddress: str,
        amountAtomic: str,
        ownerEmail: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return await self._post_ledger(
            "/ledger/withdrawals",
            {
                "agentId": agentId,
                "destinationAddress": destinationAddress,
                "amountAtomic": amountAtomic,
                "ownerEmail": ownerEmail,
                "reason": reason,
                "metadata": metadata or {},
            },
        )

    async def chain_get_wallet_state(self) -> dict[str, Any]:
        return await self._get_chain("/chain/wallet-state")

    async def chain_sign_transfer(self, to: str, amountEth: str) -> dict[str, Any]:
        return await self._post_chain(
            "/chain/transfers/sign",
            {"to": to, "amountEth": amountEth},
        )

    async def chain_submit_execution(
        self,
        to: str,
        valueEth: str,
        data: str,
    ) -> dict[str, Any]:
        return await self._post_chain(
            "/chain/executions",
            {"to": to, "valueEth": valueEth, "data": data},
        )

    async def chain_submit_user_operation(
        self,
        target: str,
        maxCostEth: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._post_chain(
            "/chain/user-operations",
            {"target": target, "maxCostEth": maxCostEth, "raw": raw},
        )

    async def chain_get_transaction(self, txHash: str) -> dict[str, Any]:
        return await self._get_chain(f"/chain/transactions/{txHash}")

    async def chain_get_user_operation(self, userOpHash: str) -> dict[str, Any]:
        return await self._get_chain(f"/chain/user-operations/{userOpHash}")

    async def chain_x402_fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        body: Optional[Any] = None,
        paymentPreference: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._post_chain(
            "/x402/fetch",
            {
                "url": url,
                "method": method,
                "headers": headers or {},
                "body": body,
                "paymentPreference": paymentPreference,
            },
        )

    async def _get_ledger(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return await self._get(f"{self.ledger_url}{path}", params=params)

    async def _post_ledger(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"{self.ledger_url}{path}", payload)

    async def _get_chain(self, path: str) -> dict[str, Any]:
        return await self._get(f"{self.chain_url}{path}")

    async def _post_chain(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"{self.chain_url}{path}", payload)

    async def _get(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
        return self._json_or_error(response)

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=_without_none(payload))
        return self._json_or_error(response)

    @staticmethod
    def _json_or_error(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"REST action failed: HTTP {response.status_code} {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("REST action response was not a JSON object")
        return payload


def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
