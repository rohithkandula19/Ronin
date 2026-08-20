#!/usr/bin/env node
/**
 * Draw the machine straight from the transition table, so the picture cannot drift.
 *
 *   node bin/dot-export.js
 */

import { stdout } from 'node:process';

import { STATES, isFinished } from '../src/states.js';
import { TRANSITIONS } from '../src/table.js';

const lines = ['digraph flowdeck {', '  rankdir=LR;', '  node [shape=box, fontname="monospace"];'];

for (const state of STATES) {
  const shape = isFinished(state) ? 'doublecircle' : 'box';
  lines.push(`  "${state}" [shape=${shape}];`);
}

for (const row of TRANSITIONS) {
  const label = row.guard === undefined ? row.event : `${row.event} [${row.guard}]`;
  lines.push(`  "${row.from}" -> "${row.to}" [label="${label}"];`);
}

lines.push('}');
stdout.write(`${lines.join('\n')}\n`);
