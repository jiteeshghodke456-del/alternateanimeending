import { useEffect, useRef } from "react";

/**
 * Lava-lamp background: six slow wax blobs rise, sink, merge and split (metaballs), under a static frost layer.
 *
 * How it stays cheap: the field is computed per pixel into a small canvas (1/5 of the viewport, about
 * 256x160 on a laptop, ~2 ms a frame) and the browser upscales it, which softens the edges just enough.
 * - Redraws at most 30 times a second (12 on a struggling device); stops while the tab is hidden; one still frame under reduced motion.
 * - Colours come from CSS tokens (--lava-1..5, --lava-opacity, --bg-base), re-read when the theme changes.
 * - Edges: a smoothstep on the summed field gives the defined "wax" edge; a faint halo sits just outside it,
 *   and the core of each blob is a little lighter, so blobs read as 3D wax rather than a flat haze.
 */

type Blob = { x: number; amp: number; py: number; px: number; sway: number; r: number; c: number; ph: number };

// x: column (fraction of width); amp: how far it rises/sinks (fraction of height); py/px: vertical / sideways
// periods in seconds; r: radius as a fraction of the shorter side; c: colour token index (0-4).
const BLOBS: Blob[] = [
  { x: 0.14, amp: 0.40, py: 58, px: 37, sway: 0.05, r: 0.24, c: 0, ph: 0.0 },
  { x: 0.86, amp: 0.38, py: 52, px: 43, sway: 0.05, r: 0.22, c: 1, ph: 2.4 },
  { x: 0.52, amp: 0.44, py: 66, px: 47, sway: 0.08, r: 0.17, c: 2, ph: 4.1 },
  { x: 0.30, amp: 0.36, py: 47, px: 33, sway: 0.06, r: 0.14, c: 3, ph: 1.2 },
  { x: 0.72, amp: 0.42, py: 61, px: 39, sway: 0.06, r: 0.15, c: 4, ph: 3.3 },
  { x: 0.95, amp: 0.26, py: 74, px: 51, sway: 0.03, r: 0.18, c: 3, ph: 0.7 },
];
const SCALE = 1 / 5;
const FRAME_MS = 1000 / 30;
const SLOW_FRAME_MS = 1000 / 12; // used when the device is struggling (see loop)
const TAU = Math.PI * 2;

