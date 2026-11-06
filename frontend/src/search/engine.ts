/** Global search — the client-side engine: builds the navigation index for THIS user from the
 *  menu (same role / SKU gate as the sidebar) + searchIndex.ts, and ranks it with a
 *  fuzzy, typo-tolerant scorer and synonym expansion. Pure functions — no React, no network. */
import type { LucideIcon } from "lucide-react";
import { PAGE_INDEX, EXTRA_PAGES, SYNONYMS } from "./searchIndex";

export interface NavItemLike { to: string; label: string; icon: LucideIcon; roles: string[]; country?: string; untilLive?: boolean }
export interface NavGroupLike { label: string; items: NavItemLike[]; module?: string }

export interface IndexEntry {
  id: string;
  title: string;
  crumbs: string[];          // Module › Page (› Tab) — shown under the title
  to: string;
  icon?: LucideIcon;
  kind: "page" | "tab";
  words: string[];           // lower-case words of title + crumbs + keywords
  title_l: string;
  kw_l: string[];
}

export interface Viewer {
  role: string; country: string; modules?: string[] | null; live?: boolean;
  /** 2026-10-26: may this viewer open the path `to` (entry roles = `roles`)? Default: the system role is in `roles`. The
   *  palette passes config/permissions navItemAllowed so custom roles find the screens their permissions open. */
  allow?: (to: string, roles: string[]) => boolean;
}
const sees = (roles: string[], to: string, v: Viewer) => roles.includes(v.role) || (!!v.allow && v.allow(to, roles));

const norm = (s: string) => s.toLowerCase().replace(/[›·,/()]+/g, " ").replace(/\s+/g, " ").trim();
const wordsOf = (s: string) => norm(s).split(" ").filter(Boolean);

function entry(id: string, title: string, crumbs: string[], to: string, kind: IndexEntry["kind"], kw: string[] = [], icon?: LucideIcon): IndexEntry {
  const kw_l = kw.map(norm);
  const words = Array.from(new Set([...wordsOf(title), ...crumbs.flatMap(wordsOf), ...kw_l.flatMap((k) => k.split(" "))]));
  return { id, title, crumbs, to, kind, icon, words, title_l: norm(title), kw_l };
}

const allowed = (roles: string[] | undefined, v: Viewer, to = "") => !roles || sees(roles, to, v);

/** The navigation index for one viewer. `tx` translates menu labels (i18n). */
export function buildIndex(groups: NavGroupLike[], v: Viewer, tx: (k: string, f: string) => string = (_k, f) => f): IndexEntry[] {
  const out: IndexEntry[] = [];
  const seen = new Set<string>();
  const push = (e: IndexEntry) => { const k = e.to + "|" + e.title_l; if (!seen.has(k)) { seen.add(k); out.push(e); } };
  const visibleGroups = v.role === "superadmin" ? groups.filter((g) => g.label === "Lexora Platform") : groups;
  for (const g of visibleGroups) {
    if (g.module && v.modules && !v.modules.includes(g.module)) continue;
    const gl = tx(`nav.${g.label}`, g.label);
    for (const it of g.items) {
      if (!sees(it.roles, it.to, v) || (it.untilLive && v.live)) continue;
      const il = tx(`nav.${it.label}`, it.label);
      const meta = PAGE_INDEX[it.to] || {};
      push(entry(`p:${it.to}`, il, [gl], it.to, "page", meta.kw, it.icon));
      for (const s of meta.subs || []) {
        if (!allowed(s.roles, v, s.to)) continue;
        push(entry(`t:${s.to}:${s.label}`, s.label, [gl, il], s.to, "tab", s.kw, it.icon));
      }
    }
  }
  if (v.role !== "superadmin") {
    for (const x of EXTRA_PAGES) {
      if (!allowed(x.roles, v, x.to)) continue;
      if (x.module && v.modules && !v.modules.includes(x.module)) continue;
      push(entry(`x:${x.to}:${x.label}`, x.label, [x.group, ...(x.parent ? [x.parent] : [])], x.to, "tab", x.kw));
    }
  }
  return out;
}

