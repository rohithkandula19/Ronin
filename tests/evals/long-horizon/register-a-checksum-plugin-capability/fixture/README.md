# inspectorkit

A plugin-driven file inspector. Ops points it at a file (or a tree) and gets a
report built from whatever the enabled plugins can say about it.

## The plugin contract

Two things are separate on purpose:

* a **capability** is a question the tool knows how to ask (`summary`,
  `histogram`, `warnings`). Capabilities live in `inspectorkit/capabilities.py`,
  and each one names the hook method that answers it. The base class in
  `inspectorkit/plugin.py` defines that hook with a default that refuses, so
  asking a plugin something it cannot do is a clean error rather than an
  `AttributeError`.
* a **plugin** is a thing that answers some of those questions. It lists the
  capabilities it provides in `declares` and overrides exactly those hooks.
  `inspectorkit/registry.py` refuses to register a plugin whose declaration and
  implementation disagree in either direction, because a silently unanswered
  question is worse than a failed startup.

`inspectorkit/dispatch.py` is generic: it looks the hook up from the catalog, so
it never needs to know which capabilities exist.

## Running it

```
python3 inspect_file.py --path samples/notes.txt --all
python3 inspect_file.py --path samples/notes.txt --capability summary
python3 scan_tree.py --root samples
python3 -m unittest discover -s tests -t .
```

Which plugins are enabled, and the order the report renders its sections in,
come from `config/plugins.json`.
