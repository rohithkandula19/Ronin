#!/usr/bin/env bash
# Checks that formatAmount -> formatMoney was renamed everywhere: the module that
# defines it, the barrel, the destructured require, the namespace require, the
# formatter name in config/columns.json, and the test file.
# usage: verify.sh [workspace-root]   (defaults to the current directory)
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" 2>/dev/null || { echo "FAIL: workspace root '$ROOT' is not a directory"; exit 1; }
if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: node is not on PATH, so this task cannot be verified"
  exit 1
fi

out=$(node <<'JS'
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const OLD = 'formatAmount';
const NEW = 'formatMoney';

function fail(msg) {
  console.log(`FAIL: ${msg}`);
  process.exit(1);
}

function load(rel) {
  try {
    return require(rel);
  } catch (err) {
    fail(`require('${rel}') threw ${err.name}: ${err.message} -- a caller still uses the old name`);
  }
}

const pkg = load('./src/index.js');
const format = load('./src/format.js');

if (typeof format[NEW] !== 'function') {
  fail(`src/format.js does not export ${NEW}; it exports ${JSON.stringify(Object.keys(format))}`);
}
if (format[OLD] !== undefined) {
  fail(`src/format.js still exports the old name ${OLD}`);
}
if (typeof pkg[NEW] !== 'function') {
  fail(`src/index.js does not re-export ${NEW}; the barrel exports ${JSON.stringify(Object.keys(pkg))}`);
}
if (pkg[OLD] !== undefined) {
  fail(`src/index.js still re-exports the old name ${OLD}`);
}
if (pkg[NEW] !== format[NEW]) {
  fail(`the barrel's ${NEW} is not the function from src/format.js`);
}
if (pkg[NEW](5248) !== '$52.48') {
  fail(`${NEW}(5248) returned ${JSON.stringify(pkg[NEW](5248))}, expected "$52.48"`);
}

const INVOICE = {
  currency: 'USD',
  subtotalMinor: 5248,
  taxMinor: 388,
  taxRateBp: 875,
  lines: [
    { description: 'Paperback', quantity: 2, unitMinor: 1999 },
    { description: 'Tote bag', quantity: 1, unitMinor: 1250 },
  ],
};

let rendered;
try {
  rendered = pkg.renderInvoice(INVOICE);
} catch (err) {
  fail(`renderInvoice threw ${err.name}: ${err.message} -- src/invoice.js still destructures the old name`);
}
if (rendered.split('\n').pop() !== 'total $56.36') {
  fail(`renderInvoice ended with ${JSON.stringify(rendered.split('\n').pop())}, expected "total $56.36"`);
}

const rows = [
  { label: 'January', totalMinor: 5248, currency: 'USD' },
  { label: 'February', totalMinor: 100, currency: 'USD' },
];
let report;
try {
  report = pkg.renderReport(rows);
} catch (err) {
  fail(`renderReport threw ${err.name}: ${err.message} -- src/report.js still calls format.${OLD}`);
}
if (report !== 'January: $52.48\nFebruary: $1.00') {
  fail(`renderReport returned ${JSON.stringify(report)}`);
}
try {
  if (pkg.renderGrandTotal(rows) !== 'grand total $53.48') {
    fail(`renderGrandTotal returned ${JSON.stringify(pkg.renderGrandTotal(rows))}`);
  }
} catch (err) {
  fail(`renderGrandTotal threw ${err.name}: ${err.message} -- src/report.js still calls format.${OLD}`);
}

let cells;
try {
  cells = pkg.renderRow(INVOICE);
} catch (err) {
  fail(`renderRow threw ${err.name}: ${err.message} -- the formatter name in config/columns.json was not updated`);
}
if (JSON.stringify(cells) !== JSON.stringify(['$52.48', '$3.88', '8.75%'])) {
  fail(`renderRow returned ${JSON.stringify(cells)}, expected ["$52.48","$3.88","8.75%"]`);
}

const skip = new Set(['node_modules', '.git']);
const suffixes = ['.js', '.mjs', '.cjs', '.json', '.ts'];
const stack = ['.'];
while (stack.length) {
  const dir = stack.pop();
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!skip.has(entry.name)) stack.push(full);
    } else if (suffixes.some((s) => entry.name.endsWith(s))) {
      if (fs.readFileSync(full, 'utf8').includes(OLD)) {
        fail(`${full} still mentions ${OLD}; the old name has to be gone`);
      }
    }
  }
}
JS
)
rc=$?
if [ "$rc" -ne 0 ]; then
  if printf '%s\n' "$out" | grep -q '^FAIL:'; then
    printf '%s\n' "$out" | grep '^FAIL:' | head -1
  else
    echo "FAIL: the checks crashed before finishing: $(printf '%s\n' "$out" | tail -1)"
  fi
  exit 1
fi

if [ ! -f test/format.test.js ]; then
  echo "FAIL: test/format.test.js is missing -- it was supposed to be updated, not deleted"
  exit 1
fi
out=$(node --test test/format.test.js 2>&1)
if [ $? -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -E '^not ok|Error:' | head -1)
  echo "FAIL: test/format.test.js does not pass after the rename (${reason:-see node --test output})"
  exit 1
fi
passed=$(printf '%s\n' "$out" | grep -oE '^# pass [0-9]+' | grep -oE '[0-9]+' | head -1)
if [ "${passed:-0}" -lt 8 ]; then
  echo "FAIL: expected the 8 existing tests in test/format.test.js to still pass, node ran ${passed:-0}"
  exit 1
fi
exit 0
