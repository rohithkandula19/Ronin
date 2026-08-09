const SIZE_PATTERN = /^([0-9]+(?:\.[0-9]+)?)\s*kB$/;

/**
 * Read the gzipped bundle size out of a release record.
 *
 * Release records come from the size-limit report, which sometimes omits the
 * stats block entirely and sometimes writes a placeholder it could not measure.
 *
 * @param {{stats?: {gzip?: string|null}}} release
 * @returns {number} the size in kilobytes, or null when it cannot be read
 */
export function parseSizeKb(release) {
  const raw = release && release.stats ? release.stats.gzip : undefined;
  if (raw === undefined || raw === null) {
    return null;
  }
  const match = SIZE_PATTERN.exec(String(raw).trim());
  if (match === null) {
    return null;
  }
  return match[1];
}

/**
 * Format a size for the release notes table.
 *
 * @param {string} tag
 * @param {*} size
 * @returns {string}
 */
export function formatRow(tag, size) {
  return `${tag.padEnd(8)} ${size === null ? "-" : `${size} kB`}`;
}
