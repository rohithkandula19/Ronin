# ledger-etl

A small batch importer that turns raw order exports from the shop systems into the
canonical order feed that finance reads.

## Shape

```
source file --> reader --> transforms --> filters --> validator --> writer --> feed
```

* `etl/schema.py` declares the canonical record: one `Field` per column, with its
  kind and (optionally) the name of a config allowlist that constrains it. Every
  other layer is driven by this declaration, so a new column starts here.
* `etl/config.py` wraps `config/pipeline.json`. Allowlists are looked up by name
  (`Config.allowlist("channels")`), so adding a new allowlist is a config-file
  change only.
* `etl/readers/` turn a source file into raw string rows. Each reader owns the
  mapping from *its* source columns to field names, because every shop system
  names its columns differently. `etl/readers/jsonl_reader.py` re-imports our own
  feed, so it is schema-driven and has no mapping of its own.
* `etl/transforms/` clean values (whitespace, case, money formatting) *before*
  validation runs. Filters (`dedupe`) drop whole rows.
* `etl/validate.py` decides whether a row is acceptable. It reports `RowError`s
  rather than raising, so one bad row never kills a run.
* `etl/writers/jsonl_writer.py` writes the feed. It pins an explicit column order
  because downstream jobs diff the files.

## Running it

```
python3 run_import.py --source data/orders_sample.csv --out out/orders.jsonl
python3 run_report.py --feed out/orders.jsonl
python3 -m unittest discover -s tests -t .
```
