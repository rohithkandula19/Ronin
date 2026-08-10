"""Dispatch looks hooks up from the catalog and refuses cleanly."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspectorkit.blob import Blob  # noqa: E402
from inspectorkit.dispatch import run, run_one  # noqa: E402
from inspectorkit.errors import CapabilityNotSupported, UnknownCapability  # noqa: E402
from inspectorkit.plugins.line_count import PLUGIN as LINE_COUNT  # noqa: E402
from inspectorkit.registry import Registry  # noqa: E402

BLOB = Blob(name="memo.txt", data=b"one\ntwo\n\nthree\n")


class DispatchTest(unittest.TestCase):
    def test_it_asks_a_declared_capability(self) -> None:
        result = run_one(LINE_COUNT, "summary", BLOB)
        self.assertEqual(result.plugin, "line_count")
        self.assertEqual(result.value["lines"], "4")

    def test_asking_for_something_a_plugin_lacks_is_a_clean_refusal(self) -> None:
        with self.assertRaises(CapabilityNotSupported):
            run_one(LINE_COUNT, "histogram", BLOB)

    def test_an_unknown_capability_names_the_catalog(self) -> None:
        with self.assertRaises(UnknownCapability) as caught:
            run_one(LINE_COUNT, "telepathy", BLOB)
        self.assertIn("catalog declares", str(caught.exception))

    def test_running_a_capability_over_an_empty_registry_is_empty(self) -> None:
        self.assertEqual(run(Registry(), "summary", BLOB), ())


if __name__ == "__main__":
    unittest.main()
