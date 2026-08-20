# invsync

One-way inventory sync, split so each stage is a pure function.

* `invsync/records.py` — the immutable `Record`.
* `invsync/mapping.py` — header aliases from the supplier feeds.
* `invsync/source_csv.py` — CSV text into records.
* `invsync/diff.py` — `compute(current, incoming) -> Diff`.
* `invsync/apply.py` — a `Diff` applied to an indexed target.
* `invsync/report.py` — the human-readable summary.
* `invsync/cli.py` — argument handling for `bin/invsync`.

Every stage is pure and every input is checked in, so the whole sync runs
offline and produces byte-identical output every time.
