/** Join class names, dropping falsy values. No dependency, no dedupe magic. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
