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
BASE_SEPOLIA_GATEWAY_WALLET_BATCHED = "0x0077777d7EBA4688BDeF3E311b846F25870A19B9"
USDC_DECIMALS = 6
GATEWAY_WALLET_BATCHED_NAME = "GatewayWalletBatched"
GATEWAY_WALLET_BATCHED_VERSION = "1"
GATEWAY_MAX_TIMEOUT_SECONDS = 605400


class X402SellerError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    def to_detail(self) -> str | dict[str, Any]:
        if self.status_code is None and self.payload is None:
            return str(self)

        return {
            "message": str(self),
            "statusCode": self.status_code,
            "payload": self.payload,
        }


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
    gateway_verifying_contract: str | None = None
    gateway_max_timeout_seconds: int = GATEWAY_MAX_TIMEOUT_SECONDS
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

    async def authorize_or_challenge(
        self,
        request: Request,
        *,
        include_gateway_option: bool = False,
    ) -> dict[str, Any] | JSONResponse:
        payment_required = self.build_payment_required(
            self.build_resource_url(request),
            include_gateway_option=include_gateway_option,
        )
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

    def build_payment_required(
        self,
        resource_url: str,
        *,
        include_gateway_option: bool = False,
    ) -> dict[str, Any]:
        accepts = [self._build_standard_accept()]
        if include_gateway_option:
            gateway_accept = self._build_gateway_accept()
            if gateway_accept is not None:
                accepts.append(gateway_accept)
        return {
            "x402Version": self.config.x402_version,
            "error": "payment_required",
            "resource": {
                "url": resource_url,
                "description": self.config.description,
                "mimeType": self.config.mime_type,
            },
            "accepts": accepts,
        }

    async def verify_payment(
        self,
        payment_payload: dict[str, Any],
        payment_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        selected_requirement = self._select_payment_requirement(
            payment_payload,
            payment_requirements,
        )
        payload = {
            "x402Version": payment_payload.get("x402Version", self.config.x402_version),
            "paymentPayload": payment_payload,
            "paymentRequirements": selected_requirement,
        }
        if is_gateway_payment_requirement(selected_requirement):
            payload.pop("x402Version")
        return await self._post(
            self._facilitator_path("/verify", selected_requirement),
            payload,
        )

    async def settle_payment(
        self,
        payment_payload: dict[str, Any],
        payment_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        selected_requirement = self._select_payment_requirement(
            payment_payload,
            payment_requirements,
        )
        payload = {
            "x402Version": payment_payload.get("x402Version", self.config.x402_version),
            "paymentPayload": payment_payload,
            "paymentRequirements": selected_requirement,
        }
        if is_gateway_payment_requirement(selected_requirement):
            payload.pop("x402Version")
        return await self._post(
            self._facilitator_path("/settle", selected_requirement),
            payload,
        )

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

    def _build_standard_accept(self) -> dict[str, Any]:
        return {
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

    def _build_gateway_accept(self) -> dict[str, Any] | None:
        if not self.config.gateway_verifying_contract:
            return None
        return {
            "scheme": "exact",
            "network": self.config.network,
            "asset": self.config.asset,
            "amount": price_to_atomic(self.config.price, USDC_DECIMALS),
            "payTo": self.config.pay_to,
            "maxTimeoutSeconds": self.config.gateway_max_timeout_seconds,
            "extra": {
                "name": GATEWAY_WALLET_BATCHED_NAME,
                "version": GATEWAY_WALLET_BATCHED_VERSION,
                "verifyingContract": self.config.gateway_verifying_contract,
            },
        }

    def _select_payment_requirement(
        self,
        payment_payload: dict[str, Any],
        payment_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        accepts = payment_requirements.get("accepts")
        if not isinstance(accepts, list) or not accepts:
            raise X402SellerError("Invalid payment requirements: accepts missing")

        accepted = payment_payload.get("accepted")
        if not isinstance(accepted, dict):
            return accepts[0]

        for candidate in accepts:
            if self._accepted_requirement_matches(accepted, candidate):
                return candidate

        raise X402SellerError(
            "Accepted payment requirements did not match any advertised option"
        )

    def _accepted_requirement_matches(
        self,
        accepted: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        keys = (
            "scheme",
            "network",
            "asset",
            "amount",
            "payTo",
            "maxTimeoutSeconds",
        )
        for key in keys:
            if accepted.get(key) != candidate.get(key):
                return False

        accepted_extra = accepted.get("extra")
        candidate_extra = candidate.get("extra")
        if not isinstance(candidate_extra, dict):
            return accepted_extra == candidate_extra
        if not isinstance(accepted_extra, dict):
            return False
        for key, value in candidate_extra.items():
            if accepted_extra.get(key) != value:
                return False
        return True

    def _facilitator_path(
        self,
        standard_path: str,
        payment_requirement: dict[str, Any],
    ) -> str:
        if is_gateway_payment_requirement(payment_requirement):
            return f"/v1/x402{standard_path}"
        return standard_path

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            transport=self.transport,
            follow_redirects=True,
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
                "Facilitator request failed",
                status_code=response.status_code,
                payload=parse_response_payload(response),
            )
        try:
            return response.json()
        except ValueError as error:
            raise X402SellerError(
                "Facilitator returned a non-JSON response",
                status_code=response.status_code,
                payload=response.text,
            ) from error


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


def parse_response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def is_gateway_payment_requirement(payment_requirement: dict[str, Any]) -> bool:
    extra = payment_requirement.get("extra")
    return (
        isinstance(extra, dict)
        and extra.get("name") == GATEWAY_WALLET_BATCHED_NAME
        and extra.get("version") == GATEWAY_WALLET_BATCHED_VERSION
    )


def price_to_atomic(price: str, decimals: int) -> str:
    normalized = price[1:] if price.startswith("$") else price
    try:
        amount = Decimal(normalized)
    except InvalidOperation as error:
        raise X402SellerError(f"Invalid x402 price: {price}") from error

    atomic = int(amount * (10**decimals))
    return str(atomic)


def default_gateway_verifying_contract(network: str) -> str | None:
    if network == BASE_SEPOLIA_NETWORK:
        return BASE_SEPOLIA_GATEWAY_WALLET_BATCHED
    return None
