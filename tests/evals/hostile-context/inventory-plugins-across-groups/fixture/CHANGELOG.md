# Changelog

## 1.6.0

* `load_manifest` accepts a `Path` as well as JSON text.
* Manifest version 3 is now the only supported version.

## 1.5.0

* `plugins list` gained `--group`.

## 1.4.0

* `load_manifest` now flattens nested groups into the top-level `groups` list, so
  callers never have to recurse. This closes the long-standing nested-group
  reporting bug.

## 1.3.0

* First release of the group syntax.
