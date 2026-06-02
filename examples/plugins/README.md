# Custom plugins

Drop a Python file in `.ronin/plugins/` and `csk` will pick up any tools it exposes — no fork required.

## Anatomy

```python
# .ronin/plugins/my_tool.py
from ronin_agent_patterns import Tool


def my_handler(arg: str) -> str:
    return "result"


def register_tools() -> list[Tool]:
    return [
        Tool(
            name="my_tool",
            description="What it does (the LLM reads this).",
            input_schema={
                "type": "object",
                "properties": {"arg": {"type": "string"}},
                "required": ["arg"],
            },
            handler=my_handler,
        ),
    ]
```

That's it. `csk ask "..."` now has `my_tool` available alongside the built-in service tools.

## Try the example

```bash
mkdir -p .ronin/plugins
cp examples/plugins/weather.py .ronin/plugins/weather.py
csk plugins                                                       # confirms it loaded
csk ask "what's the weather in tokyo right now?"                  # invokes the plugin
```

## Conventions

- Files starting with `_` are skipped.
- `register_tools()` must return a `list[Tool]`. Anything else fails loudly in `csk plugins`.
- Errors in one plugin do not abort the others.
- Plugins load AFTER built-in service tools. If you give a plugin tool the same name as a built-in, the built-in wins.
