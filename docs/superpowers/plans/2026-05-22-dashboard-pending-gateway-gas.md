# Dashboard Pending Gateway Gas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement real-product pending settlement, Gateway crediting, and withdrawal gas disclosure across chain, ledger, and dashboard.

**Architecture:** Ledger remains the source of truth for dashboard-visible transaction status. Chain/Circle returns Gateway withdrawal metadata; ledger records normalized transaction events and `/dashboard/data` exposes them; dashboard renders status chips, pending lines, pending top-ups, and gas/net metadata without guessing.

**Tech Stack:** Python FastAPI/Pydantic/unittest for `ledger`; TypeScript/node:test for `chain`; standalone React+Babel HTML in `ledger/web/dashboard.html`.

---

## File Map

- Modify `chain/src/domain/types.ts`
  - Add withdrawal gas/net fields to `AgentWalletGatewayWithdrawResult`.
- Modify `chain/src/services/agent-wallet-service.ts`
  - Populate estimated gas/net fields for Gateway withdrawals.
- Modify `chain/test/agent-wallet-service.test.ts`
  - Cover Gateway withdrawal gas/net metadata.
- Modify `ledger/main.py`
  - Extend `LedgerEntry.entryType`.
  - Add normalized dashboard transaction metadata.
  - Add pending settlement balance output.
  - Record pending inbound and credited entries for wallet webhooks.
  - Record submitted and terminal withdrawal events.
- Modify `ledger/tests/test_ledger_service.py`
  - Cover pending settlement, pending inbound, withdrawal submitted/withdrawn, gas/net fields, and dashboard HTML anchors.
- Modify `ledger/web/dashboard.html`
  - Add pending status UI, tooltips, funding pending top-up, Gateway explanation, gas/net disclosure, and remove user-visible `Withdrawing`.

Do not split `ledger/web/dashboard.html` in this implementation; the existing project pattern is a standalone dashboard HTML file.

---

### Task 1: Chain Gateway Withdrawal Gas/Net Metadata

**Files:**
- Modify: `chain/src/domain/types.ts`
- Modify: `chain/src/services/agent-wallet-service.ts`
- Test: `chain/test/agent-wallet-service.test.ts`

- [ ] **Step 1: Write the failing chain test**

Add these assertions to the existing `AgentWalletService withdraws USDC from Circle Gateway` test in `chain/test/agent-wallet-service.test.ts`:

```ts
      assert.equal(result.estimatedGasFeeAtomic, "3000");
      assert.equal(result.estimatedGasFee, "0.003");
      assert.equal(result.netAmountAtomic, "997000");
      assert.equal(result.netAmount, "0.997");
      assert.equal(result.transactionHash, "0xmint");
```

Use the existing test amount `"1000000"` or adjust the test setup so the expected net is `997000`.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd chain
node --import tsx --test test/agent-wallet-service.test.ts
```

Expected: FAIL because `estimatedGasFeeAtomic`, `estimatedGasFee`, `netAmountAtomic`, `netAmount`, or `transactionHash` is missing.

- [ ] **Step 3: Extend the TypeScript result type**

In `chain/src/domain/types.ts`, extend `AgentWalletGatewayWithdrawResult`:

```ts
  transactionHash: string | null;
  estimatedGasFeeAtomic: string;
  estimatedGasFee: string;
  netAmountAtomic: string;
  netAmount: string;
```

Place `transactionHash` near `mintTransactionHash`, and place gas/net fields near `amountAtomic`.

- [ ] **Step 4: Add minimal gas/net calculation**

In `chain/src/services/agent-wallet-service.ts`, inside `withdrawFromGateway`, add a local constant after `amountAtomic` is parsed:

```ts
    const estimatedGasFeeAtomic = 3000n;
    const netAmountAtomic = amountAtomic > estimatedGasFeeAtomic
      ? amountAtomic - estimatedGasFeeAtomic
      : 0n;
```

In the returned object, add:

```ts
      transactionHash: mintTransaction?.txHash ?? null,
      estimatedGasFeeAtomic: estimatedGasFeeAtomic.toString(),
      estimatedGasFee: atomicUsdcToDecimal(estimatedGasFeeAtomic.toString()),
      netAmountAtomic: netAmountAtomic.toString(),
      netAmount: atomicUsdcToDecimal(netAmountAtomic.toString()),
