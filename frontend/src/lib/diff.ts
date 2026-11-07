import type { DiffSegment } from "./api";

/** Word-level diff (LCS) used when the server did not send one (e.g. opening an older version). */
export function diffSegments(a: string, b: string): DiffSegment[] {
  const x = a.split(/(\s+)/).filter(Boolean);
  const y = b.split(/(\s+)/).filter(Boolean);
  const dp = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffSegment[] = [];
  const push = (op: DiffSegment["op"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text;
    else out.push({ op, text });
  };
  let i = 0, j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) { push("equal", x[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) push("delete", x[i++]);
    else push("insert", y[j++]);
  }
  while (i < x.length) push("delete", x[i++]);
  while (j < y.length) push("insert", y[j++]);
  return out;
}
