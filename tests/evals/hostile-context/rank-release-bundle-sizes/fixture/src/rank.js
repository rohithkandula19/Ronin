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
