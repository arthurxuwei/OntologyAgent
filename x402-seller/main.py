from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from x402_seller import (
    BASE_SEPOLIA_NETWORK,
    X402SellerConfig,
    X402SellerError,
    X402SellerService,
    default_gateway_verifying_contract,
)

app = FastAPI(title="OntologyAgent x402-seller")


@lru_cache(maxsize=1)
def get_x402_seller_service() -> X402SellerService:
    pay_to = os.getenv("X402_PAY_TO")
    if not pay_to:
        raise RuntimeError("X402_PAY_TO is not configured")

    network = os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK)
    return X402SellerService(
        X402SellerConfig(
            pay_to=pay_to,
            facilitator_url=os.getenv("X402_FACILITATOR_URL", "https://x402.org/facilitator"),
            price=os.getenv("X402_PRICE", "$0.01"),
            network=network,
            timeout_seconds=float(os.getenv("X402_TIMEOUT_SECONDS", "20")),
            gateway_verifying_contract=os.getenv(
                "X402_GATEWAY_VERIFYING_CONTRACT",
                default_gateway_verifying_contract(network) or "",
            )
            or None,
        )
    )


@app.get("/health")
def health() -> dict[str, Any]:
    gateway_contract = os.getenv(
        "X402_GATEWAY_VERIFYING_CONTRACT",
        default_gateway_verifying_contract(os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK))
        or "",
    )
    return {
        "service": "OntologyAgent-x402-seller",
        "status": "ok",
        "x402Network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
        "x402PayToConfigured": bool(os.getenv("X402_PAY_TO")),
        "x402GatewayVerifyingContract": gateway_contract or None,
    }


@app.get("/x402/demo-resource")
async def x402_demo_resource(request: Request):
    return await authorize_paid_response(
        request,
        {
            "ok": True,
            "resource": "demo-x402-resource",
            "network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
            "quote": {
                "tokenIn": "ETH",
                "tokenOut": "USDC",
                "price": os.getenv("X402_PRICE", "$0.01"),
            },
        },
    )


@app.get("/x402/agent-services/research-summary")
async def x402_research_summary_service(request: Request):
    return await authorize_paid_response(
        request,
        lambda settlement: {
            "ok": True,
            "service": "research-summary",
            "summary": "Agent Wallet MVP paid research summary",
            "network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
            "settlement": {
                "success": bool(settlement.get("success")),
                "transaction": settlement.get("transaction"),
                "network": settlement.get("network"),
            },
        },
    )


@app.get("/x402/agent-services/research-summary/nanopayments")
async def x402_research_summary_nanopayments_service(request: Request):
    return await authorize_paid_response(
        request,
        lambda settlement: {
            "ok": True,
            "service": "research-summary-nanopayments",
            "summary": "Circle Gateway nanopayment-backed research summary",
            "network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
            "paymentMethod": "circle_gateway_nano",
            "settlement": {
                "success": bool(settlement.get("success")),
                "transaction": settlement.get("transaction"),
                "network": settlement.get("network"),
            },
        },
        include_gateway_option=True,
    )


async def authorize_paid_response(
    request: Request,
    body: Any,
    *,
    include_gateway_option: bool = False,
):
    try:
        seller = get_x402_seller_service()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        authorization = await seller.authorize_or_challenge(
            request,
            include_gateway_option=include_gateway_option,
        )
    except X402SellerError as error:
        raise HTTPException(status_code=502, detail=error.to_detail()) from error

    if isinstance(authorization, JSONResponse):
        return authorization

    response_body = body(authorization) if callable(body) else body
    return seller.build_success_response(response_body, authorization)
