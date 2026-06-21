/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#F5F7FA",
        surface: "#FFFFFF",
        line: "#E5E7EB",
        brand: {
          DEFAULT: "#0B1F3A",
          50: "#eef2f7",
          100: "#d6e0ec",
          200: "#aebfd5",
          500: "#1c3a63",
          600: "#16335a",
          700: "#102a4c",
          800: "#0d2444",
          900: "#0B1F3A",
        },
        accent: {
          DEFAULT: "#2563EB",
          50: "#eff4ff",
          100: "#dbe6fe",
          500: "#3b76f0",
          600: "#2563EB",
          700: "#1d4ed8",
        },
        pos: { DEFAULT: "#16A34A", 50: "#ecfdf3", 100: "#d1fadf", 700: "#15803d" },
        neg: { DEFAULT: "#DC2626", 50: "#fef2f2", 100: "#fee2e2", 700: "#b91c1c" },
        warn: { DEFAULT: "#D97706", 50: "#fffbeb", 100: "#fef0c7", 700: "#b45309" },
        explore: { DEFAULT: "#6D28D9", 50: "#f5f3ff", 100: "#ede9fe", 500: "#7c3aed", 600: "#6D28D9", 700: "#5b21b6" },
      },
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
        card: "0 1px 2px rgba(11,31,58,0.04), 0 1px 3px rgba(11,31,58,0.06)",
        pop: "0 8px 24px rgba(11,31,58,0.10)",
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "0.95rem" }],
      },
    },
  },
  plugins: [],
};
