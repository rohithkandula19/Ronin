# Layer rules of thumb

* A route may: read the request, coerce types, call one service method, shape the
  response. It may not decide anything.
* A service may: validate, enforce policy, coordinate repositories, raise domain
  errors from `notedeck/errors.py`. It never knows about status codes.
* A repository may: put records in and take them out, in a deterministic order. It
  raises `NotFound` for a missing key and nothing else.
* Numbers that an operator might want to change live in `config/app.json` and are
  read through `notedeck/config.py`. Nothing else parses that file.

Status codes are a web concern: `notedeck/web/error_map.py` is the only file that
knows one. It matches the most specific mapped error class first, so a new error
that subclasses an existing one needs its own entry to get its own status.
