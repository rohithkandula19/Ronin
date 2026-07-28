# Release Automation

Ronin releases are tag-driven. A tag is the explicit approval boundary; this
automation prepares and validates a release, but it does not create a tag unless
the maintainer runs the release command.

## Release Manifest

The release manifest is implemented in `ronin_cli.release` and includes these
published distributions:

- `ronin-agent-patterns`
- `ronin-eval-suite`
- `ronin-memory`
- `ronin-hardening`
- `ronin-mcp-servers`
- `ronin-cli`
- `ronin-relay`

The root version, each package version, the CLI and relay `__version__` values,
and the CLI's five exact internal dependency pins must agree with the tag. A
final tag such as `v1.2.3` maps to `1.2.3`; a release-candidate tag such as
`v1.2.3-rc.1` maps to the PEP 440 version `1.2.3rc1`.

## Maintainer Flow

Run the non-mutating gate first from a clean `main` checkout:

```bash
./scripts/release.sh 1.0.1 --dry-run
```

The dry run checks the working tree, branch, and full release test suite. It
does not edit files, create a tag, contact GitHub, or publish a package.

After reviewing the result and changelog, run the same command without
`--dry-run`:

```bash
./scripts/release.sh 1.0.1
```

The script synchronizes the release manifest, updates `CHANGELOG.md`, commits
the preparation, pushes `main`, creates `v1.0.1`, and watches the tag workflow.
It accepts either `1.0.1` or `v1.0.1`; prerelease tags use the friendly form
`v1.1.0-rc.1`.

## Workflow Gates

`.github/workflows/release.yml` runs for a `v*` tag, or manually for an
existing tag. It performs these ordered gates:

1. Validate the tag, synchronized source versions, and exact CLI dependency pins.
2. Run the release test suite.
3. Build every release-manifest package and verify one wheel plus one sdist for each.
4. Run `twine check` and the clean standalone CLI installation smoke test.
5. Generate `dist/SHA256SUMS`, upload the artifacts, and attach them to the GitHub Release.
6. Publish to PyPI only when the `PYPI_TOKEN` repository secret is configured.

The GitHub Release and its checksummed artifacts are created even when PyPI
publishing is intentionally disabled. A manual workflow dispatch checks out the
specified existing tag; it cannot turn an arbitrary branch tip into a release.

## Rollback

Before the tag is pushed, amend or discard the local release-preparation commit.
After the tag exists, do not retag it: publish a corrective version instead.
GitHub Release notes and attachments can be edited or replaced, while PyPI
versions are immutable. If a published PyPI release must be withdrawn, yank the
affected distributions and cut a corrected version; never overwrite artifacts
or checksum files for the same version.

## Migration Note

The former `scripts/release.sh` flow still accepted a bare version, but it was
labelled `csk` and only bumped `ronin-cli`. It now uses the shared release
manifest, synchronizes all published distributions and CLI pins, and keeps a
truthful no-write dry run. Existing invocations such as
`./scripts/release.sh 1.0.1` remain valid.
