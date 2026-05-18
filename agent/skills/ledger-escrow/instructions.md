Use ledger escrow for asynchronous A2A task settlement.

Escrow creation locks buyer balance, release transfers locked funds to the seller, and refund returns locked funds to the buyer. Any payment flow must first route the payment intent.

Use agent_wallet_transfer for immediate internal Agent-to-Agent payments that do not require acceptance, locking, release, or refund. Direct transfers must complete the real Circle USDC transfer before ledger available balances are updated.
