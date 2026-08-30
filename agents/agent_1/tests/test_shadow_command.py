from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from agents.agent_1.run import main


class FakeProvider:
    def __init__(self) -> None:
        self.calls = []

    def observe(self, now):
        self.calls.append(now)
        return "shadow-result"


class ShadowCommandTests(unittest.TestCase):
    def test_shadow_once_auto_refreshes_without_manual_config(self) -> None:
        provider = FakeProvider()
        config = SimpleNamespace()
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run.connect_paper", return_value=object()),
            patch("agents.agent_1.run.disconnect"),
            patch("agents.agent_1.run.load_state", return_value=SimpleNamespace(bound_contracts={})),
            patch(
                "agents.agent_1.run.build_auto_live_provider", return_value=provider
            ) as build,
            patch(
                "agents.agent_1.run.render_shadow_result", return_value="shadow-only"
            ),
        ):
            result = main(["shadow-once", "--run-id", "test-auto-shadow"])

        self.assertEqual(result, 0)
        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(build.call_args.kwargs["executable"])

    def test_shadow_once_observes_without_creating_execution_store(self) -> None:
        provider = FakeProvider()
        config = SimpleNamespace()
        with (
            patch("agents.agent_1.run.load_config", return_value=config),
            patch("agents.agent_1.run.connect_paper", return_value=object()),
            patch("agents.agent_1.run.disconnect") as disconnect,
            patch(
                "agents.agent_1.run.build_shadow_provider", return_value=provider
            ) as build,
            patch(
                "agents.agent_1.run.render_shadow_result", return_value="shadow-only"
            ),
            patch("agents.agent_1.run._create_store") as create_store,
        ):
            result = main(
                [
                    "shadow-once",
                    "--shadow-config",
                    str(Path("shadow.json")),
                    "--run-id",
                    "test-shadow",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(provider.calls), 1)
        build.assert_called_once()
        create_store.assert_not_called()
        disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
