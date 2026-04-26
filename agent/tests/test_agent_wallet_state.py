import json
import os
import tempfile
import threading
import unittest

from agent_wallet_state import AgentWalletStore


class AgentWalletStateStoreTests(unittest.TestCase):
    def test_agent_wallet_claim_flow_persists_hashed_claim_code(self) -> None:
        wallet_payload = {
            "circleWalletId": "circle-wallet-123",
            "circleWalletSetId": "wallet-set-456",
            "blockchain": "BASE-SEPOLIA",
            "walletAddress": "0xabc123",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentWalletStore(os.path.join(temp_dir, "agent-wallet.json"))

            owner = store.upsert_owner(
                provider="github",
                provider_user_id="12345",
                login="octocat",
                email="octocat@example.com",
                display_name="The Octocat",
                avatar_url="https://example.com/octocat.png",
            )
            updated_owner = store.upsert_owner(
                provider="github",
                provider_user_id="12345",
                login="octocat-updated",
                email=None,
                display_name=None,
                avatar_url=None,
            )
            self.assertEqual(updated_owner.ownerId, owner.ownerId)
            self.assertEqual(updated_owner.login, "octocat-updated")

            agent, claim_code = store.create_agent_wallet(
                agent_name="Demo Agent",
                agent_description="Local demo wallet",
                wallet_payload=wallet_payload,
            )

            self.assertEqual(agent.claimStatus, "unclaimed")
            self.assertIsNone(agent.ownerId)
            self.assertEqual(agent.walletId, "circle-wallet-123")
            self.assertEqual(agent.circleWalletSetId, "wallet-set-456")
            self.assertEqual(agent.walletAddress, "0xabc123")

            with open(store.path, encoding="utf-8") as state_file:
                raw_state = state_file.read()
            self.assertNotIn(claim_code, raw_state)
            self.assertIn(AgentWalletStore.hash_claim_code(claim_code), raw_state)

            claimed_agent = store.claim_wallet(claim_code, owner.ownerId)
            self.assertEqual(claimed_agent.agentId, agent.agentId)
            self.assertEqual(claimed_agent.ownerId, owner.ownerId)
            self.assertEqual(claimed_agent.claimStatus, "claimed")

            reloaded_state = store.load()
            self.assertEqual(reloaded_state.agents[0].ownerId, owner.ownerId)
            self.assertEqual(reloaded_state.agents[0].claimStatus, "claimed")
            self.assertEqual(
                reloaded_state.claims[0].consumedByOwnerId,
                owner.ownerId,
            )

            with self.assertRaises(ValueError):
                store.claim_wallet(claim_code, owner.ownerId)

    def test_claim_wallet_allows_only_one_concurrent_claim(self) -> None:
        wallet_payload = {
            "circleWalletId": "circle-wallet-123",
            "walletAddress": "0xabc123",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentWalletStore(os.path.join(temp_dir, "agent-wallet.json"))
            owner = store.upsert_owner(
                provider="github",
                provider_user_id="12345",
                login="octocat",
                email=None,
                display_name=None,
                avatar_url=None,
            )
            _, claim_code = store.create_agent_wallet(
                agent_name="Demo Agent",
                agent_description=None,
                wallet_payload=wallet_payload,
            )

            barrier = threading.Barrier(2)
            successes = []
            errors = []

            def attempt_claim() -> None:
                barrier.wait()
                try:
                    successes.append(store.claim_wallet(claim_code, owner.ownerId))
                except ValueError as error:
                    errors.append(str(error))

            threads = [threading.Thread(target=attempt_claim) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(successes), 1)
            self.assertEqual(errors, ["claim code has already been consumed"])

    def test_save_uses_atomic_replace_without_leaving_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "agent-wallet.json")
            store = AgentWalletStore(path)
            state = store.load()

            store.save(state)

            with open(path, encoding="utf-8") as state_file:
                payload = json.load(state_file)
            self.assertEqual(payload["owners"], [])
            self.assertEqual(
                [
                    name
                    for name in os.listdir(temp_dir)
                    if name.startswith(".agent-wallet-state-")
                ],
                [],
            )

    def test_add_service_and_payment_persist_demo_ledger(self) -> None:
        wallet_payload = {
            "circleWalletId": "circle-wallet-123",
            "walletAddress": "0x3333333333333333333333333333333333333333",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentWalletStore(os.path.join(temp_dir, "agent-wallet.json"))
            agent, _ = store.create_agent_wallet(
                agent_name="Demo Agent",
                agent_description=None,
                wallet_payload=wallet_payload,
            )
            service = store.add_service(
                agent_id=agent.agentId,
                service_payload={
                    "name": "Research Summary",
                    "path": "/x402/agent-services/research-summary",
                    "priceAtomic": "10000",
                    "assetAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                    "network": "eip155:84532",
                    "payTo": "0x3333333333333333333333333333333333333333",
                    "active": True,
                },
            )
            payment = store.add_payment(
                service_id=service.serviceId,
                request_url="http://x402-seller:8000/x402/agent-services/research-summary",
                result={
                    "upstream": {"status": 200, "payload": {"ok": True}},
                    "payment": {
                        "response": {
                            "success": True,
                            "transaction": "0xsettled",
                            "network": "eip155:84532",
                        }
                    },
                },
            )

            reloaded = store.load()

        self.assertEqual(reloaded.services[0].serviceId, service.serviceId)
        self.assertEqual(reloaded.services[0].payTo, agent.walletAddress)
        self.assertEqual(payment.status, "settled")
        self.assertEqual(payment.txHash, "0xsettled")
        self.assertEqual(payment.sellerAgentId, agent.agentId)
        self.assertEqual(payment.sellerWalletAddress, agent.walletAddress)

    def test_add_payment_persists_failed_x402_result_without_tx_hash(self) -> None:
        wallet_payload = {
            "circleWalletId": "circle-wallet-123",
            "walletAddress": "0x3333333333333333333333333333333333333333",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentWalletStore(os.path.join(temp_dir, "agent-wallet.json"))
            agent, _ = store.create_agent_wallet(
                agent_name="Demo Agent",
                agent_description=None,
                wallet_payload=wallet_payload,
            )
            service = store.add_service(
                agent_id=agent.agentId,
                service_payload={
                    "name": "Research Summary",
                    "path": "/x402/agent-services/research-summary",
                    "priceAtomic": "10000",
                    "assetAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                    "network": "eip155:84532",
                    "payTo": agent.walletAddress,
                    "active": True,
                },
            )
            payment = store.add_payment(
                service_id=service.serviceId,
                request_url="http://x402-seller:8000/x402/agent-services/research-summary",
                result={
                    "error": {"message": "missing buyer signer"},
                    "payment": {"response": {"success": False}},
                },
            )

        self.assertEqual(payment.status, "failed")
        self.assertIsNone(payment.txHash)
        self.assertIsNone(payment.settlementReference)
        self.assertEqual(
            payment.resultSummary["error"]["message"],
            "missing buyer signer",
        )


if __name__ == "__main__":
    unittest.main()
