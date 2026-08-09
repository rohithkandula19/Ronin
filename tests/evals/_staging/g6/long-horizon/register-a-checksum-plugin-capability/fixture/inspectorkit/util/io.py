"""Reading files and fingerprinting their bytes."""

from __future__ import annotations

import zlib
from pathlib import Path

from ..blob import Blob
from ..errors import InspectorError


def read_blob(path: str | Path) -> Blob:
    target = Path(path)
    try:
        data = target.read_bytes()
    except FileNotFoundError as exc:
        raise InspectorError(f"no such file: {target}") from exc
    except IsADirectoryError as exc:
        raise InspectorError(f"{target} is a directory, not a file") from exc
    return Blob(name=str(target), data=data)


def fingerprint(data: bytes) -> str:
    """Ops-standard fingerprint: ``crc32:`` plus eight lower-case hex digits.

    CRC32 is not a security property; it is here so two runs over the same tree
    can be diffed cheaply and reproducibly on any machine.
    """
    return f"crc32:{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def walk_files(root: str | Path) -> list[Path]:
    """Every file under ``root``, sorted so a scan is reproducible."""
    base = Path(root)
    if base.is_file():
        return [base]
    return sorted(p for p in base.rglob("*") if p.is_file())
