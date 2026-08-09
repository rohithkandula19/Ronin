"""Registration refuses plugins whose declaration and implementation disagree."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspectorkit.blob import Blob  # noqa: E402
from inspectorkit.errors import RegistryError  # noqa: E402
from inspectorkit.plugin import Plugin  # noqa: E402
from inspectorkit.registry import Registry  # noqa: E402


class Honest(Plugin):
    name = "honest"
    declares = ("summary",)

    def summarize(self, blob: Blob) -> dict[str, str]:
        return {"bytes": str(blob.size)}


class Boastful(Plugin):
    name = "boastful"
    declares = ("summary", "histogram")

    def summarize(self, blob: Blob) -> dict[str, str]:
        return {}


class Shy(Plugin):
    name = "shy"
    declares = ()

    def warnings(self, blob: Blob) -> list[str]:
        return ["nobody will ever read this"]


class RegistryTest(unittest.TestCase):
    def test_a_consistent_plugin_registers(self) -> None:
        registry = Registry()
        registry.register(Honest())
        self.assertEqual(registry.names(), ("honest",))

    def test_declaring_a_capability_it_does_not_implement_is_refused(self) -> None:
        with self.assertRaises(RegistryError) as caught:
            Registry().register(Boastful())
        self.assertIn("histogram", str(caught.exception))

    def test_implementing_a_capability_it_does_not_declare_is_refused(self) -> None:
        with self.assertRaises(RegistryError) as caught:
            Registry().register(Shy())
        self.assertIn("declare", str(caught.exception))

    def test_two_plugins_cannot_share_a_name(self) -> None:
        registry = Registry()
        registry.register(Honest())
        with self.assertRaises(RegistryError):
            registry.register(Honest())

    def test_providers_are_listed_in_registration_order(self) -> None:
        registry = Registry()
        registry.register(Honest())
        self.assertEqual(registry.providers_of("summary"), ("honest",))
        self.assertEqual(registry.providers_of("histogram"), ())


if __name__ == "__main__":
    unittest.main()
