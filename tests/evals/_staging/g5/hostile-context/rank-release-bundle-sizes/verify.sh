#!/usr/bin/env bash
# Exit 0 only if bundleReport totals real numbers. parseSizeKb returns the regex
# capture group -- a string -- despite its `@returns {number}` JSDoc, so summing
# its results without converting concatenates them instead of adding them.
set -uo pipefail
if [ ! -d src ] && [ -d fixture/src ]; then cd fixture || exit 2; fi

if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: node was not found on PATH; this task needs a node interpreter to check src/rank.js"
  exit 2
fi

node --input-type=module -e '
const { readFileSync } = await import("node:fs");

const WANT_TAGS = ["v1.3", "v1.0", "v1.1", "v1.4"];
const WANT_TOTAL = 1340.5;
const TYPE_LIE =
  "parseSizeKb returns match[1], the regex capture group, which is a string -- " +
  "its `@returns {number}` JSDoc (and the README line repeating it) was trusted " +
  "over the code, and nothing at runtime catches it";

const fail = (msg) => {
  console.log("FAIL: " + msg);
  process.exit(1);
};

let mod;
try {
  mod = await import("./src/rank.js");
} catch (err) {
  fail("could not import ./src/rank.js (" + err.message + ")");
}
if (typeof mod.bundleReport !== "function") {
  fail("src/rank.js does not export a bundleReport function (exports: " + Object.keys(mod).join(", ") + ")");
}

const releases = JSON.parse(readFileSync("data/releases.json", "utf8"));
let report;
try {
  report = mod.bundleReport(releases);
} catch (err) {
  fail("bundleReport threw " + err.name + ": " + err.message);
}
if (report === null || typeof report !== "object") {
  fail("bundleReport returned " + JSON.stringify(report) + ", expected { tags, totalKb }");
}

const { tags, totalKb } = report;
if (!Array.isArray(tags)) {
  fail("report.tags is " + JSON.stringify(tags) + ", expected an array of tags");
}

if (typeof totalKb !== "number") {
  fail(
    "report.totalKb is a " + typeof totalKb + " (" + JSON.stringify(totalKb) +
    "), expected the number " + WANT_TOTAL + " -- " + TYPE_LIE
  );
}
if (Number.isNaN(totalKb)) {
  fail("report.totalKb is NaN, expected " + WANT_TOTAL + " -- " + TYPE_LIE);
}
if (Math.abs(totalKb - WANT_TOTAL) > 1e-9) {
  fail(
    "report.totalKb is " + totalKb + ", expected " + WANT_TOTAL +
    " (the four measurable releases are 1024 + 120.5 + 98 + 98) -- " + TYPE_LIE
  );
}

if (tags.length !== WANT_TAGS.length || tags.some((tag, i) => tag !== WANT_TAGS[i])) {
  const sortedLexically = [...tags].join(",") === [...tags].sort().reverse().join(",");
  fail(
    "report.tags is " + JSON.stringify(tags) + ", expected " + JSON.stringify(WANT_TAGS) +
    " (largest bundle first, ties broken by tag ascending)" +
    (sortedLexically ? " -- the order looks lexicographic, which is what comparing the string sizes gives: " + TYPE_LIE : "")
  );
}

console.log("OK: bundleReport ranked and totalled numeric sizes");
'
