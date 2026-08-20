'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');

const pricing = require('../src/index.js');
const { formatAmount } = require('../src/format.js');
const { renderRow } = require('../src/columns.js');

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

test('minor units render with two decimals', () => {
  assert.equal(formatAmount(5248), '$52.48');
  assert.equal(formatAmount(5, 'EUR'), '€0.05');
  assert.equal(formatAmount(-125), '-$1.25');
});

test('the barrel re-exports the same function', () => {
  assert.equal(pricing.formatAmount, formatAmount);
});

test('a non-integer amount is rejected', () => {
  assert.throws(() => formatAmount(12.5), TypeError);
});

test('an unknown currency is rejected', () => {
  assert.throws(() => formatAmount(100, 'XTS'), RangeError);
});

test('basis points render as a percentage', () => {
  assert.equal(pricing.formatPercent(875), '8.75%');
});

test('an invoice ends with its total', () => {
  const rendered = pricing.renderInvoice(INVOICE);
  assert.equal(rendered.split('\n').pop(), 'total $56.36');
});

test('the report uses the module namespace import', () => {
  const rows = [
    { label: 'January', totalMinor: 5248, currency: 'USD' },
    { label: 'February', totalMinor: 100, currency: 'USD' },
  ];
  assert.equal(pricing.renderReport(rows), 'January: $52.48\nFebruary: $1.00');
  assert.equal(pricing.renderGrandTotal(rows), 'grand total $53.48');
});

test('configured columns resolve their formatter by name', () => {
  assert.deepEqual(renderRow(INVOICE), ['$52.48', '$3.88', '8.75%']);
});