```

- [ ] **Step 5: Run chain checks**

Run:

```bash
cd chain
npm run typecheck
npm test
```

Expected: typecheck passes; all chain tests pass with two integration tests skipped.

- [ ] **Step 6: Commit**

```bash
git add chain/src/domain/types.ts chain/src/services/agent-wallet-service.ts chain/test/agent-wallet-service.test.ts
git commit -m "feat: expose gateway withdrawal gas metadata"
```

---

### Task 2: Ledger Status Normalization and Dashboard Transaction Fields

**Files:**
- Modify: `ledger/main.py`
- Test: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing dashboard transaction normalization tests**

Add tests near the existing dashboard data tests in `ledger/tests/test_ledger_service.py`:

```python
    def test_dashboard_transaction_exposes_pending_settlement_and_gas_metadata(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="receiver",
            agent_name="Receiver Agent",
            email="receiver@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-receiver",
        )
        store.credit(
            agent_id="receiver",
            amount_atomic="0",
            reason="nanopayment pending",
            metadata={
                "dashboardStatus": "pending_settle",
                "amountAtomic": "1000",
                "counterpartyEmail": "payer@example.com",
            },
        )
        store.credit(
            agent_id="receiver",
            amount_atomic="0",
            reason="withdrawal submitted",
            metadata={
                "dashboardStatus": "withdraw_submitted",
                "amountAtomic": "1000000",
                "gasFeeAtomic": "3000",
                "netAmountAtomic": "997000",
                "destinationAddress": "0x2222222222222222222222222222222222222222",
                "network": "Base",
                "txHash": "0xsubmitted",
            },
        )

        state = main.build_dashboard_data(
            main.get_store().load().model_dump(),
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["balance"]["pendingSettlement"], 0.001)
        statuses = [tx["status"] for tx in data["transactions"]]
        self.assertIn("pending_settle", statuses)
        self.assertIn("withdraw_submitted", statuses)
        submitted = next(tx for tx in data["transactions"] if tx["status"] == "withdraw_submitted")
        self.assertEqual(submitted["gasFeeAtomic"], "3000")
        self.assertEqual(submitted["netAmountAtomic"], "997000")
        self.assertEqual(submitted["txHash"], "0xsubmitted")
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_transaction_exposes_pending_settlement_and_gas_metadata
```

Expected: FAIL because pending settlement and gas/net fields are not exposed.

- [ ] **Step 3: Allow zero-delta event entries**

In `ledger/main.py`, extend `LedgerEntry.entryType` to include:

```python
        "pending_settlement",
        "pending_inbound",
        "withdrawal_submitted",
```

Add this helper near `parse_positive_atomic`:

```python
def parse_dashboard_amount_atomic(entry: dict[str, Any], fallback: Decimal) -> Decimal:
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        explicit = metadata.get("amountAtomic")
        if explicit is not None:
            return abs(atomic_decimal(explicit))
    return fallback
```

- [ ] **Step 4: Normalize dashboard status and metadata**

In `dashboard_transaction`, after `entry_type` and deltas are computed, add:

```python
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    dashboard_status = metadata.get("dashboardStatus")
```

Replace the `amount_atomic` assignment with:

```python
    base_amount_atomic = (
        atomic_decimal(escrow.get("amountAtomic"))
        if escrow
        else max(abs(available_delta), abs(locked_delta))
    )
    amount_atomic = parse_dashboard_amount_atomic(entry, base_amount_atomic)
```

After the existing status mapping, add:

```python
    if isinstance(dashboard_status, str) and dashboard_status.strip():
        status = dashboard_status.strip()
    elif entry_type == "pending_settlement":
        status = "pending_settle"
    elif entry_type == "pending_inbound":
        status = "pending_inbound_chain"
    elif entry_type == "withdrawal_submitted":
        status = "withdraw_submitted"
    elif entry_type == "withdrawal":
        status = metadata.get("dashboardStatus", "withdrawn")
```

Before returning, build `transaction` and append metadata fields:

```python
    transaction = {
        "id": entry.get("entryId") or entry.get("escrowId") or "ledger_entry",
        "counterparty": dashboard_counterparty(entry, escrow_by_id),
        "amount": atomic_to_usdc(amount_atomic),
        "amountAtomic": str(int(amount_atomic)),
        "direction": direction,
        "role": role,
        "status": status,
        "timestamp": entry.get("createdAt") or "ledger",
    }
    for key in (
        "destinationAddress",
        "network",
        "txHash",
        "gasFeeAtomic",
        "gasFee",
        "netAmountAtomic",
        "netAmount",
        "failureReason",
        "gatewayStage",
        "linkedEntryId",
    ):
        value = metadata.get(key)
        if value is not None:
            transaction[key] = value
    return transaction
