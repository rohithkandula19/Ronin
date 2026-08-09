'use strict';

const { formatAmount, formatPercent } = require('./format');

/**
 * A receipt, one line per item plus the totals block.
 */
function renderInvoice(invoice) {
  const lines = invoice.lines.map(
    (line) => `${line.quantity} x ${line.description}  ${formatAmount(line.unitMinor, invoice.currency)}`,
  );
  lines.push(`subtotal ${formatAmount(invoice.subtotalMinor, invoice.currency)}`);
  lines.push(`tax ${formatAmount(invoice.taxMinor, invoice.currency)} (${formatPercent(invoice.taxRateBp)})`);
  lines.push(`total ${formatAmount(invoice.subtotalMinor + invoice.taxMinor, invoice.currency)}`);
  return lines.join('\n');
}

module.exports = { renderInvoice };
