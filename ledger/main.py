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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_ASSET = "USDC"
DEFAULT_LEDGER_STATE_PATH = "ledger/data/offchain_ledger.json"

app = FastAPI(title="OntologyAgent offchain ledger")

LEDGER_CONSOLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>OntologyAgent Ledger</title>
    <style>
      :root {
        --bg: #f7f3ea;
        --panel: #fffdf8;
        --ink: #1f2933;
        --muted: #66747f;
        --accent: #0f766e;
        --border: #d9c9a8;
        --danger: #b91c1c;
      }

      * { box-sizing: border-box; }

      body {
        margin: 0;
        min-height: 100vh;
        font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
        color: var(--ink);
        background: linear-gradient(180deg, #fffaf0 0%, var(--bg) 100%);
      }

      .shell {
        max-width: 1180px;
        margin: 0 auto;
        padding: 32px 20px;
        display: grid;
        gap: 18px;
      }

      header {
        display: flex;
        justify-content: space-between;
        gap: 14px;
        align-items: end;
        flex-wrap: wrap;
      }

      h1, h2 {
        margin: 0;
      }

      h1 {
        font-size: 36px;
        line-height: 1;
      }

      h2 {
        font-size: 16px;
      }

      .hint {
        color: var(--muted);
        font-size: 13px;
      }

      .grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }

      .card {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 16px;
        display: grid;
        gap: 12px;
      }

      .summary {
        min-height: 140px;
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 13px;
        line-height: 1.5;
      }

      label {
        display: grid;
        gap: 6px;
        font-size: 12px;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      input,
      select {
        width: 100%;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 12px;
        font: inherit;
        font-size: 14px;
        color: var(--ink);
        background: rgba(255, 255, 255, 0.82);
      }

      button {
        border: 0;
        border-radius: 12px;
        padding: 11px 13px;
        font: inherit;
        cursor: pointer;
      }

      .primary {
        background: var(--accent);
        color: white;
      }

      .secondary {
        background: #eadfc7;
        color: var(--ink);
      }

      .danger {
        background: transparent;
        border: 1px solid rgba(185, 28, 28, 0.35);
        color: var(--danger);
      }

      .actions {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }

      @media (max-width: 860px) {
        .grid { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <main class="shell">
      <header>
        <div>
          <h1>OntologyAgent Ledger</h1>
          <div class="hint">Offchain balances and escrow settlement console</div>
        </div>
        <button class="secondary" id="refresh-button">Refresh</button>
      </header>

      <section class="card">
        <h2>Ledger State</h2>
        <div class="summary" id="ledger-state">Loading ledger state...</div>
      </section>

      <section class="grid">
        <form class="card" id="credit-form">
          <h2>Credit Account</h2>
          <label>
            Agent ID
            <input id="credit-agent-id" value="agent_buyer" autocomplete="off" required />
          </label>
          <label>
            Amount Atomic
            <input id="credit-amount" value="5000000" autocomplete="off" required />
          </label>
          <label>
            Reason
            <input id="credit-reason" value="demo funding" autocomplete="off" />
          </label>
          <button class="primary" type="submit">Credit</button>
        </form>

        <form class="card" id="escrow-form">
          <h2>Create Escrow</h2>
          <label>
            Buyer Agent ID
            <input id="escrow-buyer-agent-id" value="agent_buyer" autocomplete="off" required />
          </label>
          <label>
            Seller Agent ID
            <input id="escrow-seller-agent-id" value="agent_seller" autocomplete="off" required />
          </label>
          <label>
            Amount Atomic
            <input id="escrow-amount" value="3000000" autocomplete="off" required />
          </label>
          <label>
            Task ID
            <input id="escrow-task-id" value="task_demo" autocomplete="off" />
          </label>
          <label>
            Description
            <input id="escrow-description" value="Research task" autocomplete="off" />
          </label>
          <button class="primary" type="submit">Create Escrow</button>
        </form>

        <form class="card" id="settlement-form">
          <h2>Settle Escrow</h2>
          <label>
            Escrow
            <select id="escrow-select"></select>
          </label>
          <div class="actions">
            <button class="primary" type="button" id="release-button">Release</button>
            <button class="danger" type="button" id="refund-button">Refund</button>
          </div>
        </form>
      </section>
    </main>

    <script>
      const ledgerStateEl = document.getElementById("ledger-state");
      const refreshButtonEl = document.getElementById("refresh-button");
      const creditFormEl = document.getElementById("credit-form");
      const escrowFormEl = document.getElementById("escrow-form");
      const settlementFormEl = document.getElementById("settlement-form");
      const creditAgentIdEl = document.getElementById("credit-agent-id");
      const creditAmountEl = document.getElementById("credit-amount");
      const creditReasonEl = document.getElementById("credit-reason");
      const escrowBuyerAgentIdEl = document.getElementById("escrow-buyer-agent-id");
      const escrowSellerAgentIdEl = document.getElementById("escrow-seller-agent-id");
      const escrowAmountEl = document.getElementById("escrow-amount");
      const escrowTaskIdEl = document.getElementById("escrow-task-id");
      const escrowDescriptionEl = document.getElementById("escrow-description");
      const escrowSelectEl = document.getElementById("escrow-select");
      const releaseButtonEl = document.getElementById("release-button");
      const refundButtonEl = document.getElementById("refund-button");

      function selectedEscrowId() {
        return escrowSelectEl.value || escrowSelectEl.children?.[0]?.value || "";
      }

      async function requestJson(url, options = {}) {
        const response = await fetch(url, {
          headers: {
            "content-type": "application/json",
            ...(options.headers || {}),
          },
          ...options,
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(payload?.detail ?? JSON.stringify(payload) ?? `HTTP ${response.status}`);
        }
        return payload;
      }

      function renderLedgerState(state) {
        const accounts = Array.isArray(state?.accounts) ? state.accounts : [];
        const entries = Array.isArray(state?.entries) ? state.entries : [];
        const escrows = Array.isArray(state?.escrows) ? state.escrows : [];
        const latestEscrow = escrows[escrows.length - 1];
        ledgerStateEl.textContent = [
          `Accounts: ${accounts.length}`,
          `Entries: ${entries.length}`,
          `Escrows: ${escrows.length}`,
          `Latest Escrow: ${latestEscrow ? `${latestEscrow.status} ${latestEscrow.escrowId}` : "none"}`,
          "",
          JSON.stringify({ accounts, escrows }, null, 2),
        ].join("\\n");

        const previousValue = escrowSelectEl.value;
        escrowSelectEl.replaceChildren();
        if (escrows.length === 0) {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = "No escrows";
          escrowSelectEl.appendChild(option);
          return;
        }
        escrows.forEach((escrow) => {
          const option = document.createElement("option");
          option.value = escrow.escrowId;
          option.textContent = `${escrow.status} | ${escrow.escrowId} | ${escrow.amountAtomic}`;
          escrowSelectEl.appendChild(option);
        });
        escrowSelectEl.value = escrows.some((escrow) => escrow.escrowId === previousValue)
          ? previousValue
          : escrows[0].escrowId;
      }

      async function refreshLedgerState() {
        const state = await requestJson("/ledger/state", { method: "GET" });
        renderLedgerState(state);
      }

      async function creditAccount(event) {
        event.preventDefault();
        const reason = creditReasonEl.value.trim();
        await requestJson(`/ledger/accounts/${creditAgentIdEl.value.trim()}/credit`, {
          method: "POST",
          body: JSON.stringify({
            amountAtomic: creditAmountEl.value.trim(),
            reason: reason || null,
          }),
        });
        await refreshLedgerState();
      }

      async function createEscrow(event) {
        event.preventDefault();
        const taskId = escrowTaskIdEl.value.trim();
        const description = escrowDescriptionEl.value.trim();
        await requestJson("/ledger/escrows", {
          method: "POST",
          body: JSON.stringify({
            buyerAgentId: escrowBuyerAgentIdEl.value.trim(),
            sellerAgentId: escrowSellerAgentIdEl.value.trim(),
            amountAtomic: escrowAmountEl.value.trim(),
            taskId: taskId || null,
            description: description || null,
          }),
        });
        await refreshLedgerState();
      }

      function preventSettlementSubmit(event) {
        event.preventDefault();
      }

      async function settleEscrow(action) {
        const escrowId = selectedEscrowId();
        if (!escrowId) {
          return;
        }
        await requestJson(`/ledger/escrows/${escrowId}/${action}`, { method: "POST" });
        await refreshLedgerState();
      }

      refreshButtonEl.addEventListener("click", refreshLedgerState);
      creditFormEl.addEventListener("submit", creditAccount);
      escrowFormEl.addEventListener("submit", createEscrow);
      settlementFormEl.addEventListener("submit", preventSettlementSubmit);
      releaseButtonEl.addEventListener("click", () => settleEscrow("release"));
      refundButtonEl.addEventListener("click", () => settleEscrow("refund"));

      refreshLedgerState().catch((error) => {
        ledgerStateEl.textContent = error.message;
      });
    </script>
  </body>
</html>"""


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


@app.get("/", response_class=HTMLResponse)
def ledger_console() -> str:
    return LEDGER_CONSOLE_HTML


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
