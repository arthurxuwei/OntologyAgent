from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_ASSET = "USDC"
DEFAULT_LEDGER_STATE_PATH = "ledger/data/offchain_ledger.json"

app = FastAPI(title="OntologyAgent offchain ledger")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    asset: str = DEFAULT_ASSET
    availableAtomic: str = "0"
    lockedAtomic: str = "0"
    createdAt: str
    updatedAt: str


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entryId: str
    entryType: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"]
    agentId: str
    asset: str = DEFAULT_ASSET
    availableDeltaAtomic: str = "0"
    lockedDeltaAtomic: str = "0"
    escrowId: Optional[str] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class EscrowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    escrowId: str
    buyerAgentId: str
    sellerAgentId: str
    amountAtomic: str
    asset: str = DEFAULT_ASSET
    status: Literal["locked", "released", "refunded"]
    taskId: Optional[str] = None
    description: Optional[str] = None
    createdAt: str
    updatedAt: str
    releasedAt: Optional[str] = None
    refundedAt: Optional[str] = None


class LedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: list[LedgerAccount] = Field(default_factory=list)
    entries: list[LedgerEntry] = Field(default_factory=list)
    escrows: list[EscrowRecord] = Field(default_factory=list)


class CreditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amountAtomic: str
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateEscrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    buyerAgentId: str
    sellerAgentId: str
    amountAtomic: str
    taskId: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def parse_positive_atomic(value: str) -> int:
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("amountAtomic must be a positive integer string")
    return int(value)


def add_atomic(left: str, delta: int) -> str:
    result = int(left) + delta
    if result < 0:
        raise ValueError("ledger balance cannot become negative")
    return str(result)


class OffchainLedgerStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> LedgerState:
        with self._lock:
            return self._load_unlocked()

    def credit(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerEntry]:
            account, account_index = self._account_for_update(state, agent_id, create=True)
            current = now_iso()
            updated = account.model_copy(
                update={
                    "availableAtomic": add_atomic(account.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            state.accounts[account_index] = updated
            entry = self._entry(
                entry_type="credit",
                agent_id=agent_id,
                available_delta=amount,
                reason=reason,
                metadata=metadata,
            )
            state.entries.append(entry)
            return updated, entry

        return self._mutate(mutate)

    def create_escrow(
        self,
        *,
        buyer_agent_id: str,
        seller_agent_id: str,
        amount_atomic: str,
        task_id: Optional[str],
        description: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[EscrowRecord, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[EscrowRecord, LedgerEntry]:
            buyer, buyer_index = self._account_for_update(
                state, buyer_agent_id, create=False
            )
            if int(buyer.availableAtomic) < amount:
                raise ValueError("insufficient available balance")

            current = now_iso()
            updated_buyer = buyer.model_copy(
                update={
                    "availableAtomic": add_atomic(buyer.availableAtomic, -amount),
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, amount),
                    "updatedAt": current,
                }
            )
            escrow = EscrowRecord(
                escrowId=f"escrow_{uuid.uuid4().hex}",
                buyerAgentId=buyer_agent_id,
                sellerAgentId=seller_agent_id,
                amountAtomic=str(amount),
                status="locked",
                taskId=task_id,
                description=description,
                createdAt=current,
                updatedAt=current,
            )
            entry = self._entry(
                entry_type="escrow_lock",
                agent_id=buyer_agent_id,
                available_delta=-amount,
                locked_delta=amount,
                escrow_id=escrow.escrowId,
                reason="escrow created",
                metadata=metadata,
            )
            state.accounts[buyer_index] = updated_buyer
            state.escrows.append(escrow)
            state.entries.append(entry)
            return escrow, entry

        return self._mutate(mutate)

    def release_escrow(self, escrow_id: str) -> EscrowRecord:
        def mutate(state: LedgerState) -> EscrowRecord:
            escrow, escrow_index = self._escrow_for_update(state, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer, buyer_index = self._account_for_update(
                state, escrow.buyerAgentId, create=False
            )
            seller, seller_index = self._account_for_update(
                state, escrow.sellerAgentId, create=True
            )
            current = now_iso()
            state.accounts[buyer_index] = buyer.model_copy(
                update={
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                    "updatedAt": current,
                }
            )
            state.accounts[seller_index] = seller.model_copy(
                update={
                    "availableAtomic": add_atomic(seller.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "released",
                    "releasedAt": current,
                    "updatedAt": current,
                }
            )
            state.escrows[escrow_index] = updated_escrow
            state.entries.append(
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.buyerAgentId,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                )
            )
            state.entries.append(
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.sellerAgentId,
                    available_delta=amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                )
            )
            return updated_escrow

        return self._mutate(mutate)

    def refund_escrow(self, escrow_id: str) -> EscrowRecord:
        def mutate(state: LedgerState) -> EscrowRecord:
            escrow, escrow_index = self._escrow_for_update(state, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer, buyer_index = self._account_for_update(
                state, escrow.buyerAgentId, create=False
            )
            current = now_iso()
            state.accounts[buyer_index] = buyer.model_copy(
                update={
                    "availableAtomic": add_atomic(buyer.availableAtomic, amount),
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                    "updatedAt": current,
                }
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "refunded",
                    "refundedAt": current,
                    "updatedAt": current,
                }
            )
            state.escrows[escrow_index] = updated_escrow
            state.entries.append(
                self._entry(
                    entry_type="escrow_refund",
                    agent_id=escrow.buyerAgentId,
                    available_delta=amount,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow refunded",
                )
            )
            return updated_escrow

        return self._mutate(mutate)

    def _load_unlocked(self) -> LedgerState:
        if not os.path.exists(self.path):
            return LedgerState()
        with open(self.path, encoding="utf-8") as handle:
            return LedgerState.model_validate(json.load(handle))

    def _save_unlocked(self, state: LedgerState) -> None:
        parent_dir = os.path.dirname(self.path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        target_dir = parent_dir or "."
        fd, temp_path = tempfile.mkstemp(
            prefix=".offchain-ledger-",
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

    def _mutate(self, mutator):
        with self._lock:
            state = self._load_unlocked()
            result = mutator(state)
            self._save_unlocked(state)
            return result

    def _account_for_update(
        self, state: LedgerState, agent_id: str, *, create: bool
    ) -> tuple[LedgerAccount, int]:
        for index, account in enumerate(state.accounts):
            if account.agentId == agent_id and account.asset == DEFAULT_ASSET:
                return account, index
        if not create:
            raise ValueError("account not found")
        account = LedgerAccount(
            agentId=agent_id,
            createdAt=now_iso(),
            updatedAt=now_iso(),
        )
        state.accounts.append(account)
        return account, len(state.accounts) - 1

    def _escrow_for_update(
        self, state: LedgerState, escrow_id: str
    ) -> tuple[EscrowRecord, int]:
        for index, escrow in enumerate(state.escrows):
            if escrow.escrowId == escrow_id:
                return escrow, index
        raise LookupError("escrow not found")

    @staticmethod
    def _require_locked(escrow: EscrowRecord) -> None:
        if escrow.status != "locked":
            raise ValueError("escrow is not locked")

    @staticmethod
    def _entry(
        *,
        entry_type: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"],
        agent_id: str,
        available_delta: int = 0,
        locked_delta: int = 0,
        escrow_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LedgerEntry:
        return LedgerEntry(
            entryId=f"entry_{uuid.uuid4().hex}",
            entryType=entry_type,
            agentId=agent_id,
            availableDeltaAtomic=str(available_delta),
            lockedDeltaAtomic=str(locked_delta),
            escrowId=escrow_id,
            reason=reason,
            metadata=metadata or {},
            createdAt=now_iso(),
        )


@lru_cache(maxsize=1)
def get_store() -> OffchainLedgerStore:
    return OffchainLedgerStore(
        os.getenv("LEDGER_STATE_PATH", DEFAULT_LEDGER_STATE_PATH)
    )


def http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "OntologyAgent-ledger", "status": "ok"}


@app.get("/ledger/state")
def get_ledger_state() -> dict[str, Any]:
    return get_store().load().model_dump()


@app.post("/ledger/accounts/{agent_id}/credit")
def credit_agent_balance(agent_id: str, request: CreditRequest) -> dict[str, Any]:
    try:
        account, entry = get_store().credit(
            agent_id=agent_id,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=request.metadata,
        )
    except (LookupError, ValueError) as error:
        raise http_error(error) from error
    return {"account": account.model_dump(), "entry": entry.model_dump()}


@app.post("/ledger/escrows")
def create_escrow(request: CreateEscrowRequest) -> dict[str, Any]:
    try:
        escrow, entry = get_store().create_escrow(
            buyer_agent_id=request.buyerAgentId,
            seller_agent_id=request.sellerAgentId,
            amount_atomic=request.amountAtomic,
            task_id=request.taskId,
            description=request.description,
            metadata=request.metadata,
        )
    except (LookupError, ValueError) as error:
        raise http_error(error) from error
    return {"escrow": escrow.model_dump(), "entry": entry.model_dump()}


@app.post("/ledger/escrows/{escrow_id}/release")
def release_escrow(escrow_id: str) -> dict[str, Any]:
    try:
        escrow = get_store().release_escrow(escrow_id)
    except (LookupError, ValueError) as error:
        raise http_error(error) from error
    return {"escrow": escrow.model_dump()}


@app.post("/ledger/escrows/{escrow_id}/refund")
def refund_escrow(escrow_id: str) -> dict[str, Any]:
    try:
        escrow = get_store().refund_escrow(escrow_id)
    except (LookupError, ValueError) as error:
        raise http_error(error) from error
    return {"escrow": escrow.model_dump()}
