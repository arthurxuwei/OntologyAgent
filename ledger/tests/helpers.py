import asyncio
import base64
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import auth
import httpx
import jwt
import main
import services
import webhooks
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from fastapi.testclient import TestClient


class LedgerServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).resolve().parents[2] / ".codex-tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"ledger-test-{uuid.uuid4().hex}"
        self.temp_dir.mkdir()
        self.state_path = str(self.temp_dir / "ledger.json")
        self.db_path = str(self.temp_dir / "ledger.sqlite3")
        self.previous_state_path = os.environ.get("LEDGER_STATE_PATH")
        self.previous_db_path = os.environ.get("LEDGER_DB_PATH")
        self.previous_coinbase_mock = os.environ.get("COINBASE_ONRAMP_MOCK")
        self.previous_coinbase_key_id = os.environ.get("COINBASE_API_KEY_ID")
        self.previous_coinbase_private_key = os.environ.get("COINBASE_API_PRIVATE_KEY")
        self.previous_chain_record_enabled = os.environ.get("LEDGER_CHAIN_RECORD_ENABLED")
        self.previous_chain_record_require_success = os.environ.get(
            "LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS"
        )
        self.previous_settlement_enabled = os.environ.get("LEDGER_SETTLEMENT_ENABLED")
        self.previous_settlement_require_success = os.environ.get(
            "LEDGER_SETTLEMENT_REQUIRE_SUCCESS"
        )
        self.previous_settlement_http_url = os.environ.get("LEDGER_SETTLEMENT_HTTP_URL")
        self.previous_wallet_http_url = os.environ.get("LEDGER_WALLET_HTTP_URL")
        self.previous_circle_api_key = os.environ.get("CIRCLE_API_KEY")
        self.previous_circle_webhook_verify = os.environ.get("CIRCLE_WEBHOOK_VERIFY_SIGNATURE")
        os.environ["LEDGER_STATE_PATH"] = self.state_path
        os.environ["LEDGER_DB_PATH"] = self.db_path
        os.environ["LEDGER_CHAIN_RECORD_ENABLED"] = "false"
        os.environ["LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS"] = "false"
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "false"
        os.environ["LEDGER_SETTLEMENT_REQUIRE_SUCCESS"] = "false"
        os.environ["CIRCLE_WEBHOOK_VERIFY_SIGNATURE"] = "false"
        main.get_store.cache_clear()
        main.get_coinbase_onramp_client.cache_clear()
        main.get_chain_recorder.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        main.get_ledger_wallet_client.cache_clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.get_store.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        main.get_ledger_wallet_client.cache_clear()
        if self.previous_state_path is None:
            os.environ.pop("LEDGER_STATE_PATH", None)
        else:
            os.environ["LEDGER_STATE_PATH"] = self.previous_state_path
        if self.previous_db_path is None:
            os.environ.pop("LEDGER_DB_PATH", None)
        else:
            os.environ["LEDGER_DB_PATH"] = self.previous_db_path
        if self.previous_coinbase_mock is None:
            os.environ.pop("COINBASE_ONRAMP_MOCK", None)
        else:
            os.environ["COINBASE_ONRAMP_MOCK"] = self.previous_coinbase_mock
        self._restore_env("COINBASE_API_KEY_ID", self.previous_coinbase_key_id)
        self._restore_env("COINBASE_API_PRIVATE_KEY", self.previous_coinbase_private_key)
        self._restore_env("LEDGER_CHAIN_RECORD_ENABLED", self.previous_chain_record_enabled)
        self._restore_env(
            "LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS",
            self.previous_chain_record_require_success,
        )
        self._restore_env("LEDGER_SETTLEMENT_ENABLED", self.previous_settlement_enabled)
        self._restore_env(
            "LEDGER_SETTLEMENT_REQUIRE_SUCCESS",
            self.previous_settlement_require_success,
        )
        self._restore_env("LEDGER_SETTLEMENT_HTTP_URL", self.previous_settlement_http_url)
        self._restore_env("LEDGER_WALLET_HTTP_URL", self.previous_wallet_http_url)
        self._restore_env("CIRCLE_API_KEY", self.previous_circle_api_key)
        self._restore_env("CIRCLE_WEBHOOK_VERIFY_SIGNATURE", self.previous_circle_webhook_verify)
        main.get_coinbase_onramp_client.cache_clear()
        main.get_chain_recorder.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        main.get_ledger_wallet_client.cache_clear()

    def _restore_env(self, name: str, previous: str | None) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def ledger_domain_state(self, agent_id: str | None = None) -> dict:
        if agent_id:
            account_response = self.client.get(f"/ledger/accounts/{agent_id}")
            accounts = (
                [account_response.json()["account"]]
                if account_response.status_code == 200
                else []
            )
            entries = self.client.get(f"/ledger/accounts/{agent_id}/entries?limit=500").json()[
                "entries"
            ]
            escrows = self.client.get(f"/ledger/accounts/{agent_id}/escrows").json()["escrows"]
            onramp_sessions = self.client.get(
                f"/ledger/onramp-sessions?agentId={agent_id}&limit=500"
            ).json()["onrampSessions"]
            state = main.get_store().load_for_agent(agent_id).model_dump()
            state.update(
                {
                    "accounts": accounts,
                    "entries": entries,
                    "escrows": escrows,
                    "onrampSessions": onramp_sessions,
                }
            )
            return state
        return {
            "accounts": self.client.get("/ledger/accounts").json()["accounts"],
            "entries": self.client.get("/ledger/entries?limit=500").json()["entries"],
            "escrows": self.client.get("/ledger/escrows").json()["escrows"],
            "onrampSessions": self.client.get("/ledger/onramp-sessions?limit=500").json()[
                "onrampSessions"
            ],
            "onrampEvents": main.get_store().load().model_dump()["onrampEvents"],
            "circleWebhookEvents": main.get_store().load().model_dump()["circleWebhookEvents"],
            "chainRecords": main.get_store().load().model_dump()["chainRecords"],
            "settlementRecords": main.get_store().load().model_dump()["settlementRecords"],
        }
