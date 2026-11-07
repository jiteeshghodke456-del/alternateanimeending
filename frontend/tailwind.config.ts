import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1rem", screens: { "2xl": "1200px" } },
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "-apple-system", "BlinkMacSystemFont", "system-ui", "sans-serif"],
      },
      fontSize: {
        // Apple-style type scale: [size, {lineHeight, letterSpacing, fontWeight}]
        caption: ["12px", { lineHeight: "16px", letterSpacing: "0.005em" }],
        footnote: ["13px", { lineHeight: "18px" }],
        body: ["15px", { lineHeight: "22px", letterSpacing: "-0.006em" }],
        callout: ["16px", { lineHeight: "24px", letterSpacing: "-0.01em" }],
        headline: ["17px", { lineHeight: "24px", letterSpacing: "-0.014em", fontWeight: "600" }],
        title3: ["20px", { lineHeight: "26px", letterSpacing: "-0.019em", fontWeight: "600" }],
        title2: ["24px", { lineHeight: "30px", letterSpacing: "-0.022em", fontWeight: "600" }],
        title1: ["32px", { lineHeight: "38px", letterSpacing: "-0.026em", fontWeight: "600" }],
        display: ["clamp(40px, 7vw, 72px)", { lineHeight: "1.04", letterSpacing: "-0.035em", fontWeight: "650" }],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        // Brand Gate semantic colours (see src/styles/index.css for AA-safe text variants)
        brand: "#0A84FF",
        flag: { DEFAULT: "#FF453A", text: "var(--flag-text)" },
        human: { DEFAULT: "#FF9F0A", text: "var(--human-text)" },
        pass: { DEFAULT: "#30D158", text: "var(--pass-text)" },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 4px)", sm: "calc(var(--radius) - 8px)" },
      keyframes: {
        "blob-a": { "0%,100%": { transform: "translate3d(0,0,0) scale(1)" }, "50%": { transform: "translate3d(6vw,4vh,0) scale(1.12)" } },
        "blob-b": { "0%,100%": { transform: "translate3d(0,0,0) scale(1)" }, "50%": { transform: "translate3d(-5vw,6vh,0) scale(1.08)" } },
        "blob-c": { "0%,100%": { transform: "translate3d(0,0,0) scale(1)" }, "50%": { transform: "translate3d(4vw,-5vh,0) scale(1.15)" } },
      },
      animation: {
        "blob-a": "blob-a 38s ease-in-out infinite",
        "blob-b": "blob-b 46s ease-in-out infinite",
        "blob-c": "blob-c 54s ease-in-out infinite",
      },
    },
  },
};
export default config;
