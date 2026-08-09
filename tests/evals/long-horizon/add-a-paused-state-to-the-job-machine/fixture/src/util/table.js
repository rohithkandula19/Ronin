/** Fixed-width table rendering, so board output diffs cleanly between runs. */

import { pad } from './text.js';

export function renderTable(headers, rows) {
  const widths = headers.map((header, index) =>
    Math.max(header.length, ...rows.map((row) => String(row[index] ?? '').length), 1),
  );
  const line = (cells) =>
    cells.map((cell, index) => pad(cell ?? '', widths[index])).join('  ').trimEnd();
  return [line(headers), line(widths.map((width) => '-'.repeat(width))), ...rows.map(line)].join('\n');
}