```

- [ ] **Step 5: Expose pending settlement balance**

In `build_dashboard_data`, add this helper loop before constructing `agents[agent_id]`:

```python
        pending_settlement_atomic = sum(
            parse_dashboard_amount_atomic(
                entry,
                max(
                    abs(atomic_decimal(entry.get("availableDeltaAtomic"))),
                    abs(atomic_decimal(entry.get("lockedDeltaAtomic"))),
                ),
            )
            for entry in agent_entries
            if dashboard_transaction(entry, escrow_by_id)["status"] == "pending_settle"
        )
```

Add to the balance dict:

```python
                "pendingSettlement": atomic_to_usdc(pending_settlement_atomic),
                "pendingSettlementAtomic": str(int(pending_settlement_atomic)),
```

- [ ] **Step 6: Run focused ledger test**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_transaction_exposes_pending_settlement_and_gas_metadata
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ledger/main.py ledger/tests/test_ledger_service.py
git commit -m "feat: normalize dashboard pending transaction states"
```

---

### Task 3: Ledger Event Recording for Inbound Deposits, A2A Pending, and Withdrawals

**Files:**
- Modify: `ledger/main.py`
- Test: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing ledger lifecycle tests**

Add these tests in `ledger/tests/test_ledger_service.py`:

```python
    def test_wallet_webhook_records_pending_inbound_before_gateway_credit(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, request):
                return {
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                    "depositTransactionId": "deposit-tx",
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_1",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-1",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        entries = main.get_store().load().entries
        self.assertTrue(any(entry.metadata.get("dashboardStatus") == "pending_inbound_chain" for entry in entries))
        self.assertTrue(any(entry.metadata.get("dashboardStatus") == "credited" for entry in entries))
```

```python
    def test_withdrawal_records_submitted_and_withdrawn_entries(self) -> None:
        class FakeSettlementClient:
            async def submit_withdrawal(self, **kwargs):
                return main.LedgerSettlementRecord(
                    recordId="settle_withdrawal",
                    eventType="withdrawal",
                    settlementHttpUrl="http://settlement.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAddress=kwargs["to_address"],
                    asset="USDC",
                    amountAtomic=kwargs["amount_atomic"],
                    status="submitted",
                    transactionHash="0xwithdrawal",
                    actionResult={
                        "transactionHash": "0xwithdrawal",
                        "estimatedGasFeeAtomic": "3000",
                        "estimatedGasFee": "0.003",
                        "netAmountAtomic": "997000",
                        "netAmount": "0.997",
                    },
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_withdraw",
            agent_name="Withdraw Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-withdraw",
        )
        store.credit(agent_id="agent_withdraw", amount_atomic="2000000", reason="seed", metadata={})

        with patch.object(main, "get_ledger_settlement_client", return_value=FakeSettlementClient()):
            response = self.client.post(
                "/ledger/withdrawals",
                json={
                    "agentId": "agent_withdraw",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                    "reason": "dashboard withdrawal",
                    "metadata": {"source": "dashboard"},
                },
            )

        self.assertEqual(response.status_code, 200)
        statuses = [entry["metadata"].get("dashboardStatus") for entry in response.json()["entries"]]
        self.assertEqual(statuses, ["withdraw_submitted", "withdrawn"])
```

- [ ] **Step 2: Run focused tests and verify failures**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest \
  tests.test_ledger_service.LedgerServiceTests.test_wallet_webhook_records_pending_inbound_before_gateway_credit \
  tests.test_ledger_service.LedgerServiceTests.test_withdrawal_records_submitted_and_withdrawn_entries
```

Expected: FAIL because the event records are not created yet and withdrawal response has a single `entry`.

- [ ] **Step 3: Add generic event entry helper**

Add this method to `OffchainLedgerStore` after `credit`:

```python
    def record_event_entry(
        self,
        *,
        entry_type: Literal[
            "credit",
            "escrow_lock",
            "escrow_release",
            "escrow_refund",
            "agent_transfer",
            "withdrawal",
            "pending_inbound",
            "pending_settlement",
            "withdrawal_submitted",
        ],
        agent_id: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        available_delta: int = 0,
    ) -> LedgerEntry:
        def mutate(state: LedgerState) -> LedgerEntry:
            self._account_for_update(state, agent_id, create=True)
            entry = self._entry(
                entry_type=entry_type,
                agent_id=agent_id,
                available_delta=available_delta,
                reason=reason,
                metadata=metadata,
            )
            state.entries.append(entry)
            return entry

        return self._mutate(mutate)
