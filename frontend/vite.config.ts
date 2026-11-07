import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: { port: 5173, proxy: { "/api": "http://localhost:7860" } },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 450,
    rollupOptions: {
      output: {
        // Long-lived vendor chunks: they change less often than app code, so browsers keep them cached.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom)[\\/]/.test(id)) return "react";
          if (/[\\/]node_modules[\\/](framer-motion|motion|motion-dom|motion-utils)[\\/]/.test(id)) return "motion";
          if (/[\\/]node_modules[\\/](recharts|d3-[^/\\]+|victory-vendor|es-toolkit|immer|redux|@reduxjs|react-redux|reselect|decimal\.js-light)[\\/]/.test(id)) return "charts";
          return undefined;
        },
      },
    },
  },
});
