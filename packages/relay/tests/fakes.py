"""In-process fakes used by the round-trip tests.

FakeResponse / FakeLocalCaller stand in for httpx against the real Ronin
gateway. The caller RECORDS the url it was asked to hit, so a test can prove
the connector actually called the configured target with the right path and
body. A connector stub that skipped the call would leave these unrecorded and
return the wrong status, failing the assertions.
"""

from __future__ import annotations

from typing import Any


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload


class FakeLocalCaller:
    """Records calls and returns a canned response.

    Acts as the local Ronin gateway. By default it echoes back the body it
    received, prefixed, so the test can confirm the body genuinely traveled
    phone -> relay -> connector -> target and back.
    """

    def __init__(self, status_code: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_code = status_code

    async def request(
        self, method: str, url: str, *, json: Any, headers: dict[str, str]
    ) -> FakeResponse:
        self.calls.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        return FakeResponse(
            self.status_code,
            {"echo": json, "seen_path": url},
        )


class BrokenLocalCaller:
    """Simulates the local target being down (connector cannot reach it)."""

    async def request(self, method: str, url: str, *, json: Any, headers: dict[str, str]):
        raise ConnectionError("connection refused")
