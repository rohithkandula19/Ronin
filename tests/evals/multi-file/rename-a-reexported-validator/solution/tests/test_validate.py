import unittest

from feedkit import collect_payload_problems
from feedkit.pipeline import ingest
from feedkit.report import render_summary
from feedkit.validate import collect_payload_problems as check_direct

GOOD = {"id": "a1", "title": "Hello", "url": "https://example.invalid/a1"}


class CheckPayloadTests(unittest.TestCase):
    def test_a_complete_payload_has_no_problems(self):
        self.assertEqual(collect_payload_problems(GOOD), [])

    def test_the_reexported_and_direct_names_are_the_same_function(self):
        self.assertIs(collect_payload_problems, check_direct)

    def test_missing_fields_are_reported_in_declaration_order(self):
        self.assertEqual(
            collect_payload_problems({"url": "https://example.invalid/x"}),
            ["missing required field: id", "missing required field: title"],
        )

    def test_items_must_be_a_list(self):
        payload = dict(GOOD, items={"nope": 1})
        self.assertIn("items must be a list", collect_payload_problems(payload))

    def test_a_relative_url_is_rejected(self):
        self.assertIn("url must be http(s)", collect_payload_problems(dict(GOOD, url="/a1")))


class PipelineTests(unittest.TestCase):
    def test_bad_payloads_land_in_rejected(self):
        result = ingest([GOOD, {"id": "b2"}])
        self.assertEqual(result["accepted"], [GOOD])
        self.assertEqual(len(result["rejected"]), 1)

    def test_summary_counts_problems(self):
        self.assertEqual(render_summary(GOOD), "a1: ok")
        self.assertTrue(render_summary({"id": "b2"}).startswith("b2: 2 problem(s)"))


if __name__ == "__main__":
    unittest.main()
