from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.agent_1.broker import BrokerError, connect_paper, disconnect


class FakeIB:
    def __init__(self):
        self.connected = False
        self.connect_calls = []
        self.disconnect_calls = 0

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        self.connected = True

    def isConnected(self):
        return self.connected

    def managedAccounts(self):
        return ["DU123"]

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False


class BrokerConnectionTests(unittest.TestCase):
    def test_connects_only_to_configured_local_paper_endpoint(self) -> None:
        ib = FakeIB()
        config = SimpleNamespace(
            account="DU123", host="127.0.0.1", port=7497,
            client_id=31,
        )
        connected = connect_paper(config, ib_factory=lambda: ib, timeout_seconds=20)
        self.assertIs(connected, ib)
        self.assertEqual(ib.connect_calls, [{
            "host": "127.0.0.1", "port": 7497, "clientId": 31, "timeout": 20,
        }])

    def test_refuses_non_paper_endpoint_even_if_config_object_is_mutated(self) -> None:
        config = SimpleNamespace(
            account="DU123", host="example.com", port=7497,
            client_id=31,
        )
        with self.assertRaisesRegex(BrokerError, "localhost"):
            connect_paper(config, ib_factory=FakeIB)

    def test_disconnect_is_scoped_to_own_session(self) -> None:
        ib = FakeIB()
        ib.connected = True
        disconnect(ib)
        self.assertEqual(ib.disconnect_calls, 1)


if __name__ == "__main__":
    unittest.main()
