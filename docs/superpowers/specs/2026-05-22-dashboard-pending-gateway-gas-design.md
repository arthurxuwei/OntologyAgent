# Dashboard Pending Settlement, Gateway Crediting, and Gas Disclosure Design

## Context

The Chief ledger dashboard now uses real GitHub auth, real claim codes, real
dashboard data, real Gateway balances, and real withdrawals. Product feedback
from the latest prototype asks the dashboard to explain time gaps that are
natural to the payment rail:

- Nanopayments can batch, so the receiver may have a short "received but not
  spendable" window.
- External deposits can be detected before Chief finishes crediting the Gateway
  Wallet-backed ledger balance.
- Withdrawals require gas and must disclose both the fee and the net amount.

The product requirement is to implement this as a complete real-product change,
not a front-end-only approximation.

## Goals

- Show a receiver-only pending settlement state for nanopayments batch
  processing.
- Improve the funding UX for external top-ups by making the Base confirmation
  and Gateway Wallet crediting process explicit.
- Remove user-facing "withdrawing" language while still recording every
  withdrawal event.
- Disclose withdrawal gas in the form and in transaction rows.
- Keep all transaction records visible in the Transactions tab.
- Keep dashboard rendering driven by normalized ledger/chain fields rather than
  front-end guesses.

## Non-Goals

- Do not add demo-only settlement controls to the production dashboard.
- Do not hide submitted or failed withdrawal records.
- Do not change the 1 USDC minimum withdrawal requirement.
- Do not replace Coinbase onramp production integration work; the funding screen
  can continue to show a soft "coming soon" line for card/bank onramp.

## Status Model

The normalized dashboard transaction status set will include:

- `pending_settle`: receiver-side pending settlement for nanopayments batch
  processing. It is visible only to the receiving agent/user and does not count
  toward available balance.
- `pending_inbound_chain`: external top-up has been sent or detected but has not
  completed Gateway Wallet crediting. It does not count toward available
  balance.
- `credited`: external top-up is credited and spendable.
- `withdraw_submitted`: withdrawal request has been recorded/submitted. The
  user-facing label is `Submitted` / `已提交`.
- `withdrawn`: withdrawal completed. The user-facing label is `Withdrawn` /
  `已提现`.
- `failed`: terminal failure with a reason when available.

User-facing labels must not include `Withdrawing` / `提现中`.

## Data Flow

### Nanopayments Receiver Pending Settlement

When a nanopayment is accepted and the receiver is waiting for batch settlement,
ledger records a receiver-side transaction/entry with status `pending_settle`.
This amount is exposed separately from available balance as pending settlement.

`/dashboard/data` should expose:

- `balance.available`
- `balance.pendingSettlement`
- transaction rows with `status: "pending_settle"` for the receiver

The payer-side dashboard should not show `pending_settle`; payer history can
show the payment as sent/deducted according to the existing payment semantics.

### External Top-Up and Gateway Crediting

When external funds are detected for an agent wallet but Gateway crediting has
not completed, ledger records `pending_inbound_chain`.

`/dashboard/data` should expose the pending top-up as a transaction row. A
future optional `gatewayStage` can refine the progress display:

- `detected`
- `base_confirming`
- `gateway_crediting`
- `credited`

Once crediting completes, the transaction becomes `credited`, and available
balance increases.

### Withdrawal Lifecycle

Withdrawal submission records a transaction event immediately if the request is
accepted for processing:

- status: `withdraw_submitted`
- gross amount
- destination address
- estimated gas fee
- estimated net amount
- network
- tx hash if already known

When Gateway/chain completion is known, ledger updates the withdrawal record or
adds a linked terminal event with:

- status: `withdrawn`
- tx hash
- actual gas fee when available
- final net amount

If submission fails before a withdrawal record is created, the Funding form
shows an inline error and no history row is added. If a record exists and later
processing fails, the transaction remains visible with status `failed` and a
failure reason.

## Dashboard UX

### Overview

The main balance remains available balance. When `pendingSettlement > 0`, show a
secondary line below it:

```text
+$0.01 Settling
```

The line has an info tooltip explaining that the payment is in nanopayments
batch settlement and will become spendable after settlement.

### Portfolio

Each agent card continues to show available balance. If an agent has pending
settlement, show a small pending line:

```text
+$0.01 Settling
```

This clarifies that the agent has incoming funds waiting on settlement.

### Transactions

All transaction records are visible. Status chips include:

- `Settling` for `pending_settle`
- `Crediting` for `pending_inbound_chain`
- `Submitted` for `withdraw_submitted`
- `Credited` for `credited`
- `Withdrawn` for `withdrawn`
- `Failed` for `failed`

Rows can show tooltip/info affordances for `Settling` and `Crediting`.

Withdrawal rows use this amount convention:

