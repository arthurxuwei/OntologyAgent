from __future__ import annotations

from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, HttpUrl


PaymentMethod = Literal[
    "ledger_escrow",
    "ledger_transfer",
    "x402",
    "chain_transfer",
    "onramp",
    "needs_clarification",
]
DeliveryMode = Literal[
    "funding",
    "agent_transfer",
    "async_task",
    "immediate_api",
    "withdrawal",
    "unknown",
]


class PaymentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    purpose: str
    deliveryMode: DeliveryMode = "unknown"
    requiresAcceptance: bool = False
    externalService: bool = False
    serviceUrl: Optional[HttpUrl] = None


class PaymentRouteDecision(TypedDict):
    method: PaymentMethod
    reason: str
    allowedTools: list[str]


def route_payment_intent(intent: PaymentIntent) -> PaymentRouteDecision:
    if intent.deliveryMode == "funding":
        return {
            "method": "onramp",
            "reason": (
                "Funding an Agent Wallet should create a hosted onramp session. "
                "Ledger balance is credited only after confirmed settlement."
            ),
            "allowedTools": ["agent_wallet_create_onramp_session"],
        }

    if intent.deliveryMode == "agent_transfer":
        if intent.externalService:
            return {
                "method": "needs_clarification",
                "reason": "Direct Agent Wallet transfers only apply to internal agent recipients.",
                "allowedTools": [],
            }
        return {
            "method": "ledger_transfer",
            "reason": (
                "Direct Agent-to-Agent payments require a real Circle USDC transfer "
                "before the ledger records the transfer."
            ),
            "allowedTools": ["agent_wallet_transfer"],
        }

    if intent.deliveryMode == "withdrawal":
        return {
            "method": "chain_transfer",
            "reason": "Withdrawals or external wallet transfers require chain transfer tools.",
            "allowedTools": ["chain_sign_transfer", "chain_submit_execution"],
        }

    if intent.deliveryMode == "async_task" or intent.requiresAcceptance:
        if intent.externalService:
            return {
                "method": "needs_clarification",
                "reason": (
                    "external asynchronous work needs clarification because the current "
                    "MVP escrow ledger only governs internal Agent Wallet balances."
                ),
                "allowedTools": [],
            }
        return {
            "method": "ledger_escrow",
            "reason": (
                "Matched asynchronous task payments require ledger escrow so funds can "
                "be locked, released after acceptance, or refunded."
            ),
            "allowedTools": [
                "agent_wallet_create_escrow",
                "agent_wallet_release_escrow",
                "agent_wallet_refund_escrow",
            ],
        }

    if intent.deliveryMode == "immediate_api" and intent.externalService:
        return {
            "method": "x402",
            "reason": "An immediate paid HTTP/API call should use x402 fetch.",
            "allowedTools": ["chain_x402_fetch"],
        }

    return {
        "method": "needs_clarification",
        "reason": (
            "The payment intent is ambiguous. Clarify whether this is an asynchronous "
            "task, immediate paid API call, withdrawal, or another flow."
        ),
        "allowedTools": [],
    }
