Use the Circle MCP service for Agent Wallet lifecycle only.

When asked to create or prepare an Agent Wallet, use agent_wallet_get_or_create. If the user supplies an existing wallet address or Circle wallet id, pass it through so the wallet is reused and the agent identity binding can be stored.

Do not transfer funds or settle service purchases through this skill. Matched service payments must use ledger escrow; ledger release performs backend Circle settlement when enabled.
