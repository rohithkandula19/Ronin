"""bytes_stats reports byte-level facts."""

from __future__ import annotations

import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspectorkit.blob import Blob  # noqa: E402
from inspectorkit.plugins.bytes_stats import PLUGIN  # noqa: E402


class BytesStatsTest(unittest.TestCase):
    def test_it_counts_bytes(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b"hello"))
        self.assertEqual(facts["bytes"], "5")

    def test_an_empty_file_is_fully_printable(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b""))
        self.assertEqual(facts["printable_ratio"], "1.000")

    def test_control_bytes_lower_the_printable_ratio(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b"ab\x00\x00"))
        self.assertEqual(facts["nul_bytes"], "2")
        self.assertEqual(facts["printable_ratio"], "0.500")

    def test_crlf_endings_are_flagged(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b"a\r\nb\r\n"))
        self.assertEqual(facts["crlf_line_endings"], "yes")

    def test_it_fingerprints_the_bytes(self) -> None:
        self.assertEqual(
            PLUGIN.checksum(Blob(name="x", data=b"inspectorkit\n")),
            "crc32:%08x" % zlib.crc32(b"inspectorkit\n"),
        )

    def test_an_empty_file_fingerprints_to_zero(self) -> None:
        self.assertEqual(PLUGIN.checksum(Blob(name="x", data=b"")), "crc32:00000000")

    def test_the_fingerprint_declares_itself(self) -> None:
        self.assertIn("checksum", PLUGIN.declares)


if __name__ == "__main__":
    unittest.main()