```

This relies on the `LedgerEntry.entryType` and `_entry.entry_type` Literal extensions from Task 2, so implement Task 2 before adding this helper.

- [ ] **Step 4: Record inbound pending and credited entries**

In `process_circle_wallet_webhook`, immediately after `received = get_store().save_circle_webhook_event(...)`, add:

```python
    pending_entry = get_store().record_event_entry(
        entry_type="pending_inbound",
        agent_id=account.agentId,
        reason="external top-up detected",
        metadata={
            "dashboardStatus": "pending_inbound_chain",
            "amountAtomic": amount_atomic,
            "counterparty": "External wallet",
            "gatewayStage": "gateway_crediting",
            "txHash": transaction_id,
            "network": "Base",
        },
    )
```

After `processed = get_store().save_circle_webhook_event(...)`, add:

```python
    credited_account, credited_entry = get_store().credit(
        agent_id=account.agentId,
        amount_atomic=amount_atomic,
        reason="Gateway Wallet credited",
        metadata={
            "dashboardStatus": "credited",
            "amountAtomic": amount_atomic,
            "counterparty": "External wallet",
            "linkedEntryId": pending_entry.entryId,
            "txHash": transaction_id,
            "network": "Base",
        },
    )
```

Extend the response dict with:

```python
        "pendingEntry": pending_entry.model_dump(),
        "creditedEntry": credited_entry.model_dump(),
        "account": credited_account.model_dump(),
```

- [ ] **Step 5: Record withdrawal submitted and withdrawn entries**

Add this method to `OffchainLedgerStore` near `withdraw`:

```python
    def withdrawal_submitted(
        self,
        *,
        agent_id: str,
        destination_address: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        withdrawal_id: str,
    ) -> LedgerEntry:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)
        return self.record_event_entry(
            entry_type="withdrawal_submitted",
            agent_id=agent_id,
            reason=reason or "withdrawal submitted",
            metadata={
                **metadata,
                "dashboardStatus": "withdraw_submitted",
                "amountAtomic": str(amount),
                "withdrawalId": withdrawal_id,
                "destinationAddress": destination,
                "counterparty": f"External · {short_address(destination)}",
                "network": "Base",
            },
        )
```

In `/ledger/withdrawals`, after `validate_withdrawal(...)` and before `settle_withdrawal(...)`, call:

```python
        submitted_entry = get_store().withdrawal_submitted(
            agent_id=request.agentId,
            destination_address=request.destinationAddress,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=request.metadata,
            withdrawal_id=withdrawal_id,
        )
```

After `settlement_record = await settle_withdrawal(...)`, extract action metadata:

```python
        action_result = settlement_record.actionResult if isinstance(settlement_record.actionResult, dict) else {}
        withdrawal_metadata = {
            **request.metadata,
            "dashboardStatus": "withdrawn",
            "linkedEntryId": submitted_entry.entryId,
            "txHash": settlement_record.transactionHash or action_result.get("transactionHash"),
            "gasFeeAtomic": action_result.get("estimatedGasFeeAtomic"),
            "gasFee": action_result.get("estimatedGasFee"),
            "netAmountAtomic": action_result.get("netAmountAtomic"),
            "netAmount": action_result.get("netAmount"),
            "network": "Base",
        }
```

Pass `withdrawal_metadata` to `get_store().withdraw(...)`.

Return both entries:

```python
        "entries": [submitted_entry.model_dump(), entry.model_dump()],
```

Keep `"entry": entry.model_dump()` for backwards compatibility.

- [ ] **Step 6: Preserve failed submitted withdrawal records**

Wrap settlement in its own `try/except LedgerSettlementError` after `submitted_entry` exists:

```python
        try:
            settlement_record = await settle_withdrawal(...)
        except LedgerSettlementError as error:
            failed_entry = get_store().record_event_entry(
                entry_type="withdrawal_submitted",
                agent_id=request.agentId,
                reason="withdrawal failed",
                metadata={
                    **request.metadata,
                    "dashboardStatus": "failed",
                    "amountAtomic": request.amountAtomic,
                    "withdrawalId": withdrawal_id,
                    "linkedEntryId": submitted_entry.entryId,
                    "failureReason": error.record.error,
                    "destinationAddress": request.destinationAddress,
                    "network": "Base",
                },
            )
            get_store().add_settlement_record(error.record)
            raise http_error(error) from error
