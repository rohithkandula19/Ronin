"""A Retainer: Ronin kept in service rather than summoned.

``docs/RETAINER.md`` is the contract this package implements. The short version:
a Retainer is a **durable record, not a running process**. It has a name, a post
it holds, standing orders that say what it may do, and a way to be reached. A
process exists only while one turn runs — wake on a summons, restore, run,
persist, release.

The package is deliberately layered so the parts with no I/O can be tested
without any: :mod:`ronin.retainer.model` is frozen dataclasses and enums with no
Ronin imports beyond one enum, and everything that touches a network, a clock,
or a disk sits above it.
"""

from __future__ import annotations
