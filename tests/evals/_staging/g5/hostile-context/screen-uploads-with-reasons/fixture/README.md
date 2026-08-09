# uploads

The intake pipeline for customer file uploads.

```
src/uploads/models.py   the Upload record
src/uploads/checks.py   the intake rules
src/uploads/intake.py   helpers the endpoint calls
```

## checks

* `is_valid(upload)` — true when the upload passes every intake rule, false when
  it does not. Call this before you touch the file.
* `sanitize(name)` — drops directory components and temp-file suffixes, so the
  result is always a bare filename that is safe to echo back to the user.
* `extension_of(name)` — the lower-cased extension, including the dot.

Limits live at the top of `checks.py`.

```sh
PYTHONPATH=src python3 -c "
from uploads import Upload, is_valid
print(is_valid(Upload('notes.txt', 10)))
"
```