/** Menu routes that have no PAGE_INDEX entry (dev check, mirrors scripts/check_search_index.py). */
export function missingIndexRoutes(groups: NavGroupLike[]): string[] {
  const miss = new Set<string>();
  for (const g of groups) for (const it of g.items) if (!PAGE_INDEX[it.to]) miss.add(it.to);
  return [...miss];
}

// ── ranking ──────────────────────────────────────────────────────────────────

/** Bounded Damerau (OSA) edit distance; returns max+1 when it exceeds `max`. */
export function editDistance(a: string, b: string, max = 2): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  const n = a.length, m = b.length;
  let prev2: number[] = [], prev = Array.from({ length: m + 1 }, (_, j) => j);
  for (let i = 1; i <= n; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let d = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d = Math.min(d, prev2[j - 2] + 1);
      cur[j] = d;
      if (d < rowMin) rowMin = d;
    }
    if (rowMin > max) return max + 1;
    prev2 = prev; prev = cur;
  }
  return prev[m];
}

const allowedTypos = (t: string) => (t.length >= 8 ? 2 : t.length >= 4 ? 1 : 0);

/** Query variants: the query itself + each synonym substitution (both directions, word-bounded). */
export function variants(q: string): string[] {
  const base = norm(q);
  const out = new Set([base]);
  for (const [a, b] of SYNONYMS) {
    for (const [from, to] of [[a, b], [b, a]]) {
      const re = new RegExp(`(^|\\s)${from.replace(/[.*+?^${}()|[\]\\&]/g, "\\$&")}(?=\\s|$)`);
      if (re.test(base)) out.add(base.replace(re, `$1${to}`).trim());
    }
  }
  return [...out].slice(0, 8);
}

function scoreOne(e: IndexEntry, q: string): number {
  if (!q) return 0;
  const t = e.title_l;
  if (t === q) return 100;
  if (e.kw_l.includes(q)) return 92;
  if (t.startsWith(q)) return 90;
  const tw = t.split(" ");
  if (tw.some((w) => w.startsWith(q))) return 84;
  if (e.kw_l.some((k) => k.startsWith(q))) return 80;
  if (t.includes(q)) return 76;
  if (e.kw_l.some((k) => k.includes(q))) return 70;
  const crumbs = e.crumbs.map(norm).join(" ");
  if (crumbs.includes(q)) return 50;
  // every query word must hit a word of the entry: exact / prefix / typo (bounded edit distance)
  const toks = q.split(" ").filter(Boolean);
  let s = 0, inTitle = 0, typos = 0;
  for (const tok of toks) {
    let best = 0;
    for (const w of e.words) {
      if (w === tok) { best = 3; break; }
      if (w.startsWith(tok) && tok.length >= 2) best = Math.max(best, 2);
      else if (tok.length >= 3 && w.includes(tok)) best = Math.max(best, 1.5);
      else {
        const k = allowedTypos(tok);
        if (k && best < 1 && editDistance(tok, w.slice(0, Math.max(tok.length, Math.min(w.length, tok.length + 1))), k) <= k) best = 1;
      }
    }
    if (!best) return 0;
    if (best === 1) typos++;
    if (tw.some((w) => w.startsWith(tok))) inTitle++;
    s += best;
  }
  return Math.max(1, Math.min(68, 30 + (s / toks.length) * 10 + inTitle * 4 - typos * 6));
}

export interface Ranked { e: IndexEntry; score: number }

/** Rank the index for a query. Synonym variants score slightly lower than the literal query. */
export function rank(index: IndexEntry[], q: string, limit = 40): Ranked[] {
  const vs = variants(q);
  if (!vs[0]) return [];
  const out: Ranked[] = [];
  for (const e of index) {
    let best = 0;
    vs.forEach((v, i) => { const s = scoreOne(e, v) * (i === 0 ? 1 : 0.97); if (s > best) best = s; });
    if (best > 0) out.push({ e, score: best + (e.kind === "page" ? 0.5 : 0) });
  }
  out.sort((a, b) => b.score - a.score || a.e.title.localeCompare(b.e.title));
  return out.slice(0, limit);
}