function rgbOf(c: string, fallback: [number, number, number]): [number, number, number] {
  const h = c.trim().replace("#", "");
  const full = h.length === 3 ? h.split("").map((x) => x + x).join("") : h;
  const n = parseInt(full.slice(0, 6), 16);
  return Number.isNaN(n) || full.length < 6 ? fallback : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function readTheme() {
  const s = getComputedStyle(document.documentElement);
  const v = (n: string) => s.getPropertyValue(n);
  return {
    base: rgbOf(v("--bg-base"), [11, 8, 16]),
    cols: [1, 2, 3, 4, 5].map((i) => rgbOf(v(`--lava-${i}`), [149, 30, 252])),
    alpha: parseFloat(v("--lava-opacity")) || 0.8,
  };
}

const smooth = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

export function MeshBackground() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d", { alpha: false });
    if (!canvas || !ctx) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let theme = readTheme();
    let img: ImageData | null = null;
    let px32: Uint32Array | null = null;
    let raf = 0;
    let last = 0;
    const t0 = performance.now() - 11_000; // start mid-motion
    const n = BLOBS.length;
    const bx = new Float32Array(n), by = new Float32Array(n), br2 = new Float32Array(n);

    const resize = () => {
      const w = Math.max(64, Math.round(window.innerWidth * SCALE));
      const h = Math.max(64, Math.round(window.innerHeight * SCALE));
      if (canvas.width !== w || canvas.height !== h || !img) {
        canvas.width = w; canvas.height = h;
        img = ctx.createImageData(w, h);
        px32 = new Uint32Array(img.data.buffer);
      }
    };

    const draw = (now: number) => {
      if (!img || !px32) return;
      const t = (now - t0) / 1000;
      const w = canvas.width, h = canvas.height, m = Math.min(w, h);
      for (let i = 0; i < n; i++) {
        const b = BLOBS[i];
        // Lava-lamp path: a slow rise and sink (eased at the ends, like warm wax), a small sideways sway,
        // and a radius that breathes a little so merges and splits keep changing.
        const s = Math.sin((TAU * t) / b.py + b.ph);
        bx[i] = (b.x + b.sway * Math.sin((TAU * t) / b.px + b.ph * 1.3)) * w;
        by[i] = (0.5 - b.amp * s * (0.75 + 0.25 * s * s)) * h;
        const r = b.r * m * (0.9 + 0.1 * Math.sin((TAU * t) / (b.px * 0.7) + b.ph));
        br2[i] = r * r;
      }
      const [r0, g0, b0] = theme.base;
      const A = theme.alpha;
      const cols = theme.cols;
      let k = 0;
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          let f = 0, cr = 0, cg = 0, cb = 0;
          for (let i = 0; i < n; i++) {
            const dx = x - bx[i], dy = y - by[i];
            const v = br2[i] / (dx * dx + dy * dy + 1);
            f += v;
            const c = cols[BLOBS[i].c];
            cr += v * c[0]; cg += v * c[1]; cb += v * c[2];
          }
          let R = r0, G = g0, B = b0;
          if (f > 0.02) {
            cr /= f; cg /= f; cb /= f;
            const body = smooth(0.7, 1.25, f);           // the wax edge (soft, but still a shape)
            const halo = smooth(0.25, 0.7, f) * 0.14;     // faint glow just outside it
            const core = smooth(1.6, 5, f) * 0.16;        // slightly lighter centre: reads as 3D wax
            cr += (255 - cr) * core; cg += (255 - cg) * core; cb += (255 - cb) * core;
            const a = Math.max(body, halo) * A;
            R = r0 + (cr - r0) * a; G = g0 + (cg - g0) * a; B = b0 + (cb - b0) * a;
          }
          px32[k++] = 0xff000000 | ((B & 255) << 16) | ((G & 255) << 8) | (R & 255);
        }
      }
      ctx.putImageData(img, 0, 0);
    };

    // Adaptive: if the page itself drops below ~40 fps (slow GPU, software compositing under the frosted
    // panels), redraw the lava 12 times a second instead of 30, so scrolling and clicks stay smooth.
    let frameMs = FRAME_MS, ticks = 0, windowStart = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (!windowStart) windowStart = now;
      ticks++;
      if (now - windowStart > 2000) {
        const fps = (ticks * 1000) / (now - windowStart);
        frameMs = fps < 40 ? SLOW_FRAME_MS : FRAME_MS;
        ticks = 0; windowStart = now;
      }
      if (now - last < frameMs) return;
      last = now;
      draw(now);
    };
    const start = () => {
      cancelAnimationFrame(raf);
      resize();
      if (reduce.matches || document.hidden) { draw(t0 + 11_000); return; }
      raf = requestAnimationFrame(loop);
    };
    const stop = () => cancelAnimationFrame(raf);
    const onVisibility = () => (document.hidden ? stop() : start());
    const onResize = () => { resize(); draw(performance.now()); };
    const onTheme = () => { theme = readTheme(); draw(performance.now()); };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", onResize);
    reduce.addEventListener("change", start);
    const mo = new MutationObserver(onTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", onTheme);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", onResize);
      reduce.removeEventListener("change", start);
      mq.removeEventListener("change", onTheme);
      mo.disconnect();
    };
  }, []);

  return (
    <div className="lava-bg mesh-bg" aria-hidden="true">
      <canvas ref={ref} className="lava-canvas" width={256} height={160} />
      <div className="lava-frost" />
    </div>
  );
}
