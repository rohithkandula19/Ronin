# hooks

Installs a repo-local `pre-commit` hook that runs the project's checks.

```python
from pathlib import Path

from hooks import install_hook

install_hook(Path("."), [("lint", ["ruff", "check", "."]), ("types", ["mypy", "src"])])
```

The hook is generated from a template so that the installed file has no
dependency on this package.
