#!/usr/bin/env bash
# Checks the paused state reached every layer of the machine: the state list, the
# event list, the transition table, the guard registry and the renderer.
# Prints one FAIL line naming the layer at fault.
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" || { echo "FAIL [harness] cannot enter workspace $ROOT"; exit 1; }
if ! command -v node >/dev/null 2>&1; then
  echo "FAIL [harness] node is not on PATH; this fixture is a node project"
  exit 1
fi
TMP="$(mktemp -d "$ROOT/.verify_tmp.XXXXXX")" || { echo "FAIL [harness] cannot create temp dir"; exit 1; }
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/check.mjs" <<'JSCODE'
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { argv, exit, stdout } from 'node:process';
import { pathToFileURL } from 'node:url';

const ROOT = argv[2];
const TMP = argv[3];

function fail(layer, message) {
  stdout.write(`FAIL [${layer}] ${message}\n`);
  exit(1);
}

async function load(relative, layer) {
  try {
    return await import(pathToFileURL(`${ROOT}/${relative}`).href);
  } catch (error) {
    fail(layer, `${relative} does not import: ${error.message}`);
  }
}

function runNode(args) {
  try {
    return {
      code: 0,
      out: execFileSync(process.execPath, args, { cwd: ROOT, encoding: 'utf8' }),
    };
  } catch (error) {
    return {
      code: error.status ?? 1,
      out: `${error.stdout ?? ''}${error.stderr ?? ''}`,
    };
  }
}

// ------------------------------------------------------------------ states layer
const states = await load('src/states.js', 'states');
if (!states.STATES.includes('paused')) {
  fail('states', `'paused' is not a known state: src/states.js lists ${states.STATES.join(', ')}`);
}
if (states.isFinished('paused')) {
  fail(
    'states',
    "'paused' is listed as a finished state; a paused job must still be able to move, so it " +
      'does not belong in FINISHED',
  );
}

// ------------------------------------------------------------------ events layer
const events = await load('src/events.js', 'events');
for (const name of ['pause', 'resume']) {
  if (!events.EVENTS.includes(name)) {
    fail('events', `'${name}' is not a known event: src/events.js lists ${events.EVENTS.join(', ')}`);
  }
}

// ------------------------------------------------------------------- table layer
const table = await load('src/table.js', 'table');
const wanted = [
  ['running', 'pause', 'paused'],
  ['paused', 'resume', 'running'],
  ['paused', 'cancel', 'cancelled'],
];
for (const [from, event, to] of wanted) {
  const row = table.transitionFor(from, event);
  if (row === undefined) {
    fail(
      'table',
      `the transition table has no row for ${from} --${event}--> ${to}; rows from '${from}' are ` +
        `${table.eventsFrom(from).join(', ') || '(none)'}`,
    );
  }
  if (row.to !== to) {
    fail('table', `${from} --${event}--> lands in '${row.to}', expected '${to}'`);
  }
}
if (table.transitionFor('succeeded', 'pause') !== undefined) {
  fail('table', 'a succeeded job can be paused; finished states must have no way out');
}

// ------------------------------------------------------------------ guards layer
const pauseRow = table.transitionFor('running', 'pause');
const guards = await load('src/guards.js', 'guards');
if (typeof pauseRow.guard !== 'string' || pauseRow.guard.length === 0) {
  fail(
    'guards',
    'the running --pause--> paused row declares no guard, so the critical-job rule is not ' +
      `expressed as a named guard; guards defined in src/guards.js are ${Object.keys(guards.GUARDS).join(', ')}`,
  );
}
if (typeof guards.GUARDS[pauseRow.guard] !== 'function') {
  fail(
    'guards',
    `the pause row names guard '${pauseRow.guard}', which src/guards.js does not define ` +
      `(it defines ${Object.keys(guards.GUARDS).join(', ')})`,
  );
}

const machine = await load('src/machine.js', 'guards');
const errors = await load('src/errors.js', 'guards');
const store = await load('src/store.js', 'guards');

const normal = store.newJob('job-normal', { state: 'running', priority: 'normal' });
let paused;
try {
  paused = machine.apply(normal, 'pause');
} catch (error) {
  fail('guards', `pausing a running normal-priority job threw ${error.name}: ${error.message}`);
}
if (paused.state !== 'paused') {
  fail('guards', `pausing a running job produced state '${paused.state}', expected 'paused'`);
}

