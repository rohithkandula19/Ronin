"""The shipped example dataset must stay valid and loadable."""
from __future__ import annotations

from pathlib import Path

from ronin_eval_suite import SWEBenchDataset

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "swebench_sample.jsonl"


def test_example_dataset_loads_and_parses_both_list_forms():
    ds = SWEBenchDataset.from_jsonl(EXAMPLE)
    assert len(ds) == 2

    by_id = {t.instance_id: t for t in ds}
    # task 101 uses native JSON arrays
    t1 = by_id["acme__widget-101"]
    assert t1.fail_to_pass == ["tests/test_widget.py::test_square_area"]
    assert len(t1.pass_to_pass) == 2
    # task 102 uses JSON-encoded string lists (the official dataset's form)
    t2 = by_id["acme__widget-102"]
    assert t2.fail_to_pass == ["tests/test_color.py::test_short_hex"]
    assert t2.pass_to_pass == ["tests/test_color.py::test_long_hex"]
    assert t2.repo == "acme/widget"
