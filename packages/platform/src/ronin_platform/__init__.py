"""Ronin AI OS beta platform plumbing.

Feature flags, rate limiting, beta access, and environment validation — the
operational controls a controlled public beta needs. Design rules, each tested:

- **Security is never flag-dependent:** flags gate product exposure, not safety.
  A dedicated test asserts no security/floor decision reads a feature flag.
- **Backend enforcement:** flags/access/limits are evaluated server-side.
- **Fail closed:** environment validation refuses to start when production
  config is unsafe (default keys, debug/demo on, unlimited spend, local DB in
  prod, unsafe CORS).
"""

from .flags import Flag, FlagStore, FlagScope
from .ratelimit import RateLimiter, RateLimit, RateLimitExceeded
from .access import BetaAccess, AccessMode, AccessDecision
from .envcheck import EnvIssue, check_environment

__all__ = [
    "AccessDecision",
    "AccessMode",
    "BetaAccess",
    "EnvIssue",
    "Flag",
    "FlagScope",
    "FlagStore",
    "RateLimit",
    "RateLimitExceeded",
    "RateLimiter",
    "check_environment",
]
