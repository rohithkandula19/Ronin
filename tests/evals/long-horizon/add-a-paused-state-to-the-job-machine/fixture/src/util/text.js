/** Small string helpers used by the renderer. */

export function pad(value, width) {
  const text = String(value);
  return text.length >= width ? text : text + ' '.repeat(width - text.length);
}

export function truncate(value, limit) {
  const text = String(value);
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

export function titleCase(value) {
  return String(value).replace(/^[a-z]/, (character) => character.toUpperCase());
}
