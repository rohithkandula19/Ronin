# catalog

Tiny toolkit behind the docs site.

```
src/catalog/loader.py   read raw records from a JSON file
src/catalog/index.py    turn raw records into the searchable index
src/catalog/export.py   write index views for downstream tools
data/records.json       the records the site is built from
```

Run anything in here with `src` on the path:

```sh
PYTHONPATH=src python3 -c "import catalog; print(catalog.load_records('data/records.json'))"
```
