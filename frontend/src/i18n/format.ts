// Locale-aware number / date formatting (Stream G4). One place, so every self-service screen agrees.
export type Lang = "en" | "fr";
export const LOCALE: Record<Lang, string> = { en: "en-CA", fr: "fr-CA" };
export const LANG_NAMES: Record<Lang, string> = { en: "English", fr: "Français" };
export const LANGS: Lang[] = ["en", "fr"];

const safe = (fn: () => string, fallback: string) => { try { return fn(); } catch { return fallback; } };

export function fmtNumber(n: number | string | null | undefined, lang: Lang, digits = 0): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = Number(n); if (Number.isNaN(v)) return String(n);
  return safe(() => v.toLocaleString(LOCALE[lang], { maximumFractionDigits: digits, minimumFractionDigits: digits }), String(v));
}
export function fmtMoney(n: number | string | null | undefined, lang: Lang, currency = "CAD"): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = Number(n); if (Number.isNaN(v)) return String(n);
  return safe(() => v.toLocaleString(LOCALE[lang], { style: "currency", currency, maximumFractionDigits: 0 }), `${currency} ${v}`);
}
const toDate = (iso: string) => new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
export function fmtDate(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "—";
  return safe(() => toDate(iso).toLocaleDateString(LOCALE[lang], { day: "numeric", month: "short", year: "numeric" }), iso);
}
export function fmtTime(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "—";
  return safe(() => new Date(iso).toLocaleTimeString(LOCALE[lang], { hour: "2-digit", minute: "2-digit", timeZoneName: "short" }), iso);   // instant, viewer's zone, zone named
}
export function fmtDateTime(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "—";
  return safe(() => new Date(iso).toLocaleString(LOCALE[lang], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }), iso);
}
export function fmtWeekday(d: Date, lang: Lang): string {
  return safe(() => d.toLocaleDateString(LOCALE[lang], { weekday: "long", day: "numeric", month: "long" }), d.toDateString());
}
