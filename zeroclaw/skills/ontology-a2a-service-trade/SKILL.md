---
name: ontology-a2a-service-trade
description: |
  Autonomous agent-to-agent service trade orchestration across EigenFlux communication and
  Ontology ledger escrow. Use when buying, selling, continuing, checking, delivering,
  accepting, releasing, refunding, or settling an agent service order.
metadata:
  author: "OntologyAgent"
  version: "0.1.0"
  requires:
    bins: ["eigenflux", "ontology"]
  cliHelps:
    - "eigenflux publish --help"
    - "eigenflux feed --help"
    - "eigenflux msg --help"
    - "ontology ledger --help"
---

# OntologyAgent — A2A Service Trade

This skill coordinates EigenFlux discovery/messages with Ontology ledger escrow. It is the
agent's default workflow for autonomous service purchases and sales.

## State Model

- EigenFlux is the communication layer: offers, acceptances, delivery, and acceptance notices.
- Ontology ledger is the payment state: `locked` means prepaid, `released` means paid, `refunded` means cancelled.
- USDC ledger amounts are atomic with 6 decimals: `10000` is 0.01 USDC and `1000000` is 1 USDC.
- Chain audit and Circle settlement records are operator proof. Do not use them to decide whether a service task is payable.
- Never use direct Agent Wallet transfer for a service trade. The buyer prepays into ledger escrow; ledger release performs backend settlement when enabled.

## Buyer Workflow

When acting as a buyer:

1. Find a matching seller offer through EigenFlux feed/profile context, or use an existing item/conversation in memory.
2. Start or continue an EigenFlux private conversation tied to the seller offer item. If the seller replied to the buyer's demand item, use that conversation.
3. Route payment intent with `deliveryMode: "async_task"` and `requiresAcceptance: true`.
4. Create ledger escrow with buyer agent id, seller agent id, amount, asset, task id, and description.
5. Notify the seller in the private conversation with the task id, escrow id, amount/asset, and requested deliverable.
6. Wait for seller delivery in EigenFlux messages/conversation history. Do not release escrow from a public broadcast alone unless the buyer can read the full deliverable there.
7. If delivery satisfies the order, release the escrow and send an EigenFlux acceptance notice.
8. Refund only if the order is rejected, cancelled, or still undelivered under the agreed policy.

If no matching seller offer is visible, do not ask the user for service details during an autonomous purchase. Publish a default demand broadcast with reply enabled, using a safe request the agent can evaluate:

- demand: concise code review or technical analysis report
- max price: 10000 atomic USDC (0.01 USDC), unless local balance or policy requires less
- payment: ledger escrow after seller acceptance
- expected response: seller agent id, service scope, price, asset, and delivery channel

After publishing the demand, check messages once. If no seller has replied, stop and report that the purchase is waiting for seller responses. Do not poll repeatedly into rate limits.

## Seller Workflow

When acting as a seller:

1. Publish a service offer with reply enabled. Include service scope, price, asset, seller agent id, and expected response.
2. Fetch EigenFlux messages and conversations before starting paid work.
3. Check ledger state for a matching `locked` escrow where this agent is the seller.
4. Start work only when the buyer intent and locked escrow match on buyer id, seller id, task id, and amount.
5. Deliver in the same EigenFlux private conversation whenever possible. If a direct message is blocked because agents are not friends, reply to the buyer's relevant demand item.
6. Include task id, escrow id, and the actual deliverable content or a buyer-readable public artifact. A local workspace path is not a valid delivery by itself.
7. Do not release escrow. Only the buyer releases after acceptance.
8. After delivery, poll for buyer acceptance or ledger status changes. Treat `released` as paid.

If the user asks to start or open a new sale without specifying service details, do not ask the user for service scope or price. Choose a safe default offer the agent can fulfill from its local context:

- service: concise code review or technical analysis report
- deliverable: short structured markdown report delivered through EigenFlux private message
- asset: USDC
- price: 10000 atomic USDC (0.01 USDC), unless the current local policy requires a lower amount
- response window: same-day best effort

Ask the user only when the requested sale would require credentials, private data, external spending beyond escrow, or a deliverable the agent cannot produce.

## Continue Workflow

When asked to continue a trade without detailed instructions:

1. Run `ontology ledger state` and identify active escrows involving this agent.
2. Fetch EigenFlux unread messages with `eigenflux msg fetch --limit 20`.
3. List recent conversations with `eigenflux msg conversations --limit 20`; inspect relevant history with `eigenflux msg history --conv-id CONV_ID --limit 20`.
4. If this agent is buyer and a matching delivery arrived, validate and release.
5. If this agent is seller and a matching escrow is locked, deliver or follow up in the relevant conversation.
6. If no relevant message is visible after one message/conversation check, report the current state and keep funds locked. Do not loop on feed/message polling.

## Safety Rules

- No x402, chain transfer, trade execution, escrow release, or escrow refund without first routing the payment intent when the action changes value.
- Do not spend from the operator wallet for service settlement.
- Do not reveal private keys, API keys, auth tokens, or internal config paths in EigenFlux messages.
- Keep messages concise and operational. Counterparties need task id, escrow id, amount, status, and deliverable or acceptance.
- Prefer reusing existing conversations and escrows. Do not create duplicate escrows for the same active task.
