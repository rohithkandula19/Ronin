'use strict';

/**
 * Finance export normaliser.
 *
 * Rows leave this module in USD minor units (integer cents) so the ledger
 * import never has to touch a float. Rate table: data/legacy_rates.json.
 */

/**
 * @param {{orders: Array<{id: string, currency: string, total: string}>}} exportDoc
 * @param {{base: string, to_usd: Record<string, number>}} rates
 * @returns {{rows: Array<{id: string, usdCents: number}>, totalUsdCents: number}}
 */
function normalizeOrders(exportDoc, rates) {
  const orders = (exportDoc && exportDoc.orders) || [];
  const table = (rates && rates.to_usd) || {};
  const rows = orders.map((order) => {
    const rate = table[order.currency];
    if (typeof rate !== 'number') {
      throw new Error(`no USD rate for currency ${order.currency} (order ${order.id})`);
    }
    const amount = parseFloat(order.total);
    if (!Number.isFinite(amount)) {
      throw new Error(`unparseable total ${order.total} on order ${order.id}`);
    }
    return { id: order.id, usdCents: Math.round(amount * rate * 100) };
  });
  const totalUsdCents = rows.reduce((sum, row) => sum + row.usdCents, 0);
  return { rows, totalUsdCents };
}

module.exports = { normalizeOrders };
