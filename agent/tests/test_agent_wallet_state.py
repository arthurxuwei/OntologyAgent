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


if __name__ == "__main__":
    unittest.main()
