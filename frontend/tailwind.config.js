/** Lexora indigo — the brand palette of the gradient "L" mark (components/Logo.tsx, public/icon.svg). */
const LEXORA = {
  50:  "#eef2ff",
  100: "#e0e7ff",
  200: "#c7d2fe",
  300: "#a5b4fc",
  400: "#7c78ec",
  500: "#4f46e5",   // Lexora indigo — white text 6.3:1
  600: "#4338ca",   // white text 7.9:1
  700: "#3730a3",
  800: "#312e81",
  900: "#232065",
  950: "#1e1b4b",   // logo ink / headings
};

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  // Class-based dark mode — `<html class="dark">` flips the theme.
  // Dark styling is driven mostly by global overrides in index.css so we
  // don't have to add `dark:` variants to every component.
  darkMode: "class",
  theme: {
    extend: {
      // Additive only — the five Tailwind defaults (sm 640 / md 768 / lg 1024 /
      // xl 1280 / 2xl 1536) are untouched, so no existing class changes meaning.
      //   xs  → small phones held in one hand (iPhone SE is 375px).
      //   3xl → wall-mounted TV / 4K panel at 1:1. The OPD display board and
      //         the command centre are read from across a room, so they want
      //         *larger* type here, not more columns.
      //   4xl → true 4K at 1:1 (3840px) with the browser at 100%.
      screens: {
        xs: "480px",
        "3xl": "1920px",
        "4xl": "2560px",
      },
      colors: {
        // Lexora indigo (the gradient "L" mark: #2563eb → #4f46e5 → #6366f1). White text passes
        // WCAG AA from 500 up; on white, 500+ passes as link / text colour.
        primary: LEXORA,
        // Deep indigo ink (headings / logo ink) and a soft slate for secondary text.
        ink: {
          DEFAULT: "#1e1b4b",
          soft:    "#475569",
        },
        // Bright accent (the light gradient stop) — decoration only, never body text on white.
        accent: {
          DEFAULT:  "#6366f1",
          lavender: "#e0e7ff",
        },
        // Stray `bg-violet-600` / `text-purple-500` classes resolve to the brand indigo too.
        indigo: LEXORA,
        violet: LEXORA,
        purple: LEXORA,
        // `teal` is the brand blue accent (alias kept from the HMS family; the gradient's first stop,
        // 600 = #2563eb), distinct from primary for second-series tones.
        teal: {
          50:  "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
          800: "#1e40af",
          900: "#1e3a8a",
        },
        surface: {
          DEFAULT: "#f8f9fc",
          card:    "#ffffff",
          border:  "#e5e7ef",
        }
      },
      fontFamily: {
        // Brand stack (aligned with medfidelity_ivf so both apps share typography):
        //   sans    → MD System Narrow (UI / body)
        //   display → Bogart Alt (logotype / headings)
        //   mono    → ui-monospace fallback (no monospace brand font supplied)
        sans:    ["'MD System Narrow'", "Georgia", "Arial", "system-ui", "sans-serif"],
        display: ["'Bogart Alt'", "Georgia", "serif"],
        mono:    ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 3px rgba(79,70,229,0.08), 0 4px 12px rgba(79,70,229,0.06)",
        "card-hover": "0 4px 16px rgba(79,70,229,0.16)",
      }
    }
  },
  plugins: []
}
