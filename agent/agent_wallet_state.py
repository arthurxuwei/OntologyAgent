from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownerId: str
    provider: Literal["github"]
    providerUserId: str
    login: str
    email: Optional[str] = None
    displayName: Optional[str] = None
    avatarUrl: Optional[str] = None
    createdAt: str
    updatedAt: str


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    name: str
    description: Optional[str] = None
    ownerId: Optional[str] = None
    walletId: str
    walletAddress: str
    circleWalletSetId: Optional[str] = None
    blockchain: str = "BASE-SEPOLIA"
    claimStatus: Literal["unclaimed", "claimed"] = "unclaimed"
    createdAt: str
    updatedAt: str


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claimId: str
    agentId: str
    claimCodeHash: str
    expiresAt: str
    claimedAt: Optional[str] = None
    consumedByOwnerId: Optional[str] = None
    createdAt: str


class ServiceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str
    agentId: str
    name: str
    path: str
    priceAtomic: str
    assetAddress: str
    network: str
    payTo: str
    active: bool
    createdAt: str


class PaymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paymentId: str
    serviceId: str
    buyerKind: Literal["local_x402_buyer"] = "local_x402_buyer"
    sellerAgentId: str
    sellerWalletAddress: str
    amountAtomic: str
    assetAddress: str
    network: str
    status: str
    requestUrl: str
    resultSummary: dict[str, Any]
    txHash: Optional[str] = None
    settlementReference: Optional[str] = None
    createdAt: str


class AgentWalletState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owners: list[Owner] = Field(default_factory=list)
    agents: list[AgentRecord] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    services: list[ServiceRegistration] = Field(default_factory=list)
    payments: list[PaymentRecord] = Field(default_factory=list)


