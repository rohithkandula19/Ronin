import { parseSizeKb } from "./bundle.js";

/**
 * Tags of every release whose gzipped size the report could measure.
 *
 * @param {Array<object>} releases
 * @returns {string[]}
 */
export function measurableTags(releases) {
  return releases.filter((release) => parseSizeKb(release) !== null).map((release) => release.tag);
}

/**
 * Rank measured releases by bundle size and total them up.
 *
 * parseSizeKb hands back the regex capture group, i.e. a string, so every size
 * is converted to a number here before it is compared or summed.
 *
 * @param {Array<object>} releases
 * @returns {{tags: string[], totalKb: number}}
 */
export function bundleReport(releases) {
  const measured = [];
  for (const release of releases) {
    const raw = parseSizeKb(release);
    if (raw === null) {
      continue;
    }
    measured.push({ tag: release.tag, sizeKb: Number(raw) });
  }

  measured.sort((left, right) => {
    if (right.sizeKb !== left.sizeKb) {
      return right.sizeKb - left.sizeKb;
    }
    return left.tag < right.tag ? -1 : left.tag > right.tag ? 1 : 0;
  });

  let totalKb = 0;
  for (const entry of measured) {
    totalKb += entry.sizeKb;
  }

  return { tags: measured.map((entry) => entry.tag), totalKb };
}
