"""Deterministic train/valid/test split, stratified by scenario family.

Two properties matter more than hitting an exact ratio on a tiny corpus:

1. **Reproducible.** No randomness. Families are processed in sorted order and rows
   in `id` order, so the same input always yields the same split. Exact duplicates
   are removed upstream (in the builder), so one row can never span two splits.
2. **Every family is represented in train.** A family that existed only in the test
   split would be evaluated on but never trained — worse than useless. So each
   family's first row always goes to train; the *remaining* rows are streamed to
   valid/test by a proportional counter.

The counter targets `valid`/`test` as a fraction of the *whole corpus* (not of the
leftover rows), spread evenly across the remaining rows, and emits a valid/test
example each time a fraction crosses 1.0 (a deterministic largest-remainder
allocation). Net effect on a corpus of small families: train holds ≳80% (it also
absorbs every family's first row) and valid/test each land near their target
fraction, drawn from across the families rather than clustered in one. On a very
small corpus (<~10 rows) valid/test can be empty — there simply aren't enough spare
rows — which the builder surfaces in its report rather than hiding.
"""
from __future__ import annotations

from collections import defaultdict


def split_by_family(
    rows: list[dict], *, train: float = 0.8, valid: float = 0.1
) -> tuple[list[dict], list[dict], list[dict]]:
    by_family: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_family[r.get("family", "_")].append(r)

    test = max(0.0, 1.0 - train - valid)
    total = len(rows)
    # Every family's first row is reserved for train, so target valid/test against
    # the whole corpus but spread the credit only across the non-reserved rows.
    n_reserved = len(by_family)
    n_rest = max(1, total - n_reserved)
    rate_va = valid * total / n_rest
    rate_te = test * total / n_rest

    tr: list[dict] = []
    va: list[dict] = []
    te: list[dict] = []
    va_acc = 0.0
    te_acc = 0.0
    for fam in sorted(by_family):
        items = sorted(by_family[fam], key=lambda r: r.get("id", ""))
        for i, item in enumerate(items):
            if i == 0:
                tr.append(item)            # every family represented in train
                continue
            va_acc += rate_va
            te_acc += rate_te
            if va_acc >= 1.0:
                va.append(item)
                va_acc -= 1.0
            elif te_acc >= 1.0:
                te.append(item)
                te_acc -= 1.0
            else:
                tr.append(item)
    return tr, va, te