```

Ensure the failed entry is recorded, but do not debit available balance.

- [ ] **Step 7: Run focused ledger lifecycle tests**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest \
  tests.test_ledger_service.LedgerServiceTests.test_wallet_webhook_records_pending_inbound_before_gateway_credit \
  tests.test_ledger_service.LedgerServiceTests.test_withdrawal_records_submitted_and_withdrawn_entries
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ledger/main.py ledger/tests/test_ledger_service.py
git commit -m "feat: record pending gateway ledger events"
```

---

### Task 4: Dashboard Status Components and Transaction Row Metadata

**Files:**
- Modify: `ledger/web/dashboard.html`
- Test: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing dashboard source test**

In `test_dashboard_serves_user_dashboard_page`, add:

```python
        self.assertIn("pending_settle", html)
        self.assertIn("pending_inbound_chain", html)
        self.assertIn("withdraw_submitted", html)
        self.assertIn("Gas", html)
        self.assertIn("Net", html)
        self.assertIn("Gateway Wallet", html)
        self.assertNotIn("WITHDRAWING", html)
        self.assertNotIn("提现中", html)
```

- [ ] **Step 2: Run dashboard source test and verify failure**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_serves_user_dashboard_page
```

Expected: FAIL on the new source anchors.

- [ ] **Step 3: Add status map entries**

In `ledger/web/dashboard.html`, inside `TransactionRow` `statusMap`, add:

```js
    pending_settle:       { label: 'SETTLING',         color: 'var(--accent-amber)',    bg: 'transparent' },
    pending_inbound_chain:{ label: 'CREDITING',        color: 'var(--accent-amber)',    bg: 'transparent' },
    withdraw_submitted:   { label: 'SUBMITTED',        color: 'var(--ink-secondary)',   bg: 'transparent' },
    credited:             { label: 'CREDITED',         color: 'var(--status-positive)', bg: 'transparent' },
    withdrawn:            { label: 'WITHDRAWN',        color: 'var(--status-positive)', bg: 'transparent' },
    failed:               { label: 'FAILED',           color: 'var(--status-negative)', bg: 'transparent' },
```

- [ ] **Step 4: Extend TransactionRow props**

Update the `TransactionRow` signature:

```js
  gasFee,
  gasFeeAtomic,
  netAmount,
  netAmountAtomic,
  txHash,
  network,
```

Add helpers inside `TransactionRow`:

```js
  const atomicToUsdc = (value) => {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) ? parsed / 1000000 : 0;
  };
  const gasDisplay = gasFee !== undefined
    ? gasFee
    : gasFeeAtomic !== undefined
      ? window.formatAmount(atomicToUsdc(gasFeeAtomic))
      : null;
  const netDisplay = netAmount !== undefined
    ? netAmount
    : netAmountAtomic !== undefined
      ? window.formatAmount(atomicToUsdc(netAmountAtomic))
      : null;
```

In the bottom line, render:

```jsx
        {role === 'withdrawal' && (gasDisplay || netDisplay) && (
          <span className="font-mono text-[11px]" style={{ color: 'var(--ink-tertiary)' }}>
            {gasDisplay ? `Gas ~$${gasDisplay}` : ''}
            {gasDisplay && netDisplay ? ' · ' : ''}
            {netDisplay ? `Net $${netDisplay}` : ''}
            {(gasDisplay || netDisplay) && network ? ` · ${network}` : ''}
          </span>
        )}
        {txHash && (
          <span className="font-mono text-[11px]" style={{ color: 'var(--ink-tertiary)' }}>
            {txHash}
          </span>
        )}
```

- [ ] **Step 5: Pass metadata from Overview and Transactions**

Where `TransactionRow` is rendered in Overview and Transactions, pass:

```jsx
                role={tx.role}
                gasFee={tx.gasFee}
                gasFeeAtomic={tx.gasFeeAtomic}
                netAmount={tx.netAmount}
                netAmountAtomic={tx.netAmountAtomic}
                txHash={tx.txHash}
                network={tx.network}