```text
External · 0x1234...abcd       -$1.00
Gas ~$0.003 · Net $0.997 · Base
```

The main amount is gross debit. Secondary metadata shows gas and net amount.
Legacy withdrawals without gas metadata simply omit the gas/net secondary line.

### Funding: Add Funds

The receive card keeps the agent Base address and QR code. Add an info tooltip
next to the receive/Gateway label explaining:

- User sends USDC on Base to the agent address.
- Chief detects inbound funds.
- Chief credits/sweeps funds into the Gateway Wallet-backed settlement layer.
- Dashboard balance becomes spendable after crediting.

Add expectation-setting text below the receive card:

```text
Deposits may take a few minutes while Base confirms and Chief credits the
Gateway Wallet.
```

When there are `pending_inbound_chain` transactions, show a `PENDING TOP-UP`
section above Add Funds. It shows one card per pending top-up with a three-stage
progress indicator:

- Exchange withdrawn
- Base confirming
- Crediting Gateway Wallet

No production demo button should appear.

### Funding: Withdraw

The withdrawal form shows:

- destination address
- amount
- network
- estimated network fee
- net to destination
- ETA

On confirm:

- while submitting, the button may show `Submitting...` or `Broadcasting...`
- after accepted submission, Transactions shows a `Submitted` row
- after completion, the same row updates to `Withdrawn` or a linked terminal
  row appears

The Funding screen and Transactions tab must not show a `Withdrawing` status.

## Data Fields

Recommended normalized transaction fields:

- `id`
- `counterparty`
- `amount`
- `amountAtomic`
- `direction`
- `role`
- `status`
- `timestamp`
- `destinationAddress`
- `network`
- `txHash`
- `gasFeeAtomic`
- `gasFee`
- `netAmountAtomic`
- `netAmount`
- `failureReason`
- `gatewayStage`
- `linkedEntryId`

Recommended balance fields:

- `available`
- `availableAtomic`
- `pendingSettlement`
- `pendingSettlementAtomic`

## Error Handling

- Long-running `pending_settle` remains `Settling`; if backend marks it delayed,
  the UI may amber-tint the chip but should not mark it failed.
- Long-running `pending_inbound_chain` remains `Crediting`; if backend exposes a
  retry/error note, Funding can show "Chief is retrying Gateway crediting."
- Failed withdrawals after submission become visible `failed` transactions with
  a reason.
- Gas fee display uses actual gas if present, otherwise estimated gas.
- `netAmountAtomic = grossAmountAtomic - gasFeeAtomic` and must not be negative.
- Old records without new fields remain renderable.

## Implementation Scope

### Chain / Circle Runtime

- Preserve existing Gateway balance and withdrawal flows.
- Include withdrawal transaction hash and gas/net fields where available.
- Keep gas estimates explicit when actual gas is not available yet.

### Ledger

- Normalize ledger entries and dashboard transactions into the status model
  above.
- Add pending settlement balance output for receiver-side nanopayments.
- Add pending inbound top-up records and Gateway crediting status.
- Add withdrawal submitted and terminal withdrawal metadata.
- Ensure `/dashboard/data` exposes all fields dashboard needs.

### Dashboard

- Add status chip labels/tooltips.
- Add pending balance line in Overview and Portfolio.
- Add pending top-up cards and Gateway explanation in Funding.
- Add gas/net/ETA disclosure in Withdraw.
- Add gas/net secondary metadata in Transactions.
- Remove user-visible "Withdrawing" labels.

## Testing

### Ledger Tests

- `pending_settle` appears for the receiver and does not increase available
  balance.
- Payer view does not show receiver-side `pending_settle`.
- `pending_inbound_chain` appears in dashboard transactions and pending top-up
  data.
- `credited` increases available balance.
- Withdrawal submission records `withdraw_submitted`.
- Withdrawal completion records or updates to `withdrawn`.
- Failed post-submission withdrawal remains visible as `failed`.
- Gas/net metadata is present for new withdrawal records.
- Legacy records without gas metadata still render.

### Chain Tests

- Withdrawal responses expose tx hash and gas/net fields when available.
- Estimated gas fields are present when actual gas is not known.
- Net amount never goes negative.

### Dashboard Source/UI Tests

- Source contains `Settling`, `Crediting`, `Submitted`, gas/net labels, Gateway
  explanation, pending top-up UI, and pending balance line.
- Source does not contain user-facing `Withdrawing` / `提现中`.
- Transactions render gas/net secondary metadata for withdrawals.
- Funding renders pending top-up cards from real transaction data.
- Funding renders Gateway Wallet explanation and ETA text.

## Rollout

Use multiple commits for reviewability:

1. Ledger/chain status and metadata fields.
2. Dashboard status chips, pending balance lines, and pending top-up UI.
3. Withdrawal gas/net disclosure and submitted/withdrawn lifecycle polish.

Each commit should keep tests passing.
