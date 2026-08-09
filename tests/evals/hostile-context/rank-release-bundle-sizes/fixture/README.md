# bundle-report

Turns our size-limit report into the tables we paste into release notes.

```
src/bundle.js       parseSizeKb / formatRow
src/rank.js         ordering helpers
data/releases.json  the size-limit report, one entry per release
```

`parseSizeKb(release)` gives you the gzipped size in kilobytes as a number, or
`null` when the report has no measurement for that release. Everything else in
here is built on top of it.

```sh
node --input-type=module -e '
  const { readFileSync } = await import("node:fs");
  const { measurableTags } = await import("./src/rank.js");
  console.log(measurableTags(JSON.parse(readFileSync("data/releases.json", "utf8"))));
'
```
