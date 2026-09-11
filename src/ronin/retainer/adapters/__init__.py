"""Adapters: the only place that knows what GitHub, Slack or Telegram look like.

Each one does two things and nothing else — turn a delivery into a
:class:`~ronin.retainer.model.Summons`, and turn an answer back into a message
that platform understands. Everything between those two points is the same code
regardless of where the mention came from, which is what makes the second and
third adapter cheap rather than three times the work.

No adapter opens a socket. Posting is an injected callable, so the whole of this
package is testable offline — which the repository requires of every test.
"""

from __future__ import annotations
