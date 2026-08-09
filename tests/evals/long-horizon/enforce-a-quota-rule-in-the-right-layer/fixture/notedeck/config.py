"""The only reader of ``config/app.json``.

Unknown keys in the file are ignored on purpose: a config written for a newer
build must not stop an older one from starting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_PATH = "config/app.json"


@dataclass(frozen=True)
class Settings:
    max_title_length: int = 80
    max_body_length: int = 4000
    search_page_size: int = 20
    archive_enabled: bool = True

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Settings":
        target = Path(path)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target} is not valid JSON: {exc}") from None
        known = {f.name: f.type for f in fields(cls)}
        values: dict[str, object] = {}
        for section in ("limits", "features"):
            for key, value in (data.get(section) or {}).items():
                if key in known:
                    values[key] = value
        return cls(**values)  # type: ignore[arg-type]