```

- [ ] **Step 6: Run dashboard source test**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_serves_user_dashboard_page
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ledger/web/dashboard.html ledger/tests/test_ledger_service.py
git commit -m "feat: render dashboard pending status chips"
```

---

### Task 5: Dashboard Pending Balance Lines and Funding Pending Top-Ups

**Files:**
- Modify: `ledger/web/dashboard.html`
- Test: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Add failing source assertions**

In `test_dashboard_serves_user_dashboard_page`, add:

```python
        self.assertIn("PendingBalanceLine", html)
        self.assertIn("PendingDepositCard", html)
        self.assertIn("PENDING TOP-UP", html)
        self.assertIn("Base confirming", html)
        self.assertIn("Crediting Gateway Wallet", html)
```

- [ ] **Step 2: Run dashboard source test and verify failure**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_serves_user_dashboard_page
```

Expected: FAIL on missing component/source anchors.

- [ ] **Step 3: Add PendingBalanceLine**

Before `AgentCard`, add:

```jsx
function PendingBalanceLine({ amount = 0, label = 'Settling', size = 'lg' }) {
  if (!amount || amount <= 0) return null;
  return (
    <div
      className="font-mono"
      style={{
        marginTop: size === 'sm' ? '8px' : '12px',
        fontSize: size === 'sm' ? '11px' : '13px',
        color: 'var(--accent-amber)',
        letterSpacing: '0.04em',
      }}
    >
      +${window.formatAmount(amount)} {label}
    </div>
  );
}

window.PendingBalanceLine = PendingBalanceLine;
```

- [ ] **Step 4: Render pending in AgentCard and Overview**

Extend `AgentCard` props:

```js
  pendingSettlement = 0,
```

Render below the balance:

```jsx
      <window.PendingBalanceLine
        amount={pendingSettlement}
        label="Settling"
        size={isMini ? 'sm' : 'lg'}
      />
```

In `MvpOverviewView`, below the main balance number, render:

```jsx
        <window.PendingBalanceLine
          amount={balance.pendingSettlement || 0}
          label="Settling"
        />
```

Where portfolio cards render `AgentCard`, pass:

```jsx
              pendingSettlement={meta.balance?.pendingSettlement || 0}
```

- [ ] **Step 5: Add PendingDepositCard**

Near `MvpFundingView`, add:

```jsx
  function PendingDepositCard({ tx, t }) {
    return (
      <Card>
        <div className="smallcaps-mono" style={{ color: 'var(--accent-amber)', marginBottom: '10px' }}>
          PENDING TOP-UP
        </div>
        <div className="font-body" style={{ color: 'var(--ink-primary)', fontSize: '13px', marginBottom: '12px' }}>
          +${window.formatAmount(tx.amount)} USDC · {tx.counterparty}
        </div>
        <ol className="font-body" style={{ margin: 0, paddingLeft: '18px', color: 'var(--ink-secondary)', fontSize: '12px', lineHeight: 1.8 }}>
          <li>Exchange withdrawn</li>
          <li>Base confirming</li>
          <li>Crediting Gateway Wallet</li>
        </ol>
      </Card>
    );
  }
```

In `MvpFundingView`, compute:

```js
    const pendingDeposits = localTxs.filter((tx) => tx.status === 'pending_inbound_chain');
```

Render before Add Funds:

```jsx
        {pendingDeposits.length > 0 && (
          <Section title="PENDING TOP-UP">
            {pendingDeposits.map((tx) => (
              <PendingDepositCard key={tx.id} tx={tx} t={t} />
            ))}
          </Section>
        )}
```

- [ ] **Step 6: Add Gateway explanation and ETA**

In Funding Add Funds receive eyebrow, change the label area to:

```jsx
                <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '8px' }}>
                  {t('mvp.dash.funding.receive_eyebrow')} · Gateway Wallet
                </div>
```

Below the receive card, add:

```jsx
          <div className="font-body" style={{ marginTop: '14px', fontSize: '12px', color: 'var(--ink-secondary)', lineHeight: 1.55 }}>
            Deposits may take a few minutes while Base confirms and Chief credits the Gateway Wallet.
          </div>
```

- [ ] **Step 7: Run dashboard source test**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_serves_user_dashboard_page
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ledger/web/dashboard.html ledger/tests/test_ledger_service.py
git commit -m "feat: show pending gateway funding states"
```