const critical = store.newJob('job-critical', { state: 'running', priority: 'critical' });
let refusal;
try {
  machine.apply(critical, 'pause');
} catch (error) {
  refusal = error;
}
if (refusal === undefined) {
  fail('guards', 'a critical job was allowed to pause; the guard must refuse it');
}
if (!(refusal instanceof errors.GuardRejected)) {
  fail(
    'guards',
    `pausing a critical job threw ${refusal.name}, expected GuardRejected — the refusal is a ` +
      'condition on a legal transition, not an unknown transition',
  );
}

if (machine.apply(paused, 'resume').state !== 'running') {
  fail('guards', 'resuming a paused job did not return it to running');
}
if (machine.apply(paused, 'cancel').state !== 'cancelled') {
  fail('guards', 'a paused job could not be cancelled');
}

// ------------------------------------------------------------------ render layer
const render = await load('src/render.js', 'render');
let symbol;
try {
  symbol = render.symbolFor('paused');
} catch (error) {
  fail('render', `the board has no symbol for a paused job: ${error.message}`);
}
if (typeof symbol !== 'string' || symbol.length === 0) {
  fail('render', `the symbol for 'paused' is ${JSON.stringify(symbol)}`);
}
const symbols = states.STATES.map((state) => render.SYMBOLS[state]);
if (new Set(symbols).size !== symbols.length) {
  fail('render', `the paused symbol is not unique: the board would print ${symbols.join('')}`);
}
let label;
try {
  label = render.labelFor('paused');
} catch (error) {
  fail('render', `the legend has no label for a paused job: ${error.message}`);
}
const legend = render.legend();
if (legend.split('\n').length !== states.STATES.length || !legend.includes(label)) {
  fail(
    'render',
    `the legend does not describe the paused state; it reads:\n${legend.replace(/\n/g, ' | ')}`,
  );
}
try {
  const rendered = render.board([paused]);
  if (!rendered.includes('paused')) {
    fail('render', `the board does not show the paused job: ${rendered.replace(/\n/g, ' | ')}`);
  }
} catch (error) {
  fail('render', `rendering a board with a paused job threw ${error.name}: ${error.message}`);
}

// -------------------------------------------------------------------- end to end
const happy = `${TMP}/scenario-pause.json`;
writeFileSync(
  happy,
  JSON.stringify({
    name: 'paused then resumed',
    job: { id: 'job-9', priority: 'high', label: 'nightly rollup' },
    events: ['start', 'pause', 'resume', 'succeed'],
    showLegend: true,
  }),
);
let result = runNode(['bin/simulate.js', happy]);
if (result.code !== 0) {
  fail(
    'end-to-end',
    `simulating start/pause/resume/succeed exited ${result.code}: ${result.out.trim().split('\n').slice(-1)[0]}`,
  );
}
if (!result.out.includes('running --pause--> paused') || !result.out.includes('final: succeeded')) {
  fail(
    'end-to-end',
    `bin/simulate.js did not report the pause and the finish: ${result.out.trim().replace(/\n/g, ' | ').slice(-220)}`,
  );
}

const blocked = `${TMP}/scenario-critical.json`;
writeFileSync(
  blocked,
  JSON.stringify({
    name: 'critical job cannot pause',
    job: { id: 'job-10', priority: 'critical', label: 'pager feed' },
    events: ['start', 'pause'],
  }),
);
result = runNode(['bin/simulate.js', blocked]);
if (result.code === 0 || !/GuardRejected/.test(result.out)) {
  fail(
    'end-to-end',
    `simulating a pause on a critical job exited ${result.code} without a GuardRejected: ` +
      `${result.out.trim().replace(/\n/g, ' | ').slice(-220)}`,
  );
}

result = runNode(['bin/dot-export.js']);
if (result.code !== 0 || !/"running" -> "paused"/.test(result.out)) {
  fail(
    'end-to-end',
    `bin/dot-export.js does not draw the pause edge (exit ${result.code}); its output was ` +
      `${result.out.trim().replace(/\n/g, ' | ').slice(-220)}`,
  );
}

// ---------------------------------------------------------------------- the tests
result = runNode(['--test']);
if (result.code !== 0) {
  const line = result.out
    .split('\n')
    .filter((entry) => /not ok|Error|AssertionError/.test(entry))
    .slice(0, 1);
  fail('tests', `the project's own test suite no longer passes: ${line[0] ?? 'see node --test'}`);
}

stdout.write('OK paused wired through states, events, table, guards and the renderer\n');
JSCODE

node "$TMP/check.mjs" "$ROOT" "$TMP"
