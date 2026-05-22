from __future__ import annotations

from pathlib import Path


DEFAULT_ASSET = "USDC"
DEFAULT_LEDGER_STATE_PATH = "ledger/data/offchain_ledger.json"
DEFAULT_COINBASE_API_BASE_URL = "https://api.developer.coinbase.com"
DEFAULT_COINBASE_TOKEN_PATH = "/onramp/v1/token"
DEFAULT_COINBASE_HOSTED_URL = "https://pay.coinbase.com/buy/select-asset"
DEFAULT_CHAIN_HTTP_URL = "http://chain:8091"
DEFAULT_SETTLEMENT_HTTP_URL = "http://circle:8093"
DEFAULT_WALLET_HTTP_URL = "http://circle:8093"
DEFAULT_CHAIN_RECORDER_ADDRESS = "0x000000000000000000000000000000000000dEaD"
DEFAULT_CIRCLE_PUBLIC_KEY_BASE_URL = "https://api.circle.com/v2/notifications/publicKey"
DEFAULT_BASE_SEPOLIA_USDC_ASSET_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
GATEWAY_SWEEP_MIN_WALLET_BALANCE_ATOMIC = 1_000_000
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
DEFAULT_PUBLIC_LEDGER_URL = "https://ledger.curawealth.ai"
SESSION_COOKIE = "chief_ledger_session"
OAUTH_STATE_COOKIE = "chief_ledger_oauth_state"
OAUTH_RETURN_COOKIE = "chief_ledger_oauth_return"
AUTH_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
LEDGER_CONSOLE_PATH = Path(__file__).resolve().parent / "web" / "index.html"
LEDGER_DASHBOARD_PATH = Path(__file__).resolve().parent / "web" / "dashboard.html"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