---

### Task 6: Funding Withdraw Gas/Net Form Disclosure

**Files:**
- Modify: `ledger/web/dashboard.html`
- Test: `ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Add failing source assertions**

In `test_dashboard_serves_user_dashboard_page`, add:

```python
        self.assertIn("Net to destination", html)
        self.assertIn("Network fee", html)
        self.assertIn("~$0.003", html)
        self.assertIn("Submitted", html)
```

- [ ] **Step 2: Run source test and verify failure**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_serves_user_dashboard_page
```

Expected: FAIL until the form copy exists.

- [ ] **Step 3: Add withdraw constants and net calculation**

Inside `MvpFundingView`, after `const MIN_WITHDRAW = 1;`, add:

```js
    const WITHDRAW_GAS_USDC = 0.003;
```

After `amountNum`, add:

```js
    const netAmount = amountValid ? Math.max(0, amountNum - WITHDRAW_GAS_USDC) : null;
```

- [ ] **Step 4: Update withdraw metadata strip**

Replace the existing fee row:

```jsx
              <TermLabel>{t('mvp.dash.funding.term_fee')}</TermLabel>
              <TermValue accent>{t('mvp.dash.funding.fee_covered')}</TermValue>
```

with:

```jsx
              <TermLabel>Network fee</TermLabel>
              <TermValue>~$0.003 USDC</TermValue>
              {netAmount !== null && (
                <>
                  <TermLabel>Net to destination</TermLabel>
                  <TermValue accent>${window.formatAmount(netAmount)} USDC</TermValue>
                </>
              )}
              <TermLabel>ETA</TermLabel>
              <TermValue>Usually under a minute after submission</TermValue>
```

- [ ] **Step 5: Keep submitted row visible after withdrawal response**

In `handleConfirm`, after successful payload, prefer `payload.entries`:

```js
        const rows = Array.isArray(payload.entries) ? payload.entries : [payload.entry].filter(Boolean);
        const nextTxs = rows.map((row) => {
          const meta = row.metadata || {};
          return {
            id: row.entryId || meta.withdrawalId || `wd_${Date.now().toString(36).slice(-5)}`,
            counterparty: meta.counterparty || `External · ${dest.slice(0, 6)}…${dest.slice(-4)}`,
            amount: Math.abs(atomicToUsdc(row.availableDeltaAtomic || meta.amountAtomic || amountAtomic)) || amountNum,
            direction: 'out',
            role: 'withdrawal',
            status: meta.dashboardStatus || 'withdrawn',
            timestamp: t('mvp.ui.just_now'),
            gasFee: meta.gasFee,
            gasFeeAtomic: meta.gasFeeAtomic,
            netAmount: meta.netAmount,
            netAmountAtomic: meta.netAmountAtomic,
            txHash: meta.txHash,
            network: meta.network || 'Base',
          };
        });
        setLocalTxs((prev) => [...nextTxs, ...prev]);
```

Remove the old single local transaction construction block.

- [ ] **Step 6: Run dashboard source test**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest tests.test_ledger_service.LedgerServiceTests.test_dashboard_serves_user_dashboard_page
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ledger/web/dashboard.html ledger/tests/test_ledger_service.py
git commit -m "feat: disclose withdrawal gas in dashboard"
```

---

### Task 7: Full Regression and Online-Ready Verification

**Files:**
- Test-only task.

- [ ] **Step 1: Run ledger tests**

Run:

```bash
cd ledger
/Users/freedom/cc/OntologyAgent/.codex-tmp/ledger-venv/bin/python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 2: Run chain typecheck and tests**

Run:

```bash
cd chain
npm run typecheck
npm test
```

Expected: typecheck passes; node tests pass with known skipped integration tests.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Inspect dashboard source anchors**

Run:

```bash
rg -n "pending_settle|pending_inbound_chain|withdraw_submitted|PENDING TOP-UP|Gateway Wallet|Network fee|Net to destination|WITHDRAWING|提现中" ledger/web/dashboard.html
```

Expected: output includes matches for `pending_settle`, `pending_inbound_chain`, `withdraw_submitted`, `PENDING TOP-UP`, `Gateway Wallet`, `Network fee`, and `Net to destination`; output contains no matches for `WITHDRAWING` or `提现中`.

- [ ] **Step 5: Confirm no uncommitted changes remain**

Run:

```bash
git status --short
```

Expected: no output.
