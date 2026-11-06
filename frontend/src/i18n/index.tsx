// i18n — English and Canadian French (Stream G4): t() with JSON catalogues, English fallback that is NEVER silent.
//
// - Catalogues: en.json (source), fr.json (Canadian French). Flat dot keys.
// - t(key, vars): current language → English → the key itself. Any fallback is RECORDED
//   (per language, per key) and surfaced on the Translations report screen and in the
//   language picker's counter. In dev builds a fallback is also prefixed with "⚠ ".
// - tx(key, fallback): for labels that may not be in the catalogue at all (menu items):
//   if English has no such key the fallback is returned silently (not a translation gap).
// - The language is kept in localStorage ("lx-lang", no personal data) for instant paint and
//   saved on the server through /engage/me/prefs (see LanguagePicker).
import { useSyncExternalStore } from "react";
import en from "./en.json";
import fr from "./fr.json";
import { LANGS, type Lang } from "./format";

type Catalog = Record<string, string>;
export const CATALOGS: Record<Lang, Catalog> = { en: en as Catalog, fr: fr as Catalog };

let lang: Lang = "en";
try { const s = localStorage.getItem("lx-lang"); if (s && (LANGS as string[]).includes(s)) lang = s as Lang; } catch { /* ignore */ }
const missing: Record<Lang, Set<string>> = { en: new Set(), fr: new Set() };
const subs = new Set<() => void>();
let version = 0;
const bump = () => { version++; subs.forEach((f) => f()); };

export function getLang(): Lang { return lang; }
export function setLang(l: Lang) {
  if (!(LANGS as string[]).includes(l) || l === lang) return;
  lang = l;
  try { localStorage.setItem("lx-lang", l); } catch { /* ignore */ }
  try { document.documentElement.lang = l; } catch { /* ignore */ }
  bump();
}

function interpolate(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

/** Translate. Falls back to English (recorded, visible), then to the key. */
export function t(key: string, vars?: Record<string, string | number>): string {
  const cur = CATALOGS[lang][key];
  if (cur !== undefined && cur !== "") return interpolate(cur, vars);
  if (lang !== "en" && !missing[lang].has(key)) { missing[lang].add(key); bump(); }
  const base = CATALOGS.en[key];
  if (base !== undefined) return (import.meta.env.DEV && lang !== "en" ? "⚠ " : "") + interpolate(base, vars);
  if (!missing.en.has(key)) { missing.en.add(key); bump(); }
  return key;
}

/** Translate a label that may not be in the catalogue; missing in English = not a gap. */
export function tx(key: string, fallback: string): string {
  if (CATALOGS.en[key] === undefined) return fallback;
  return t(key);
}

/** The set of keys that fell back at runtime, per language (for the report + counter). */
export function runtimeMissing(): Record<Lang, string[]> {
  return { en: [...missing.en], fr: [...missing.fr] };
}

/** Static report: every English key, and which languages do not have it (or have it empty). */
export function catalogReport() {
  const keys = Object.keys(CATALOGS.en).sort();
  const rows = keys.map((k) => ({
    key: k, en: CATALOGS.en[k],
    fr: CATALOGS.fr[k] || "",
    missing: (["fr"] as Lang[]).filter((l) => !CATALOGS[l][k]),
  }));
  const totals = { keys: keys.length } as Record<string, number>;
  for (const l of ["fr"] as Lang[]) totals[l] = rows.filter((r) => r.missing.includes(l)).length;
  const extra = (["fr"] as Lang[]).map((l) => ({ lang: l, keys: Object.keys(CATALOGS[l]).filter((k) => CATALOGS.en[k] === undefined) }));
  return { rows, totals, extra };
}

function subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f); }; }
/** Re-render on language change; returns the t() function. */
export function useT() { useSyncExternalStore(subscribe, () => version, () => version); return t; }
export function useLang(): Lang { useSyncExternalStore(subscribe, () => version, () => version); return lang; }
export function useMissingCount(): number { useSyncExternalStore(subscribe, () => version, () => version); return lang === "en" ? 0 : missing[lang].size; }
