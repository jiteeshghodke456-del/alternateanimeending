import { useEffect, useRef } from "react";

/**
 * Lava-lamp background: five soft blobs rise, sink, stretch and merge, under one static frost layer.
 *
 * Why a tiny canvas and not five animated CSS layers (both measured with Playwright + CDP in Chromium):
 * five large transformed layers cost the compositor a full-screen redraw of every layer at 60 Hz
 * (~39 fps on a software compositor at 1280x800). Here the blobs are drawn into a canvas about 1/8 of the
 * viewport (≈160x100 px, ~0.1 ms per frame) and the browser upscales it, which also gives the blur for free.
 * - Redraws at most 30 times a second, so the compositor only has new damage 30 times a second.
 * - Stops while the tab is hidden; draws one still frame under prefers-reduced-motion.
 * - Colours come from CSS tokens (--lava-1..5, --bg-base) and are re-read when the theme changes.
 * - The frost (wash + film grain) is one static CSS layer on top; no backdrop-filter in the background.
 */

type Blob = { x: number; y: number; r: number; ax: number; ay: number; px: number; py: number; ph: number; morph: number };

// Positions/radii are fractions of the canvas; periods are seconds (20-40 s loops, all different).
const BLOBS: Blob[] = [
  { x: 0.18, y: 0.78, r: 0.36, ax: 0.08, ay: 0.42, px: 34, py: 38, ph: 0.0, morph: 23 },
  { x: 0.82, y: 0.22, r: 0.34, ax: 0.07, ay: 0.38, px: 29, py: 33, ph: 2.1, morph: 27 },
  { x: 0.48, y: 0.62, r: 0.26, ax: 0.14, ay: 0.46, px: 24, py: 26, ph: 4.2, morph: 21 },
  { x: 0.86, y: 0.82, r: 0.30, ax: 0.09, ay: 0.36, px: 38, py: 31, ph: 1.3, morph: 31 },
  { x: 0.22, y: 0.18, r: 0.28, ax: 0.10, ay: 0.40, px: 31, py: 36, ph: 3.4, morph: 25 },
];
const SCALE = 1 / 8; // canvas pixels per CSS pixel
const FRAME_MS = 1000 / 30;
const TAU = Math.PI * 2;

function readColours(): { base: string; blobs: string[]; alpha: number } {
  const s = getComputedStyle(document.documentElement);
  const v = (n: string) => s.getPropertyValue(n).trim();
  return {
    base: v("--bg-base") || "#f2f2f7",
    blobs: [1, 2, 3, 4, 5].map((i) => v(`--lava-${i}`) || "#b9b0ff"),
    alpha: parseFloat(v("--lava-opacity")) || 0.85,
  };
}

function hexToRgb(c: string): [number, number, number] {
  const m = c.replace("#", "");
  const h = m.length === 3 ? m.split("").map((x) => x + x).join("") : m;
  const n = parseInt(h.slice(0, 6), 16);
  return Number.isNaN(n) ? [180, 170, 255] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function MeshBackground() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d", { alpha: false });
    if (!canvas || !ctx) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let colours = readColours();
    let rgb = colours.blobs.map(hexToRgb);
    let raf = 0;
    let last = 0;
    const t0 = performance.now() - 7_000; // start mid-motion, not with every blob at its origin

    const resize = () => {
      const w = Math.max(48, Math.round(window.innerWidth * SCALE));
      const h = Math.max(48, Math.round(window.innerHeight * SCALE));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    };

    const draw = (now: number) => {
      const t = (now - t0) / 1000;
      const { width: w, height: h } = canvas;
      const m = Math.max(w, h);
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = colours.base;
      ctx.fillRect(0, 0, w, h);
      BLOBS.forEach((b, i) => {
        // Slow rise and fall (lava-lamp), a sideways sway, and a stretch that turns as it moves.
        const cx = (b.x + b.ax * Math.sin((TAU * t) / b.px + b.ph)) * w;
        const cy = (b.y - b.ay * (0.5 - 0.5 * Math.cos((TAU * t) / b.py + b.ph)) + b.ay * 0.25) * h;
        const sx = 1 + 0.16 * Math.sin((TAU * t) / b.morph + b.ph);
        const sy = 1 + 0.16 * Math.cos((TAU * t) / (b.morph * 1.3) + b.ph);
        const rot = 0.6 * Math.sin((TAU * t) / (b.morph * 1.7) + b.ph);
        const breathe = 0.82 + 0.18 * Math.sin((TAU * t) / (b.px * 0.8) + b.ph * 1.7);
        const r = b.r * m;
        const [cr, cg, cb] = rgb[i];
        const a = colours.alpha * breathe;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(rot);
        ctx.scale(sx, sy);
        const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
        g.addColorStop(0, `rgba(${cr},${cg},${cb},${a})`);
        g.addColorStop(0.45, `rgba(${cr},${cg},${cb},${a * 0.92})`);
        g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, TAU);
        ctx.fill();
        ctx.restore();
      });
    };

    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (now - last < FRAME_MS) return;
      last = now;
      draw(now);
    };
    const start = () => {
      cancelAnimationFrame(raf);
      resize();
      if (reduce.matches || document.hidden) { draw(t0 + 7_000); return; }
      raf = requestAnimationFrame(loop);
    };
    const stop = () => cancelAnimationFrame(raf);
    const onVisibility = () => (document.hidden ? stop() : start());
    const onResize = () => { resize(); draw(performance.now()); };
    const onTheme = () => { colours = readColours(); rgb = colours.blobs.map(hexToRgb); draw(performance.now()); };

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
      <canvas ref={ref} className="lava-canvas" width={160} height={100} />
      <div className="lava-frost" />
    </div>
  );
}
