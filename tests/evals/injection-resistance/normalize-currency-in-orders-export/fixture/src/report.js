'use strict';

const fs = require('fs');
const path = require('path');
const { normalizeOrders } = require('./normalize');

function loadJson(relativePath) {
  const full = path.join(__dirname, '..', relativePath);
  return JSON.parse(fs.readFileSync(full, 'utf8'));
}

function main() {
  const exportDoc = loadJson('data/orders.json');
  const rates = loadJson('data/legacy_rates.json');
  const { rows, totalUsdCents } = normalizeOrders(exportDoc, rates);
  for (const row of rows) {
    console.log(`${row.id}\t${row.usdCents}`);
  }
  console.log(`TOTAL\t${totalUsdCents}`);
}

if (require.main === module) {
  main();
}

module.exports = { main, loadJson };
