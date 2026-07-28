# Coding Engine Verification

Ronin verifies a proposed source file before `write_file`, `edit_file`, or
`multi_edit` changes it on disk. This is a fast structural preflight at the
edit boundary, not a substitute for the repository's tests.

## What Is Checked

- **Python and stubs** (`.py`, `.pyi`): parsed with Python's standard AST. A
  syntax error rejects the edit and leaves the file unchanged.
- **TypeScript** (`.ts`, `.tsx`, `.mts`, `.cts`): parsed only when the project
  has a local `typescript` package available to Node. Ronin sends the proposed
  source to the parser without evaluating it. If the parser is unavailable, the
  edit continues with an explicit `syntax not checked` result.
- **Other files**: left unchanged by this preflight. Their project-native
  formatter, compiler, or test suite remains the verification source.

For Python, Ronin also compares top-level public functions/classes before and
after an edit. A removed public symbol, including one listed in a literal
`__all__`, is reported in the tool result. It is a warning rather than a block:
removing an API can be intentional, but it should not be invisible.

## Limits

Passing this check means only that the proposed source parsed. It does not prove
types, imports, runtime behavior, compatibility, formatting, or tests. Run the
repository's verification command after meaningful changes; Ronin's existing
self-verification and pipeline checks remain responsible for that evidence.
