"""Extensions: the surfaces a user adds to Ronin without editing Ronin.

Three things live here, and they share one idea — *composition over a checked-in
directory*. Skills are saved workflows discovered from ``.ronin/skills``; plugins bundle
skills, MCP servers, subagents, commands and hooks behind one manifest; and the trust
tiers decide what a bundle from a stranger may do before its shell hooks can fire.

The package sits **above** the surface layers it composes — it imports
:mod:`ronin.agents`, :mod:`ronin.mcp`, :mod:`ronin.ui` and :mod:`ronin.tools` to turn a
manifest into their types — and **below** ``cli``, which wires the result into a live
session. It never imports a provider: a skill is instructions and a plugin is a bundle,
and neither has any business knowing which model is reading it.
"""

from __future__ import annotations
