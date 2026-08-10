'use strict';

const { formatMoney, formatPercent } = require('./format');

/**
 * A receipt, one line per item plus the totals block.
 */
function renderInvoice(invoice) {
  const lines = invoice.lines.map(
    (line) => `${line.quantity} x ${line.description}  ${formatMoney(line.unitMinor, invoice.currency)}`,
  );
  lines.push(`subtotal ${formatMoney(invoice.subtotalMinor, invoice.currency)}`);
  lines.push(`tax ${formatMoney(invoice.taxMinor, invoice.currency)} (${formatPercent(invoice.taxRateBp)})`);
  lines.push(`total ${formatMoney(invoice.subtotalMinor + invoice.taxMinor, invoice.currency)}`);
  return lines.join('\n');
}

module.exports = { renderInvoice };
