import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const tokensTs = readFileSync(join(here, "tokens.ts"), "utf8");
const css = readFileSync(join(here, "..", "styles", "rds.css"), "utf8");

// Critical palette values that MUST be identical in tokens.ts and rds.css.
// tokens.ts is the source of truth; rds.css mirrors it. This test is the
// drift guard between the two.
const MIRROR = [
  ["clay500", "#a98467", "--rds-clay-500"],
  ["clay300", "#d4a373", "--rds-clay-300"],
  ["clay700", "#8a6a4f", "--rds-clay-700"],
  ["paper50", "#fafaf9", "--rds-paper-50"],
  ["ink900", "#1a1a1a", "--rds-ink-900"],
  ["success", "#4f7a5b", "--rds-success"],
  ["warn", "#b8863b", "--rds-warn"],
  ["danger", "#b4462f", "--rds-danger"],
  ["info", "#4a6b82", "--rds-info"],
];

for (const [tsKey, hex, cssVar] of MIRROR) {
  test(`${tsKey} = ${hex} in both tokens.ts and rds.css`, () => {
    assert.match(tokensTs, new RegExp(`${tsKey}:\\s*"${hex}"`), `tokens.ts missing ${tsKey}`);
    assert.match(css, new RegExp(`${cssVar}:\\s*${hex}`), `rds.css missing ${cssVar}`);
  });
}

test("all four themes are defined in rds.css", () => {
  assert.match(css, /\[data-theme="dark"\]/);
  assert.match(css, /\[data-theme="oled"\]/);
  assert.match(css, /\[data-theme="hc"\]/);
  // light is :root default
  assert.match(css, /--rds-bg:\s*var\(--rds-paper-50\)/);
});

test("semantic role variables exist for theming", () => {
  for (const v of ["--rds-bg", "--rds-surface", "--rds-border", "--rds-text", "--rds-text-dim", "--rds-accent"]) {
    assert.ok(css.includes(v), `rds.css missing ${v}`);
  }
});

test("high-contrast theme uses pure black borders and text", () => {
  const hc = css.slice(css.indexOf('[data-theme="hc"]'));
  assert.match(hc, /--rds-border:\s*#000000/);
  assert.match(hc, /--rds-text:\s*#000000/);
});
