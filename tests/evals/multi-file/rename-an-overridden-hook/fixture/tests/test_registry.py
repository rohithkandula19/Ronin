import unittest

from plugkit import Plugin, Registry
from plugkit.plugins import AuditPlugin, MetricsPlugin


class SilentPlugin(Plugin):
    name = "silent"


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.audit = AuditPlugin()
        self.metrics = MetricsPlugin()
        self.registry = Registry([self.audit, self.metrics, SilentPlugin()])
        self.registry.start("run-7")

    def test_dispatch_collects_a_status_per_talkative_plugin(self):
        statuses = self.registry.dispatch({"kind": "write"})
        self.assertEqual(
            statuses, {"audit": "audit:write", "metrics": "metrics:write=1"}
        )

    def test_a_plugin_that_does_not_override_the_event_hook_is_silent(self):
        statuses = self.registry.dispatch({"kind": "read"})
        self.assertNotIn("silent", statuses)

    def test_the_base_hook_returns_none(self):
        self.assertIsNone(SilentPlugin().handle_event({"kind": "read"}))

    def test_counts_accumulate_across_dispatches(self):
        self.registry.dispatch({"kind": "write"})
        self.registry.dispatch({"kind": "write"})
        self.registry.dispatch({"kind": "read"})
        self.assertEqual(self.metrics.counts, {"write": 2, "read": 1})

    def test_audit_entries_are_tagged_with_the_run_id(self):
        self.registry.dispatch({"kind": "write"})
        self.assertEqual(self.audit.entries, ["run-7/write"])

    def test_flush_returns_each_plugins_payload(self):
        self.registry.dispatch({"kind": "write"})
        self.assertEqual(
            self.registry.flush(),
            {
                "audit": {"entries": ["run-7/write"]},
                "metrics": {"counts": {"write": 1}},
                "silent": {},
            },
        )


if __name__ == "__main__":
    unittest.main()
