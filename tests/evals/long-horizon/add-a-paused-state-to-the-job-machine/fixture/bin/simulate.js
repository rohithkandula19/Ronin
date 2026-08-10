#!/usr/bin/env node
/**
 * Drive one job through a scripted list of events and print what happened.
 *
 *   node bin/simulate.js fixtures/scenario-happy.json
 */

import { readFileSync } from 'node:fs';
import { argv, exit, stdout } from 'node:process';

import { defaultContext, drive } from '../src/machine.js';
import { renderHistory } from '../src/history.js';
import { board, legend } from '../src/render.js';
import { newJob } from '../src/store.js';

function usage(message) {
  stdout.write(`${message}\nusage: node bin/simulate.js <scenario.json>\n`);
  exit(2);
}

const path = argv[2];
if (path === undefined) {
  usage('no scenario given');
}

let scenario;
try {
  scenario = JSON.parse(readFileSync(path, 'utf8'));
} catch (error) {
  usage(`cannot read ${path}: ${error.message}`);
}

const spec = scenario.job ?? {};
const job = newJob(spec.id, spec);
const context = defaultContext(scenario.context ?? {});
const result = drive(job, scenario.events ?? [], context);

stdout.write(`scenario: ${scenario.name ?? path}\n`);
stdout.write(`${renderHistory(result.history)}\n\n`);
stdout.write(`${board([result.job])}\n\n`);
stdout.write(`final: ${result.job.state}\n`);
if (result.error !== undefined) {
  stdout.write(`stopped: ${result.error.name}: ${result.error.message}\n`);
}
if (scenario.showLegend) {
  stdout.write(`\n${legend()}\n`);
}
exit(result.error === undefined ? 0 : 1);
