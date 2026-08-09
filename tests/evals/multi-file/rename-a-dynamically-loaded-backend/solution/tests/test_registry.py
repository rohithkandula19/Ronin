import tempfile
import unittest
from pathlib import Path

from storekit import alias_for, load_profile, open_profile, resolve
from storekit.backends import FileStore, MemoryStore

CONFIG = Path(__file__).resolve().parent.parent / "config" / "backends.json"


class ResolveTests(unittest.TestCase):
    def test_the_file_alias_resolves_to_the_json_backed_store(self):
        self.assertIs(resolve("file"), FileStore)

    def test_the_memory_alias_resolves_to_the_dict_store(self):
        self.assertIs(resolve("memory"), MemoryStore)

    def test_alias_for_is_the_reverse_lookup(self):
        self.assertEqual(alias_for(MemoryStore), "memory")
        self.assertIn(alias_for(FileStore), {"file", "json"})


class ConfigTests(unittest.TestCase):
    def test_the_default_profile_uses_the_file_store_with_its_options(self):
        cls, options = load_profile(CONFIG)
        self.assertIs(cls, FileStore)
        self.assertEqual(options, {"path": "var/store.json", "indent": 2})

    def test_the_scratch_profile_uses_memory(self):
        cls, options = load_profile(CONFIG, "scratch")
        self.assertIs(cls, MemoryStore)
        self.assertEqual(options, {})

    def test_opening_the_default_profile_round_trips_a_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = open_profile(CONFIG, path=Path(tmp) / "store.json")
            store.put("token", "abc")
            self.assertEqual(store.get("token"), "abc")
            self.assertEqual(store.keys(), ["token"])
            reopened = FileStore(Path(tmp) / "store.json")
            self.assertEqual(reopened.get("token"), "abc")


if __name__ == "__main__":
    unittest.main()
