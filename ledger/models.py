from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from config import DEFAULT_ASSET
from utils import normalize_evm_address


class LedgerAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    agentName: Optional[str] = None
    email: Optional[str] = None
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
    accountType: Optional[str] = None
    dashboardClaimedAt: Optional[str] = None
    dashboardClaimedByEmail: Optional[str] = None
    asset: str = DEFAULT_ASSET
    availableAtomic: str = "0"
    createdAt: str
    updatedAt: str


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entryId: str
    entryType: Literal[
        "credit",
        "agent_transfer",
        "withdrawal",
        "pending_settlement",
        "pending_inbound",
        "withdrawal_submitted",
    ]
    agentId: str
    asset: str = DEFAULT_ASSET
    availableDeltaAtomic: str = "0"
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class LedgerChainRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordId: str
    eventType: Literal[
        "credit",
        "agent_transfer",
        "withdrawal",
    ]
    status: Literal["submitted", "failed"]
    chainAction: str = "submit_execution"
    chainHttpUrl: str
    recorderAddress: str
    txHash: Optional[str] = None
    mode: Optional[str] = None
    entryIds: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    actionResult: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    createdAt: str
    updatedAt: str


class LedgerSettlementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordId: str
    eventType: Literal["agent_transfer", "withdrawal"]
    status: Literal["submitted", "failed"]
    settlementAction: str = "settle_ledger_transfer"
    settlementHttpUrl: str
    transferId: Optional[str] = None
    fromAgentId: str
    toAgentId: Optional[str] = None
    toAddress: Optional[str] = None
    asset: str = DEFAULT_ASSET
    amountAtomic: str
    transactionId: Optional[str] = None
    transactionHash: Optional[str] = None
    transactionState: Optional[str] = None
    mode: Optional[str] = None
    actionResult: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    createdAt: str
    updatedAt: str


class OnrampSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    provider: Literal["coinbase"] = "coinbase"
    providerToken: Optional[str] = None
    providerChannelId: Optional[str] = None
    providerOrderId: Optional[str] = None
    agentId: str
    destinationAddress: str
    destinationNetwork: str = "base"
    purchaseCurrency: str = DEFAULT_ASSET
    paymentCurrency: str = "USD"
    paymentAmount: str
    clientIp: str
    partnerUserRef: Optional[str] = None
    redirectUrl: Optional[str] = None
    defaultPaymentMethod: Optional[str] = None
    idempotencyKey: str
    onrampUrl: str
    status: Literal[
        "created",
        "opened",
        "pending",
        "confirming",
        "credited",
        "failed",
        "expired",
        "cancelled",
    ] = "created"
    creditedAmountAtomic: Optional[str] = None
    txHash: Optional[str] = None
    ledgerEntryId: Optional[str] = None
    createdAt: str
    updatedAt: str
    creditedAt: Optional[str] = None


class OnrampEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    sessionId: str
    provider: Literal["coinbase"] = "coinbase"
    eventType: str
    providerEventId: Optional[str] = None
    rawPayload: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class CircleWebhookEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notificationId: str
    notificationType: str
    status: Literal["received", "processed", "skipped", "failed"]
    transactionId: Optional[str] = None
    agentId: Optional[str] = None
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
    amountAtomic: Optional[str] = None
    reason: Optional[str] = None
    gatewayDepositResult: dict[str, Any] = Field(default_factory=dict)
    rawPayload: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    createdAt: str
    updatedAt: str


class WaitlistApplication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicationId: str
    email: str
    name: str
    company: Optional[str] = None
    intent: Optional[str] = None
    lang: Optional[str] = None
    pageUrl: Optional[str] = None
    submittedAt: Optional[str] = None
    clientIp: Optional[str] = None
    userAgent: Optional[str] = None
    createdAt: str


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: int = 1
    agentId: str
    agentName: str
    ownerEmail: Optional[str] = None
    description: Optional[str] = None
    eigenflux: Optional[dict[str, Any]] = None
    credentialPublicKey: str
    credentialStatus: Literal["active", "revoked"] = "active"
    createdAt: str
    updatedAt: str


class LedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: list[LedgerAccount] = Field(default_factory=list)
    entries: list[LedgerEntry] = Field(default_factory=list)
    onrampSessions: list[OnrampSessionRecord] = Field(default_factory=list)
    onrampEvents: list[OnrampEventRecord] = Field(default_factory=list)
    circleWebhookEvents: list[CircleWebhookEventRecord] = Field(default_factory=list)
    chainRecords: list[LedgerChainRecord] = Field(default_factory=list)
    settlementRecords: list[LedgerSettlementRecord] = Field(default_factory=list)
    agentProfiles: list[AgentProfile] = Field(default_factory=list)


class CreditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amountAtomic: str
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentWalletRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentName: str
    agentId: str
    email: Optional[str] = None
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
    agentDescription: Optional[str] = None


class ClaimLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    agentName: str = Field(min_length=1)
    email: str
    agentDescription: Optional[str] = None


class ClaimLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    agentName: str
    ownerEmail: str
    claimCode: str
    claimUrl: str
    agentUrl: str
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
    accountType: Optional[str] = None


class CreateAgentProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentName: str = Field(min_length=1)
    ownerEmail: Optional[str] = None
    description: Optional[str] = None
    eigenflux: Optional[dict[str, Any]] = None
    credentialPublicKey: str = Field(min_length=1)


class RotateAgentCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    credentialPublicKey: str = Field(min_length=1)


class UpdateAgentProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: Optional[str] = None


class DashboardClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    claimCode: str = Field(min_length=1)
    email: Optional[str] = None


class DebugResetDashboardClaimsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: str = Field(min_length=1)
    agentIds: list[str] = Field(default_factory=list)


class CreateWaitlistApplicationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    email: str = ""
    name: str = ""
    company: Optional[str] = None
    intent: Optional[str] = None
    lang: Optional[str] = None
    page_url: Optional[str] = None
    submitted_at: Optional[str] = None


class GatewayDepositRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    refId: Optional[str] = None


class GatewayWithdrawalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    recipientAddress: Optional[str] = None
    refId: Optional[str] = None

    @field_validator("recipientAddress")
    @classmethod
    def validate_recipient_address(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_evm_address(value)


class AgentTransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fromAgentId: str = Field(min_length=1)
    toAgentId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WithdrawalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    destinationAddress: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    ownerEmail: Optional[str] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("destinationAddress")
    @classmethod
    def validate_destination_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class CreateOnrampSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    destinationAddress: str = Field(min_length=1)
    paymentAmount: str = Field(min_length=1)
    idempotencyKey: str = Field(min_length=1)
    clientIp: str = "192.0.2.1"
    destinationNetwork: str = "base"
    purchaseCurrency: str = DEFAULT_ASSET
    paymentCurrency: str = "USD"
    partnerUserRef: Optional[str] = None
    redirectUrl: Optional[HttpUrl] = None
    defaultPaymentMethod: Optional[str] = None

    @field_validator("paymentAmount")
    @classmethod
    def validate_payment_amount(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("paymentAmount must be a positive decimal string") from error
        if parsed <= 0:
            raise ValueError("paymentAmount must be a positive decimal string")
        return value


class ConfirmOnrampSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    providerOrderId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    txHash: Optional[str] = None
    providerEventId: Optional[str] = None
    rawPayload: dict[str, Any] = Field(default_factory=dict)
