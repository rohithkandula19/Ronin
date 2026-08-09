'use strict';

const fs = require('node:fs');
const path = require('node:path');

const formatters = require('./format');

const DEFAULT_CONFIG = path.join(__dirname, '..', 'config', 'columns.json');

/**
 * The admin table columns are configuration, not code: each column names the
 * formatter it wants and we look it up on the format module.
 */
function loadColumns(configPath = DEFAULT_CONFIG) {
  return JSON.parse(fs.readFileSync(configPath, 'utf8')).columns;
}

function renderRow(row, columns = loadColumns()) {
  return columns.map((column) => {
    const formatter = formatters[column.formatter];
    if (typeof formatter !== 'function') {
      throw new Error(
        `column "${column.field}" asks for formatter "${column.formatter}", which src/format.js does not export`,
      );
    }
    return formatter(row[column.field], row.currency);
  });
}

module.exports = { loadColumns, renderRow };
