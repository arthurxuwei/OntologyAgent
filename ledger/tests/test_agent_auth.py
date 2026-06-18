import unittest

from utils import generate_agent_id, generate_ulid

_CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


class TestUlid(unittest.TestCase):
    def test_generate_ulid_is_26_crockford_chars(self) -> None:
        value = generate_ulid()
        self.assertEqual(len(value), 26)
        self.assertTrue(set(value).issubset(_CROCKFORD), value)

    def test_generate_ulid_is_unique(self) -> None:
        values = {generate_ulid() for _ in range(1000)}
        self.assertEqual(len(values), 1000)

    def test_generate_agent_id_has_prefix(self) -> None:
        agent_id = generate_agent_id()
        self.assertTrue(agent_id.startswith("kloop_agent_"))
        suffix = agent_id[len("kloop_agent_") :]
        self.assertEqual(len(suffix), 26)
        self.assertTrue(set(suffix).issubset(_CROCKFORD), suffix)


if __name__ == "__main__":
    unittest.main()
