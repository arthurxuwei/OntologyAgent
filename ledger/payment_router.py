from __future__ import annotations

from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, HttpUrl


PaymentMethod = Literal[
    "ledger_escrow",
    "ledger_transfer",
    "gateway_nanopayment",
    "circle_withdrawal",
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
            "method": "gateway_nanopayment",
            "reason": (
                "Direct Agent-to-Agent payments use Circle Gateway Nanopayments. "
                "The ledger records the transfer only after Gateway settlement succeeds."
            ),
            "allowedTools": ["agent_wallet_transfer"],
        }

    if intent.deliveryMode == "withdrawal":
        return {
            "method": "gateway_withdrawal",
            "reason": (
                "Agent Wallet USDC withdrawals settle from Circle Gateway "
                "to an external Base address, then the ledger records the outflow."
            ),
            "allowedTools": ["agent_wallet_settle_ledger_transfer"],
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
