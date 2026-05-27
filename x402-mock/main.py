from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request


BASE_SEPOLIA_NETWORK = "eip155:84532"

app = FastAPI(title="Kovaloop x402-mock")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": "kovaloop-x402-mock",
        "status": "ok",
    }


@app.post("/x402/facilitator/verify")
async def verify(request: Request) -> dict[str, Any]:
    body = await request.json()
    payment_payload = body.get("paymentPayload")
    payment_requirements = body.get("paymentRequirements")

    if not isinstance(payment_payload, dict) or not isinstance(payment_requirements, dict):
        raise HTTPException(status_code=400, detail="missing paymentPayload or paymentRequirements")

    accepted = payment_payload.get("accepted", {})
    if accepted.get("network") != payment_requirements.get("network"):
        return {
            "isValid": False,
            "invalidReason": "NETWORK_MISMATCH",
            "invalidMessage": "payment network does not match requirements",
        }

    payer = payment_payload.get("payload", {}).get("authorization", {}).get("from")
    return {
        "isValid": True,
        "payer": payer,
    }


@app.post("/x402/facilitator/settle")
async def settle(request: Request) -> dict[str, Any]:
    body = await request.json()
    payment_payload = body.get("paymentPayload")
    payment_requirements = body.get("paymentRequirements")

    if not isinstance(payment_payload, dict) or not isinstance(payment_requirements, dict):
        raise HTTPException(status_code=400, detail="missing paymentPayload or paymentRequirements")

    payer = payment_payload.get("payload", {}).get("authorization", {}).get("from")
    return {
        "success": True,
        "transaction": f"0xmock_x402_settlement_{abs(hash(json_dump_sorted(body))) % 10**12:x}",
        "network": payment_requirements.get("network", BASE_SEPOLIA_NETWORK),
        "payer": payer,
    }


def json_dump_sorted(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
