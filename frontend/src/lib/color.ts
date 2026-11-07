/** Small colour helpers (WCAG 2.x relative luminance and contrast). No dependencies. */

export const HEX_RE = /^#([0-9a-f]{6})$/i;

/** "#abc" / "abc" / "#AABBCC" -> "#aabbcc", or null if it isn't a colour. */
export function normalizeHex(input: string): string | null {
  let h = input.trim().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/i.test(h)) h = h.split("").map((c) => c + c).join("");
  return /^[0-9a-f]{6}$/i.test(h) ? `#${h.toLowerCase()}` : null;
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  const h = normalizeHex(hex);
  if (!h) return 0;
  const n = parseInt(h.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

/** The readable text colour (near-black or white) on a background, and its ratio. */
export function bestText(bg: string): { color: string; ratio: number } {
  const dark = contrast(bg, "#111111"), light = contrast(bg, "#ffffff");
  return dark >= light ? { color: "#111111", ratio: dark } : { color: "#ffffff", ratio: light };
}
