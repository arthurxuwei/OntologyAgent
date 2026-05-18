Use this skill before any funding, payment, paid API call, chain transfer, escrow lock, release, or refund.

For Agent Wallet funding, route with deliveryMode=funding and use only the returned onramp tool. A hosted onramp session does not credit ledger balance until the provider-confirmed onramp is confirmed.

For immediate internal Agent-to-Agent payments that do not need acceptance, route with deliveryMode=agent_transfer and use only agent_wallet_transfer.

Only continue with tools returned in allowedTools. If the router returns needs_clarification, ask for clarification before funding, paying, or settling.
