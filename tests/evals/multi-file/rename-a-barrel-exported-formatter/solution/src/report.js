'use strict';

const format = require('./format');

/**
 * The monthly revenue summary: one label/amount pair per row.
 */
function renderReport(rows) {
  return rows
    .map((row) => `${row.label}: ${format.formatMoney(row.totalMinor, row.currency)}`)
    .join('\n');
}

/**
 * Grand total across every row. Rows are assumed to share a currency.
 */
function renderGrandTotal(rows, currency = 'USD') {
  const total = rows.reduce((sum, row) => sum + row.totalMinor, 0);
  return `grand total ${format.formatMoney(total, currency)}`;
}

module.exports = { renderGrandTotal, renderReport };
