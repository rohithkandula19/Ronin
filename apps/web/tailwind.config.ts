import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: { DEFAULT: "#a98467", soft: "#d4a373", deep: "#8a6a4f" },
        bg: "#fafaf9",
        ink: "#1a1a1a",
        dim: "#6b6b6b",
        border: "#e7e5e4",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "Menlo", "Monaco", "monospace"],
      },
    },
  },
  plugins: [typography],
};

export default config;