class AgentWalletStore:
    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = self._lock_for_path(path)

    def load(self) -> AgentWalletState:
        with self._lock:
            return self._load_unlocked()

    def save(self, state: AgentWalletState) -> None:
        with self._lock:
            self._save_unlocked(state)

    def _load_unlocked(self) -> AgentWalletState:
        if not os.path.exists(self.path):
            return AgentWalletState()
        with open(self.path, encoding="utf-8") as handle:
            return AgentWalletState.model_validate(json.load(handle))

    def _save_unlocked(self, state: AgentWalletState) -> None:
        parent_dir = os.path.dirname(self.path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        target_dir = parent_dir or "."
        fd, temp_path = tempfile.mkstemp(
            prefix=".agent-wallet-state-",
            suffix=".tmp",
            dir=target_dir,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.model_dump(), handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def _lock_for_path(cls, path: str) -> threading.RLock:
        absolute_path = os.path.abspath(path)
        with cls._locks_guard:
            lock = cls._locks.get(absolute_path)
            if lock is None:
                lock = threading.RLock()
                cls._locks[absolute_path] = lock
            return lock

    def _mutate(self, mutator):
        with self._lock:
            state = self._load_unlocked()
            result = mutator(state)
            self._save_unlocked(state)
            return result

    def upsert_owner(
        self,
        provider: Literal["github"],
        provider_user_id: str,
        login: str,
        email: Optional[str],
        display_name: Optional[str],
        avatar_url: Optional[str],
    ) -> Owner:
        def mutate(state: AgentWalletState) -> Owner:
            current = now_iso()

            for index, owner in enumerate(state.owners):
                if owner.provider == provider and owner.providerUserId == provider_user_id:
                    updated = owner.model_copy(
                        update={
                            "login": login,
                            "email": email,
                            "displayName": display_name,
                            "avatarUrl": avatar_url,
                            "updatedAt": current,
                        }
                    )
                    state.owners[index] = updated
                    return updated

            owner = Owner(
                ownerId=f"owner_{uuid.uuid4().hex}",
                provider=provider,
                providerUserId=provider_user_id,
                login=login,
                email=email,
                displayName=display_name,
                avatarUrl=avatar_url,
                createdAt=current,
                updatedAt=current,
            )
            state.owners.append(owner)
            return owner

        return self._mutate(mutate)

    def create_agent_wallet(
        self,
        agent_name: str,
        agent_description: Optional[str],
        wallet_payload: dict[str, Any],
    ) -> tuple[AgentRecord, str]:
        claim_code = secrets.token_urlsafe(18)

        def mutate(state: AgentWalletState) -> AgentRecord:
            current = now_iso()
            agent = AgentRecord(
                agentId=f"agent_{uuid.uuid4().hex}",
                name=agent_name,
                description=agent_description,
                walletId=wallet_payload["circleWalletId"],
                walletAddress=wallet_payload["walletAddress"],
                circleWalletSetId=wallet_payload.get("circleWalletSetId"),
                blockchain=wallet_payload.get("blockchain", "BASE-SEPOLIA"),
                createdAt=current,
                updatedAt=current,
            )
            claim = ClaimRecord(
                claimId=f"claim_{uuid.uuid4().hex}",
                agentId=agent.agentId,
                claimCodeHash=self.hash_claim_code(claim_code),
                expiresAt=(
                    datetime.now(timezone.utc) + timedelta(hours=24)
                ).isoformat(),
                createdAt=current,
            )

            state.agents.append(agent)
            state.claims.append(claim)
            return agent

        return self._mutate(mutate), claim_code

    def claim_wallet(self, claim_code: str, owner_id: str) -> AgentRecord:
        def mutate(state: AgentWalletState) -> AgentRecord:
            code_hash = self.hash_claim_code(claim_code)
            current = now_iso()

            for claim_index, claim in enumerate(state.claims):
                if claim.claimCodeHash != code_hash:
                    continue
                if claim.claimedAt is not None:
                    raise ValueError("claim code has already been consumed")
                if _parse_iso_datetime(claim.expiresAt) <= datetime.now(timezone.utc):
                    raise ValueError("claim code has expired")

                for agent_index, agent in enumerate(state.agents):
                    if agent.agentId != claim.agentId:
                        continue
                    if agent.claimStatus != "unclaimed":
                        raise ValueError("wallet is already claimed")

                    updated_agent = agent.model_copy(
                        update={
                            "ownerId": owner_id,
                            "claimStatus": "claimed",
                            "updatedAt": current,
                        }
                    )
                    updated_claim = claim.model_copy(
                        update={
                            "claimedAt": current,
                            "consumedByOwnerId": owner_id,
                        }
                    )
                    state.agents[agent_index] = updated_agent
                    state.claims[claim_index] = updated_claim
                    return updated_agent

            raise ValueError("claim code is invalid")

        return self._mutate(mutate)

    def add_service(
        self,
        *,
        agent_id: str,
        service_payload: dict[str, Any],
    ) -> ServiceRegistration:
        def mutate(state: AgentWalletState) -> ServiceRegistration:
            agent = next(
                (item for item in state.agents if item.agentId == agent_id),
                None,
            )
            if agent is None:
                raise ValueError("agent not found")

            service = ServiceRegistration(
                serviceId=f"service_{uuid.uuid4().hex}",
                agentId=agent_id,
                name=service_payload["name"],
                path=service_payload["path"],
                priceAtomic=service_payload["priceAtomic"],
                assetAddress=service_payload["assetAddress"],
                network=service_payload["network"],
                payTo=service_payload["payTo"],
                active=bool(service_payload["active"]),
                createdAt=now_iso(),
            )
            state.services.append(service)
            return service

        return self._mutate(mutate)

    def add_payment(
        self,
        *,
        service_id: str,
        result: dict[str, Any],
        request_url: str,
    ) -> PaymentRecord:
        def mutate(state: AgentWalletState) -> PaymentRecord:
            service = next(
                (item for item in state.services if item.serviceId == service_id),
                None,
            )
            if service is None:
                raise ValueError("service not found")

            agent = next(
                (item for item in state.agents if item.agentId == service.agentId),
                None,
            )
            if agent is None:
                raise ValueError("seller agent not found")

            payment = self._build_payment_record(
                service=service,
                agent=agent,
                result=result,
                request_url=request_url,
            )
            state.payments.append(payment)
            return payment

        return self._mutate(mutate)

    @staticmethod
    def _build_payment_record(
        *,
        service: ServiceRegistration,
        agent: AgentRecord,
        result: dict[str, Any],
        request_url: str,
    ) -> PaymentRecord:
        payment_payload = result.get("payment")
        if not isinstance(payment_payload, dict):
            payment_payload = {}
        settlement = payment_payload.get("response")
        if not isinstance(settlement, dict):
            settlement = {}

        tx_hash = settlement.get("transaction")
        if not isinstance(tx_hash, str):
            tx_hash = None
        success = bool(settlement.get("success"))

        return PaymentRecord(
            paymentId=f"payment_{uuid.uuid4().hex}",
            serviceId=service.serviceId,
            sellerAgentId=agent.agentId,
            sellerWalletAddress=agent.walletAddress,
            amountAtomic=service.priceAtomic,
            assetAddress=service.assetAddress,
            network=service.network,
            status="settled" if success else "failed",
            requestUrl=request_url,
            resultSummary=result,
            txHash=tx_hash,
            settlementReference=tx_hash,
            createdAt=now_iso(),
        )

    @staticmethod
    def hash_claim_code(claim_code: str) -> str:
        return hashlib.sha256(claim_code.encode("utf-8")).hexdigest()
