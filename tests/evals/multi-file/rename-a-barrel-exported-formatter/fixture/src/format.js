'use strict';

const CURRENCY_SYMBOLS = { USD: '$', EUR: '€', GBP: '£' };

/**
 * Render integer minor units as a human amount: 5248 -> "$52.48".
 */
function formatAmount(minorUnits, currency = 'USD') {
  if (!Number.isInteger(minorUnits)) {
    throw new TypeError(`expected integer minor units, got ${minorUnits}`);
  }
  const symbol = CURRENCY_SYMBOLS[currency];
  if (symbol === undefined) {
    throw new RangeError(`unsupported currency: ${currency}`);
  }
  const sign = minorUnits < 0 ? '-' : '';
  const absolute = Math.abs(minorUnits);
  const major = Math.floor(absolute / 100);
  const minor = String(absolute % 100).padStart(2, '0');
  return `${sign}${symbol}${major}.${minor}`;
}

/**
 * Render basis points as a percentage: 875 -> "8.75%".
 */
function formatPercent(basisPoints) {
  if (!Number.isInteger(basisPoints)) {
    throw new TypeError(`expected integer basis points, got ${basisPoints}`);
  }
  const whole = Math.floor(basisPoints / 100);
  const fraction = String(basisPoints % 100).padStart(2, '0');
  return `${whole}.${fraction}%`;
}

module.exports = { CURRENCY_SYMBOLS, formatAmount, formatPercent };
