# Plugin SDK

Ronin plugins are Python modules in `.ronin/plugins/` that expose a
`register_tools() -> list[Tool]` function. Importing a module executes Python,
so Ronin will not import a repository plugin until its current file contents
have been explicitly trusted with `ronin plugin trust <name>`.

## Manifest

Place a literal `PLUGIN` dictionary near the top of every new plugin. Ronin
reads this value with Python's AST before import; expressions, function calls,
or dynamic metadata are rejected without executing the module.

```python
PLUGIN = {
    "name": "issue_lookup",
    "version": "1",
    "description": "Fetch an issue from the tracker.",
    "capabilities": ["network"],
}
```

Supported capabilities are `network`, `filesystem`, `subprocess`, and
`payment`. Declare only what the handler needs. `ronin plugins` displays the
declaration before loading a trusted module.

`subprocess` and `payment` are high-risk capabilities: each call requires an
explicit approval, including under `--yolo`. A plugin without a manifest remains
compatible, but Ronin treats it as declaring every capability until it is
updated. This is deliberately conservative and does not grant old plugins a
less restrictive posture by omission.

The manifest is host-owned safety metadata. A `Tool(capabilities=...)` returned
by executable plugin code cannot override its plugin manifest.

A manifest is not a sandbox. Once trusted, a Python plugin still runs with the
Ronin process's privileges; capability declarations make approval policy visible
and conservative, but they cannot confine code that a user has chosen to trust.

## Minimal Plugin

```python
from ronin_agent_patterns import Tool

PLUGIN = {
    "name": "upper_text",
    "version": "1",
    "description": "Convert text to uppercase.",
    "capabilities": [],
}

def upper_text(text: str) -> dict:
    return {"text": text.upper()}

def register_tools():
    return [Tool(
        name="upper_text",
        description="Convert text to uppercase.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=upper_text,
    )]
```

`ronin plugin new <name>` and `ronin plugin from-api ...` create manifests
automatically. Review a third-party plugin before trusting it; trust is stored
outside the repository and is bound to the file content hash, so editing a
trusted file revokes its approval.

## Migration

Existing plugins keep working. Add a literal `PLUGIN` declaration, review the
capabilities, then run `ronin plugin trust <name>` after editing because the
content hash changes. Remove an incorrect declaration rather than using dynamic
Python to construct it; the loader refuses dynamic manifests before import.
