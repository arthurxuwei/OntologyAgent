import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main


class LedgerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = str(Path(self.temp_dir.name) / "ledger.json")
        self.previous_state_path = os.environ.get("LEDGER_STATE_PATH")
        os.environ["LEDGER_STATE_PATH"] = self.state_path
        main.get_store.cache_clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.get_store.cache_clear()
        if self.previous_state_path is None:
            os.environ.pop("LEDGER_STATE_PATH", None)
        else:
            os.environ["LEDGER_STATE_PATH"] = self.previous_state_path
        self.temp_dir.cleanup()

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_credit_creates_account_and_entry(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        self.assertEqual(response.status_code, 200)
        account = response.json()["account"]
        self.assertEqual(account["agentId"], "agent_buyer")
        self.assertEqual(account["availableAtomic"], "5000000")
        self.assertEqual(account["lockedAtomic"], "0")

        state = self.client.get("/ledger/state").json()
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(state["entries"][0]["entryType"], "credit")

    def test_create_escrow_moves_buyer_available_to_locked(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        response = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
                "taskId": "task_123",
                "description": "Research task",
            },
        )

        self.assertEqual(response.status_code, 200)
        escrow = response.json()["escrow"]
        self.assertEqual(escrow["status"], "locked")
        self.assertEqual(escrow["amountAtomic"], "3000000")

        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "3000000")

    def test_create_escrow_rejects_insufficient_available_balance(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "1000000", "reason": "demo funding"},
        )

        response = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "insufficient available balance")
        state = self.client.get("/ledger/state").json()
        self.assertEqual(state["escrows"], [])
        self.assertEqual(state["accounts"][0]["availableAtomic"], "1000000")
        self.assertEqual(state["accounts"][0]["lockedAtomic"], "0")

    def test_release_escrow_moves_locked_funds_to_seller_available(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]

        response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escrow"]["status"], "released")
        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertEqual(accounts["agent_seller"]["availableAtomic"], "3000000")

    def test_refund_escrow_moves_locked_funds_back_to_buyer_available(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]

        response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/refund")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escrow"]["status"], "refunded")
        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "5000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertNotIn("agent_seller", accounts)

    def test_settled_escrow_cannot_be_mutated_again(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]
        self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        refund = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/refund")
        release = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(refund.status_code, 400)
        self.assertEqual(refund.json()["detail"], "escrow is not locked")
        self.assertEqual(release.status_code, 400)
        self.assertEqual(release.json()["detail"], "escrow is not locked")

    def test_rejects_non_integer_amounts(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "1.5", "reason": "bad funding"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "amountAtomic must be a positive integer string",
        )

    def test_missing_escrow_returns_not_found(self) -> None:
        response = self.client.post("/ledger/escrows/escrow_missing/release")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "escrow not found")

    def test_state_persists_to_json_file(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store.cache_clear()
        reloaded_client = TestClient(main.app)

        state = reloaded_client.get("/ledger/state").json()

        self.assertEqual(state["accounts"][0]["agentId"], "agent_buyer")
        self.assertEqual(state["accounts"][0]["availableAtomic"], "5000000")
        self.assertTrue(Path(self.state_path).exists())


if __name__ == "__main__":
    unittest.main()
