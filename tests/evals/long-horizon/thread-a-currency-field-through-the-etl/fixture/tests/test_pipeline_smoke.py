"""End-to-end run over the checked-in sample files."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.config import Config  # noqa: E402
from etl.pipeline import Pipeline  # noqa: E402
from etl.readers import CsvOrderReader  # noqa: E402
from etl.transforms import DedupeOrders, EnrichRegion, Normalize  # noqa: E402
from etl.validate import Validator  # noqa: E402
from etl.writers import JsonlWriter  # noqa: E402


def import_file(name: str) -> tuple[object, list[dict[str, str]]]:
    config = Config.load(ROOT / "config" / "pipeline.json")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "feed.jsonl"
        with JsonlWriter(out) as writer:
            result = Pipeline(
                reader=CsvOrderReader(ROOT / "data" / name),
                validator=Validator(config),
                writer=writer,
                transforms=(Normalize(), EnrichRegion()),
                filters=(DedupeOrders(),),
            ).run()
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return result, records


class PipelineSmokeTest(unittest.TestCase):
    def test_the_sample_file_imports_cleanly(self) -> None:
        result, records = import_file("orders_sample.csv")
        self.assertEqual((result.written, result.rejected, result.dropped), (4, 0, 0))
        self.assertEqual([r["order_id"] for r in records], ["A-1001", "A-1002", "A-1003", "A-1004"])

    def test_the_legacy_region_code_is_expanded(self) -> None:
        _, records = import_file("orders_sample.csv")
        self.assertEqual(records[1]["region"], "emea")

    def test_the_bad_file_rejects_rows_without_aborting(self) -> None:
        result, records = import_file("orders_bad.csv")
        self.assertEqual((result.written, result.rejected, result.dropped), (1, 3, 1))
        self.assertEqual([r["order_id"] for r in records], ["B-2004"])

    def test_order_id_leads_every_line(self) -> None:
        _, records = import_file("orders_sample.csv")
        for record in records:
            self.assertEqual(next(iter(record)), "order_id")


if __name__ == "__main__":
    unittest.main()
