"""Beta access control: invite / waitlist / access-code / domain-allowlist / open."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AccessMode(str, Enum):
    invite_only = "invite_only"
    waitlist = "waitlist"
    access_code = "access_code"
    domain_allowlist = "domain_allowlist"
    open = "open"
    closed = "closed"


@dataclass
class AccessDecision:
    allowed: bool
    reason: str
    waitlisted: bool = False


class BetaAccess:
    """Backend-enforced registration gate. Defaults to a controlled mode, not open."""

    def __init__(self, mode: AccessMode = AccessMode.invite_only) -> None:
        self.mode = mode
        self._invites: set[str] = set()      # invited emails
        self._codes: dict[str, int] = {}     # code -> remaining uses
        self._domains: set[str] = set()      # allowed email domains
        self._waitlist: list[str] = []

    # ------------------------------------------------------------- config
    def add_invite(self, email: str) -> None:
        self._invites.add(email.lower())

    def add_code(self, code: str, uses: int = 1) -> None:
        self._codes[code] = uses

    def allow_domain(self, domain: str) -> None:
        self._domains.add(domain.lower())

    @property
    def waitlist(self) -> list[str]:
        return list(self._waitlist)

    # ------------------------------------------------------------- decide
    def evaluate(self, email: str, *, code: str = "") -> AccessDecision:
        email = email.lower().strip()
        if "@" not in email:
            return AccessDecision(False, "invalid email")
        domain = email.split("@", 1)[1]

        if self.mode is AccessMode.closed:
            return AccessDecision(False, "registration is closed")
        if self.mode is AccessMode.open:
            return AccessDecision(True, "open registration")
        if self.mode is AccessMode.invite_only:
            if email in self._invites:
                return AccessDecision(True, "invited")
            return AccessDecision(False, "invite required")
        if self.mode is AccessMode.access_code:
            if code and self._codes.get(code, 0) > 0:
                self._codes[code] -= 1
                return AccessDecision(True, "valid access code")
            return AccessDecision(False, "valid access code required")
        if self.mode is AccessMode.domain_allowlist:
            if domain in self._domains:
                return AccessDecision(True, f"allowed domain {domain}")
            return AccessDecision(False, f"domain {domain} not allowed")
        if self.mode is AccessMode.waitlist:
            if email not in self._waitlist:
                self._waitlist.append(email)
            return AccessDecision(False, "added to waitlist", waitlisted=True)
        return AccessDecision(False, "unknown mode")  # pragma: no cover
