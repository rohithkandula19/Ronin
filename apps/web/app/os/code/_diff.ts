/** Minimal LCS-based line diff for the proposed-change view. Pure. */

export interface DiffRow {
  type: "ctx" | "add" | "del";
  text: string;
  /** line number in the "after" file (for add/ctx) */
  ln?: number;
}

export function diffLines(before: string, after: string): DiffRow[] {
  const a = before.replace(/\n$/, "").split("\n");
  const b = after.replace(/\n$/, "").split("\n");
  const m = a.length;
  const n = b.length;

  // LCS length table
  const lcs: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  let ln = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      rows.push({ type: "ctx", text: a[i], ln: ++ln });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      rows.push({ type: "del", text: a[i] });
      i++;
    } else {
      rows.push({ type: "add", text: b[j], ln: ++ln });
      j++;
    }
  }
  while (i < m) rows.push({ type: "del", text: a[i++] });
  while (j < n) rows.push({ type: "add", text: b[j++], ln: ++ln });
  return rows;
}

export function diffStat(rows: DiffRow[]): { add: number; del: number } {
  return rows.reduce(
    (acc, r) => {
      if (r.type === "add") acc.add++;
      if (r.type === "del") acc.del++;
      return acc;
    },
    { add: 0, del: 0 },
  );
}
