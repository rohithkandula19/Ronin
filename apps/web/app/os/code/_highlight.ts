/**
 * A deliberately tiny, dependency-free tokenizer for display-only syntax
 * highlighting. It is NOT a real parser — it handles line comments, strings,
 * numbers, and a keyword set well enough to read nicely in the demo editor.
 * Per-line (no multi-line string/comment state), which is fine here.
 */

export type TokClass = "kw" | "str" | "com" | "num" | "type" | "" ;
export interface Tok {
  text: string;
  cls: TokClass;
}

const KEYWORDS = new Set([
  // TS/JS
  "import", "export", "from", "const", "let", "var", "function", "return",
  "if", "else", "for", "while", "of", "in", "new", "class", "extends",
  "interface", "type", "enum", "async", "await", "try", "catch", "finally",
  "throw", "void", "true", "false", "null", "undefined", "this", "super",
  "default", "as", "yield", "break", "continue", "switch", "case",
  // Python-ish
  "def", "self", "None", "True", "False", "elif", "lambda", "pass", "raise",
  "with", "not", "and", "or", "is",
]);

const TYPEish = /^[A-Z][A-Za-z0-9_]*$/;
const isIdentStart = (c: string) => /[A-Za-z_$]/.test(c);
const isIdent = (c: string) => /[A-Za-z0-9_$]/.test(c);
const isDigit = (c: string) => /[0-9]/.test(c);

export function tokenize(line: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  const push = (text: string, cls: TokClass) => text && toks.push({ text, cls });

  while (i < line.length) {
    const c = line[i];

    // line comments
    if ((c === "/" && line[i + 1] === "/") || c === "#") {
      push(line.slice(i), "com");
      break;
    }

    // strings
    if (c === '"' || c === "'" || c === "`") {
      const quote = c;
      let j = i + 1;
      while (j < line.length && line[j] !== quote) {
        if (line[j] === "\\") j++;
        j++;
      }
      push(line.slice(i, Math.min(j + 1, line.length)), "str");
      i = j + 1;
      continue;
    }

    // numbers
    if (isDigit(c)) {
      let j = i + 1;
      while (j < line.length && /[0-9._boxa-fA-F]/.test(line[j])) j++;
      push(line.slice(i, j), "num");
      i = j;
      continue;
    }

    // identifiers / keywords / types
    if (isIdentStart(c)) {
      let j = i + 1;
      while (j < line.length && isIdent(line[j])) j++;
      const word = line.slice(i, j);
      const cls: TokClass = KEYWORDS.has(word) ? "kw" : TYPEish.test(word) ? "type" : "";
      push(word, cls);
      i = j;
      continue;
    }

    // everything else, one char at a time (kept plain)
    push(c, "");
    i++;
  }

  return toks;
}

export const TOKEN_CLASS: Record<TokClass, string> = {
  kw: "text-accent-deep",
  str: "text-success",
  com: "text-text-faint italic",
  num: "text-info",
  type: "text-warn",
  "": "text-text",
};
