# notedeck

A small notes backend, deliberately framework-free so the layering is visible.

```
serve.py / scripts/seed.py
        |
   web/  routes + error_map   translate HTTP-ish requests and domain errors
        |
services/  the rules          everything a caller must not be able to skip
        |
repository/  storage          dumb; no rules, no validation, no policy
        |
   domain/  records
```

## Where a rule goes

In the **service** layer. `scripts/seed.py` and the future importer talk to the
services directly and never see a route, so a rule enforced in `web/routes/` is a
rule that can be walked around. The repository is deliberately dumb: it stores
what it is handed, which is what makes it usable from migrations and fixtures.

The web layer's only job with a broken rule is translating the domain error into a
status code, in `notedeck/web/error_map.py`.

## Running it

```
python3 scripts/seed.py --out /tmp/seeded.json
python3 serve.py --requests scripts/example_requests.json --now 2024-01-01T00:00:00
python3 -m unittest discover -s tests -t .
```
