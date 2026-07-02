/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: (() => {
        // Every color reads a CSS var (RGB triple) from src/styles/theme.css so the whole
        // palette is one dark token system with working opacity utilities.
        const c = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;
        const ramp = (base, keys) =>
          Object.fromEntries([["DEFAULT", c(base)], ...keys.map((k) => [k, c(`${base}-${k}`)])]);
        return {
          canvas: c("canvas"),
          surface: c("surface"),
          "surface-2": c("surface-2"),
          line: c("line"),
          brand: ramp("brand", [50, 100, 200, 500, 600, 700, 800, 900]),
          accent: ramp("accent", [50, 100, 500, 600, 700]),
          pos: ramp("pos", [50, 100, 700]),
          neg: ramp("neg", [50, 100, 700]),
          warn: ramp("warn", [50, 100, 700]),
          explore: ramp("explore", [50, 100, 500, 600, 700]),
        };
      })(),
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "PingFang SC",
          "Microsoft YaHei",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { xl: "12px", "2xl": "16px" },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.30), 0 1px 3px rgba(0,0,0,0.24)",
        pop: "0 10px 30px rgba(0,0,0,0.45)",
        glow: "0 0 0 1px rgb(var(--c-accent-500) / 0.25), 0 0 18px rgb(var(--c-accent-500) / 0.12)",
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "0.95rem" }],
      },
    },
  },
  plugins: [],
};
