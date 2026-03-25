from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse


BASE_SEPOLIA_NETWORK = "eip155:84532"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_DECIMALS = 6


class X402SellerError(Exception):
    pass


@dataclass(frozen=True)
class X402SellerConfig:
    pay_to: str
    facilitator_url: str
    price: str
    network: str = BASE_SEPOLIA_NETWORK
    asset: str = BASE_SEPOLIA_USDC
    timeout_seconds: float = 20.0
    description: str = "Demo x402 protected resource"
    mime_type: str = "application/json"
    max_timeout_seconds: int = 300
    x402_version: int = 2
    asset_name: str = "USDC"
    asset_version: str = "2"


class X402SellerService:
    def __init__(
        self,
        config: X402SellerConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    async def authorize_or_challenge(self, request: Request) -> dict[str, Any] | JSONResponse:
        payment_required = self.build_payment_required(self.build_resource_url(request))
        payment_header = request.headers.get("PAYMENT-SIGNATURE")

        if not payment_header:
            return JSONResponse(
                status_code=402,
                content=payment_required,
                headers={
                    "PAYMENT-REQUIRED": encode_header(payment_required),
                    "content-type": "application/json",
                },
            )

        payment_payload = decode_header(payment_header)
        verify_response = await self.verify_payment(payment_payload, payment_required)
        if not verify_response.get("isValid"):
            return JSONResponse(
                status_code=402,
                content={
                    **payment_required,
                    "error": verify_response.get("invalidMessage")
                    or verify_response.get("invalidReason")
                    or "payment_invalid",
                },
                headers={
                    "PAYMENT-REQUIRED": encode_header(payment_required),
                    "content-type": "application/json",
                },
            )

        settle_response = await self.settle_payment(payment_payload, payment_required)
        if not settle_response.get("success"):
            raise X402SellerError(
                settle_response.get("errorMessage")
                or settle_response.get("errorReason")
                or "x402 settlement failed"
            )

        return settle_response

    def build_payment_required(self, resource_url: str) -> dict[str, Any]:
        return {
            "x402Version": self.config.x402_version,
            "error": "payment_required",
            "resource": {
                "url": resource_url,
                "description": self.config.description,
                "mimeType": self.config.mime_type,
            },
            "accepts": [
                {
                    "scheme": "exact",
                    "network": self.config.network,
                    "asset": self.config.asset,
                    "amount": price_to_atomic(self.config.price, USDC_DECIMALS),
                    "payTo": self.config.pay_to,
                    "maxTimeoutSeconds": self.config.max_timeout_seconds,
                    "extra": {
                        "name": self.config.asset_name,
                        "version": self.config.asset_version,
                    },
                }
            ],
        }

    async def verify_payment(
        self,
        payment_payload: dict[str, Any],
        payment_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "x402Version": payment_payload.get("x402Version", self.config.x402_version),
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements["accepts"][0],
        }
        return await self._post("/verify", payload)

    async def settle_payment(
        self,
        payment_payload: dict[str, Any],
        payment_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "x402Version": payment_payload.get("x402Version", self.config.x402_version),
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements["accepts"][0],
        }
        return await self._post("/settle", payload)

    def build_success_response(
        self,
        body: dict[str, Any],
        settle_response: dict[str, Any],
    ) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=body,
            headers={
                "PAYMENT-RESPONSE": encode_header(settle_response),
                "content-type": "application/json",
            },
        )

    def build_resource_url(self, request: Request) -> str:
        return f"{request.url.scheme}://{request.url.netloc}{request.url.path}"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.config.facilitator_url.rstrip('/')}{path}",
                json=payload,
                headers={
                    "accept": "*/*",
                    "content-type": "application/json",
                    "user-agent": "node",
                },
            )

        if response.status_code >= 400:
            raise X402SellerError(
                f"Facilitator request failed: {response.status_code} {response.text}"
            )
        return response.json()


def encode_header(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def decode_header(value: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(value).decode("utf-8")
        parsed = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as error:
        raise X402SellerError(f"Invalid x402 header payload: {error}") from error

    if not isinstance(parsed, dict):
        raise X402SellerError("Invalid x402 header payload: expected object")
    return parsed


def price_to_atomic(price: str, decimals: int) -> str:
    normalized = price[1:] if price.startswith("$") else price
    try:
        amount = Decimal(normalized)
    except InvalidOperation as error:
        raise X402SellerError(f"Invalid x402 price: {price}") from error

    atomic = int(amount * (10**decimals))
    return str(atomic)
