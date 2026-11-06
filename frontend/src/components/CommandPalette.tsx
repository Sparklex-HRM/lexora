import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import {
  Search, Plus, Wallet, CalendarDays, UserPlus, ClipboardList, CheckSquare, CornerDownLeft, X, LucideIcon, User,
  FileText, LifeBuoy, Send, Briefcase, LogOut, Package, FileSignature, BookOpen,
  Clock, History, ShieldCheck, Gift, AlertTriangle, ArrowRight,
} from "lucide-react";
import api from "../api/client";
import { useAuthStore, useUserRole, HR_WRITE, PAYROLL_ACCESS, ALL_STAFF } from "../store/authStore";
import { navItemAllowed } from "../config/permissions";   // 2026-10-26: custom roles in search
import { tx, useT } from "../i18n";
import { buildIndex, missingIndexRoutes, rank, type NavGroupLike, type Ranked } from "../search/engine";

/** Global search (2026-10-20, global-search spec) — ONE box in the header.
 *
 *  Desktop: a real input in the header; results drop down under it. Phone: a search icon that
 *  opens a full-screen sheet. Ctrl/⌘+K or "/" (outside a text field) opens it anywhere.
 *  Results: Pages (navigation index: every menu page + every tab / section inside pages, client
 *  side, instant) · People and Records (GET /search — the gateway fans out to every service,
 *  each applying its own role scope) · Actions (role-gated quick actions). ↑↓ move, Enter open,
 *  Tab / Shift+Tab switch the type filter, Esc close. Recent searches + recently opened live in
 *  sessionStorage only (never localStorage — no personal data kept after the tab closes). */

// Structural copy of the layout's NavGroup/NavItem — passed in, not imported (no circular import).
export type PaletteNavGroup = NavGroupLike;

// ── Avatar helpers (shared with the Employees list) ──
const AVATAR = [
  "bg-primary-100 text-primary-700", "bg-teal-100 text-teal-700", "bg-amber-100 text-amber-700", "bg-rose-100 text-rose-700",
  "bg-sky-100 text-sky-700", "bg-emerald-100 text-emerald-700", "bg-violet-100 text-violet-700", "bg-orange-100 text-orange-700",
];
export function avatarClass(seed: string) { let h = 0; for (const ch of seed || "") h = (h * 31 + ch.charCodeAt(0)) >>> 0; return AVATAR[h % AVATAR.length]; }
export function initialsOf(e: { first_name?: string | null; last_name?: string | null }) {
  return `${(e.first_name || "").trim()[0] || ""}${(e.last_name || "").trim()[0] || ""}`.toUpperCase() || "?";
}
/** Every word typed must appear somewhere in the haystack (order-free substring match). */
export function matches(hay: string, q: string): boolean {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  return words.every((w) => hay.includes(w));
}

// ── server hits ──
export interface SearchHit { type: string; id: string; title: string; subtitle: string; url: string; score: number; badge?: string | null; source?: string }
interface SearchResponse { q: string; hits: SearchHit[]; partial: boolean; slow: string[]; failed: string[]; took_ms: number }

const TYPE_META: Record<string, { label: string; icon: LucideIcon }> = {
  employee: { label: "People", icon: User }, candidate: { label: "Candidates", icon: Briefcase },
  requisition: { label: "Requisitions", icon: ClipboardList }, offer: { label: "Offers", icon: FileSignature },
  letter: { label: "Letters", icon: FileText }, exit: { label: "Exit cases", icon: LogOut }, asset: { label: "Assets", icon: Package },
  request: { label: "Requests", icon: Send }, policy: { label: "Policies", icon: ShieldCheck }, onboarding: { label: "Onboarding", icon: UserPlus },
  leave: { label: "Leave requests", icon: CalendarDays }, attendance: { label: "Attendance corrections", icon: Clock },
  permission: { label: "Permissions", icon: Clock }, payslip: { label: "Pay stubs", icon: Wallet }, run: { label: "Pay runs", icon: Wallet },
  ticket: { label: "Helpdesk tickets", icon: LifeBuoy }, course: { label: "Courses", icon: BookOpen }, approval: { label: "Approvals", icon: CheckSquare },
};
const typeLabel = (t: string) => tx(`search.type.${t}`, TYPE_META[t]?.label || t);

// ── quick actions (role-gated; same roles as the screens they open) ──
interface Action { label: string; sub: string; to: string; icon: LucideIcon; roles: string[]; kw: string; country?: string; module?: string }
const ACTIONS: Action[] = [
  { label: "Add employee", sub: "Open the new-employee form", to: "/employees?new=1", icon: Plus, roles: HR_WRITE, kw: "new hire create staff" },
  { label: "Invite new hire", sub: "Start onboarding for someone joining", to: "/onboarding", icon: UserPlus, roles: HR_WRITE, kw: "onboarding joiner" },
  { label: "Run payroll", sub: "Open this month's payroll run", to: "/payroll", icon: Wallet, roles: PAYROLL_ACCESS, kw: "salary process pay run", module: "payroll" },
  { label: "Apply leave", sub: "Request time off", to: "/leave?apply=1", icon: CalendarDays, roles: ALL_STAFF, kw: "request leave time off vacation holiday", module: "leave" },
  { label: "Ask HR", sub: "Open a helpdesk ticket", to: "/engage/helpdesk?new=1", icon: LifeBuoy, roles: ALL_STAFF, kw: "ticket help question grievance" },
  { label: "Raise a request", sub: "ID card, work from home, letters…", to: "/raise-request", icon: Send, roles: ALL_STAFF, kw: "service request" },
  { label: "Raise requisition", sub: "Ask for a new position", to: "/hiring/requisitions", icon: ClipboardList, roles: [...HR_WRITE, "manager"], kw: "hiring position opening", module: "talent" },
  { label: "Approvals", sub: "Things waiting for your decision", to: "/approvals", icon: CheckSquare, roles: ALL_STAFF, kw: "approve pending inbox" },
  { label: "My pay stubs", sub: "Download a pay stub", to: "/my-pay", icon: Gift, roles: ALL_STAFF, kw: "salary slip payslip pay stub t4" },
];

// ── sessionStorage (this tab only; cleared when it closes) ──
const RECENT_Q = "lx-search-recent";
const RECENT_OPEN = "lx-search-opened";
interface Opened { title: string; sub: string; to: string; kind: string }
function readSS<T>(k: string, d: T): T { try { return JSON.parse(sessionStorage.getItem(k) || "") as T; } catch { return d; } }
function writeSS(k: string, v: unknown) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } }

const SUGGEST: { q: string; roles?: string[] }[] = [
  { q: "pay stub" }, { q: "apply leave" }, { q: "t4" }, { q: "holidays" }, { q: "ask hr" },
  { q: "letterhead", roles: HR_WRITE },
  { q: "run payroll", roles: PAYROLL_ACCESS }, { q: "statutory values", roles: PAYROLL_ACCESS }, { q: "approvals" },
];

type Row =
  | { key: string; section: "pages"; r: Ranked }
  | { key: string; section: "people" | "records"; h: SearchHit }
  | { key: string; section: "actions"; a: Action }
  | { key: string; section: "recent"; q: string }
  | { key: string; section: "opened"; o: Opened };

function useIsDesktop() {
  const get = () => typeof window !== "undefined" && window.matchMedia("(min-width: 768px)").matches;
  const [d, setD] = useState(get);
  useEffect(() => {
    const m = window.matchMedia("(min-width: 768px)");
    const h = () => setD(m.matches);
    m.addEventListener?.("change", h);
    return () => m.removeEventListener?.("change", h);
  }, []);
  return d;
}

/** Debounced server search; stale answers are dropped (AbortController). */
function useServerSearch(q: string, enabled: boolean) {
  const [state, setState] = useState<{ q: string; data: SearchResponse | null; loading: boolean }>({ q: "", data: null, loading: false });
  useEffect(() => {
    const needle = q.trim();
    if (!enabled || needle.length < 2) { setState({ q: needle, data: null, loading: false }); return; }
    const ctl = new AbortController();
    setState((s) => ({ ...s, loading: true }));
    const t = window.setTimeout(async () => {
      try {
        const r = await api.get<SearchResponse>("/search", { params: { q: needle, limit: 8 }, signal: ctl.signal });
        setState({ q: needle, data: r.data, loading: false });
      } catch {
        if (!ctl.signal.aborted) setState({ q: needle, data: null, loading: false });
      }
    }, 160);
    return () => { ctl.abort(); window.clearTimeout(t); };
  }, [q, enabled]);
  return state;
}

export default function CommandPalette({ nav: groups, live }: { nav: PaletteNavGroup[]; live?: boolean }) {
  const t = useT();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const role = useUserRole() || "";
  const desktop = useIsDesktop();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [filter, setFilter] = useState("all");
  const [recent, setRecent] = useState<string[]>(() => readSS(RECENT_Q, []));
  const [opened, setOpened] = useState<Opened[]>(() => readSS(RECENT_OPEN, []));
  const inputRef = useRef<HTMLInputElement>(null);
  const sheetInputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const country = (user?.country || "CA").toUpperCase();
  const modules = user?.modules || null;

  // dev check: a menu route without a search-index entry (scripts/check_search_index.py is the build gate)
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const miss = missingIndexRoutes(groups);
    if (miss.length) console.error("[global search] menu routes missing from search/searchIndex.ts:", miss);
  }, [groups]);

  // 2026-10-26: custom roles find the screens their permissions open (same rule as the menu: navItemAllowed)
  const allow = useCallback((to: string, roles: string[]) => navItemAllowed({ to, roles }, user), [user]);
  const index = useMemo(() => buildIndex(groups, { role, country, modules, live, allow }, tx), [groups, role, country, modules, live, allow]);
  const needle = q.trim();
  const [pages, clientMs] = useMemo(() => {
    const t0 = performance.now();
    const r = needle ? rank(index, needle, 30) : [];
    return [r, Math.round((performance.now() - t0) * 100) / 100] as const;
  }, [index, needle]);
  const server = useServerSearch(q, open && role !== "superadmin");
  const hits = server.q === needle ? server.data?.hits || [] : [];

  const actions = useMemo(() => ACTIONS.filter((a) => role !== "superadmin" && allow(a.to, a.roles)
    && (!a.country || a.country === country) && (!a.module || !modules || modules.includes(a.module))), [role, country, modules, allow]);
  const actionHits = useMemo(() => {
    if (!needle) return actions.slice(0, 6);
    const n = needle.toLowerCase();
    return actions.filter((a) => matches(`${a.label} ${a.sub} ${a.kw}`.toLowerCase(), n)).slice(0, 5);
  }, [actions, needle]);

  // chips: All · Pages · People · each record type present · Actions
  const recordTypes = useMemo(() => {
    const m = new Map<string, number>();
    for (const h of hits) if (h.type !== "employee") m.set(h.type, (m.get(h.type) || 0) + 1);
    return [...m.entries()];
  }, [hits]);
  const people = hits.filter((h) => h.type === "employee");
  const chips: { key: string; label: string; n?: number }[] = needle ? [
    { key: "all", label: t("search.all") },
    ...(pages.length ? [{ key: "pages", label: t("search.pages"), n: pages.length }] : []),
    ...(people.length ? [{ key: "people", label: t("search.people"), n: people.length }] : []),
    ...recordTypes.map(([k, n]) => ({ key: `type:${k}`, label: typeLabel(k), n })),
    ...(actionHits.length ? [{ key: "actions", label: t("search.actions"), n: actionHits.length }] : []),
  ] : [];
  const activeFilter = chips.some((c) => c.key === filter) ? filter : "all";

  const rows: Row[] = useMemo(() => {
    const out: Row[] = [];
    if (!needle) {
      recent.slice(0, 5).forEach((r) => out.push({ key: `rq:${r}`, section: "recent", q: r }));
      opened.slice(0, 5).forEach((o) => out.push({ key: `ro:${o.to}`, section: "opened", o }));
      actionHits.forEach((a) => out.push({ key: `a:${a.to}`, section: "actions", a }));
      return out;
    }
    const all = activeFilter === "all";
    if (all || activeFilter === "pages") pages.slice(0, all ? 7 : 30).forEach((r) => out.push({ key: `p:${r.e.id}`, section: "pages", r }));
    if (all || activeFilter === "people") people.slice(0, all ? 6 : 25).forEach((h) => out.push({ key: `h:${h.type}:${h.id}`, section: "people", h }));
    const recs = hits.filter((h) => h.type !== "employee" && (all || activeFilter === `type:${h.type}`));
    // group records by type (keeps the server's best-first order inside each type)
    const order: string[] = [];
    for (const h of recs) if (!order.includes(h.type)) order.push(h.type);
    for (const ty of order) recs.filter((h) => h.type === ty).slice(0, all ? 4 : 25)
      .forEach((h) => out.push({ key: `h:${h.type}:${h.id}`, section: "records", h }));
    if (all || activeFilter === "actions") actionHits.forEach((a) => out.push({ key: `a:${a.to}`, section: "actions", a }));
    return out;
  }, [needle, recent, opened, actionHits, pages, people, hits, activeFilter]);

  useEffect(() => { setCursor(0); }, [needle, activeFilter]);
  useEffect(() => { listRef.current?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`)?.scrollIntoView({ block: "nearest" }); }, [cursor]);

  const focusInput = useCallback(() => {
    window.setTimeout(() => (desktop ? inputRef.current : sheetInputRef.current)?.focus(), 0);
  }, [desktop]);
  const openBox = useCallback(() => { setOpen(true); focusInput(); }, [focusInput]);
  const close = useCallback(() => { setOpen(false); setFilter("all"); (document.activeElement as HTMLElement | null)?.blur?.(); }, []);

  // Ctrl/⌘+K anywhere; "/" when not typing in a field
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); if (open) close(); else openBox(); return; }
      if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const el = e.target as HTMLElement | null;
        const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
        if (!typing) { e.preventDefault(); openBox(); }
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, openBox, close]);

  // desktop dropdown: click outside closes
  useEffect(() => {
    if (!open || !desktop) return;
    const h = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) close(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open, desktop, close]);

  const remember = (query: string, o: Opened | null) => {
    if (query.trim().length >= 2) {
      const r = [query.trim(), ...recent.filter((x) => x.toLowerCase() !== query.trim().toLowerCase())].slice(0, 8);
      setRecent(r); writeSS(RECENT_Q, r);
    }
    if (o) {
      const list = [o, ...opened.filter((x) => x.to !== o.to)].slice(0, 8);
      setOpened(list); writeSS(RECENT_OPEN, list);
    }
  };
  const go = (row: Row) => {
    if (row.section === "recent") { setQ(row.q); focusInput(); return; }
    let to = "", o: Opened | null = null;
    if (row.section === "pages") { to = row.r.e.to; o = { title: row.r.e.title, sub: row.r.e.crumbs.join(" › "), to, kind: "page" }; }
    else if (row.section === "people" || row.section === "records") { to = row.h.url; o = { title: row.h.title, sub: row.h.subtitle, to, kind: row.h.type }; }
    else if (row.section === "actions") { to = row.a.to; }
    else if (row.section === "opened") { to = row.o.to; o = row.o; }
    remember(q, o);
    setQ(""); close();
    if (to) navigate(to);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setCursor((c) => Math.min(c + 1, rows.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); const r = rows[cursor]; if (r) go(r); }
    else if (e.key === "Escape") { e.preventDefault(); if (q) setQ(""); else close(); }
    else if (e.key === "Tab" && chips.length > 1) {
      e.preventDefault();
      const i = chips.findIndex((c) => c.key === activeFilter);
      setFilter(chips[(i + (e.shiftKey ? chips.length - 1 : 1)) % chips.length].key);
    }
  };

  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");
  const keyLabel = isMac ? "⌘ K" : "Ctrl K";
  const partial = !!server.data?.partial && server.q === needle;
  const suggestions = SUGGEST.filter((s) => (!s.roles || s.roles.includes(role) || s.roles.some((r) => (user?.ui_roles || []).includes(r))) && rank(index, s.q, 1).length).slice(0, 6);

  const sectionLabel: Record<Row["section"], string> = {
    pages: t("search.pages"), people: t("search.people"), records: t("search.records"), actions: t("search.actions"),
    recent: t("search.recent"), opened: t("search.opened"),
  };

  const results = (
    <div className="flex flex-col min-h-0 flex-1" data-testid="search-results" data-client-ms={clientMs}
      data-server-ms={server.data?.took_ms ?? ""} data-loading={server.loading ? "1" : "0"}>
      {chips.length > 1 && (
        <div className="flex items-center gap-1.5 px-3 py-2 border-b border-surface-border overflow-x-auto scrollbar-thin" role="tablist">
          {chips.map((c) => (
            <button key={c.key} type="button" role="tab" aria-selected={activeFilter === c.key} onMouseDown={(e) => e.preventDefault()}
              onClick={() => setFilter(c.key)} data-chip={c.key}
              className={clsx("shrink-0 px-2.5 py-1 rounded-full text-[11px] font-semibold border transition-colors",
                activeFilter === c.key ? "bg-primary-600 border-primary-600 text-white" : "bg-white border-surface-border text-gray-600 hover:border-primary-300")}>
              {c.label}{c.n != null && <span className={clsx("ml-1 tabular-nums", activeFilter === c.key ? "text-white/80" : "text-gray-400")}>{c.n}</span>}
            </button>
          ))}
        </div>
      )}
      {partial && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] text-amber-700 bg-amber-50 border-b border-amber-100" data-testid="search-partial">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" /> {t("search.slow")}
        </div>
      )}
      <div ref={listRef} className="flex-1 overflow-y-auto py-1" role="listbox" aria-label={t("search.short")}>
        {(() => {
          let last = "";
          return rows.map((row, idx) => {
            const head = row.section === "records" ? `records:${row.h.type}` : row.section;
            const showHead = head !== last; last = head;
            const label = row.section === "records" ? typeLabel(row.h.type) : sectionLabel[row.section];
            let title = "", sub = "", Icon: LucideIcon = Search, avatar: string | null = null, badge: string | null | undefined = null;
            if (row.section === "pages") { title = row.r.e.title; sub = row.r.e.crumbs.join(" › "); Icon = row.r.e.icon || FileText; }
            else if (row.section === "people") { title = row.h.title; sub = row.h.subtitle; avatar = row.h.title; badge = row.h.badge === "you" ? t("search.you") : row.h.badge; }
            else if (row.section === "records") { title = row.h.title; sub = row.h.subtitle; Icon = TYPE_META[row.h.type]?.icon || FileText; badge = row.h.badge; }
            else if (row.section === "actions") { title = tx(`search.action.${row.a.label}`, row.a.label); sub = row.a.sub; Icon = row.a.icon; }
            else if (row.section === "recent") { title = row.q; Icon = History; }
            else if (row.section === "opened") { title = row.o.title; sub = row.o.sub; Icon = row.o.kind === "employee" ? User : ArrowRight; }
            const parts = title.split(" ");
            return (
              <div key={row.key}>
                {showHead && <div className="px-3 pt-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400 flex items-center justify-between">
                  <span>{label}</span>
                  {(row.section === "recent" || row.section === "opened") && <button type="button" className="normal-case tracking-normal font-medium text-gray-400 hover:text-primary-600"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { if (row.section === "recent") { setRecent([]); writeSS(RECENT_Q, []); } else { setOpened([]); writeSS(RECENT_OPEN, []); } }}>{t("search.clear")}</button>}
                </div>}
                <button type="button" role="option" aria-selected={idx === cursor} data-idx={idx} data-section={row.section}
                  data-type={row.section === "people" || row.section === "records" ? row.h.type : row.section}
                  onMouseEnter={() => setCursor(idx)} onMouseDown={(e) => e.preventDefault()} onClick={() => go(row)}
                  className={clsx("w-full flex items-center gap-3 px-3 py-2 text-left transition-colors", idx === cursor ? "bg-primary-50" : "hover:bg-gray-50")}>
                  {avatar
                    ? <span className={clsx("w-7 h-7 rounded-full grid place-items-center text-[11px] font-bold shrink-0", avatarClass(row.key))}>
                        {`${parts[0]?.[0] || ""}${parts[parts.length - 1]?.[0] || ""}`.toUpperCase()}</span>
                    : <span className="w-7 h-7 rounded-lg grid place-items-center bg-surface text-gray-500 shrink-0"><Icon className="w-4 h-4" /></span>}
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 min-w-0">
                      <span className="text-sm text-gray-800 truncate">{title}</span>
                      {badge && <span className="shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-700 border border-primary-100">{badge.replace(/_/g, " ")}</span>}
                    </span>
                    {sub && <span className="block text-xs text-gray-400 truncate">{sub}</span>}
                  </span>
                  {idx === cursor && <CornerDownLeft className="w-3.5 h-3.5 text-gray-400 shrink-0" aria-hidden="true" />}
                </button>
              </div>
            );
          });
        })()}
        {needle && server.loading && !hits.length && (
          <div className="px-3 py-2 text-xs text-gray-400 flex items-center gap-2" data-testid="search-loading">
            <span className="w-3 h-3 rounded-full border-2 border-primary-300 border-t-transparent animate-spin" /> {t("search.searching")}
          </div>
        )}
        {needle && !rows.length && !server.loading && (
          <div className="px-4 py-8 text-center" data-testid="search-empty">
            <div className="text-sm text-gray-600">{t("search.none", { q: needle })}</div>
            <div className="text-xs text-gray-400 mt-1">{t("search.none_hint")}</div>
          </div>
        )}
        {(!needle || (!rows.length && !server.loading)) && suggestions.length > 0 && (
          <div className="px-3 pt-2 pb-3">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1.5">{t("search.try")}</div>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((s) => (
                <button key={s.q} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { setQ(s.q); focusInput(); }}
                  className="px-2.5 py-1 rounded-full text-xs border border-surface-border bg-white text-gray-600 hover:border-primary-300 hover:text-primary-700">{s.q}</button>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="hidden sm:flex items-center gap-3 px-3 py-1.5 border-t border-surface-border text-[10px] text-gray-400">
        <span><kbd className="px-1 rounded border border-surface-border">↑</kbd> <kbd className="px-1 rounded border border-surface-border">↓</kbd> {t("search.hint_move")}</span>
        <span><kbd className="px-1 rounded border border-surface-border">Enter</kbd> {t("search.hint_open")}</span>
        <span><kbd className="px-1 rounded border border-surface-border">Tab</kbd> {t("search.hint_filter")}</span>
        <span><kbd className="px-1 rounded border border-surface-border">Esc</kbd> {t("search.hint_close")}</span>
        <span className="ml-auto">{t("search.pages")} · {t("search.people")} · {t("search.records")} · {t("search.actions")}</span>
      </div>
    </div>
  );

  if (desktop) {
    return (
      <div ref={boxRef} className="relative w-full max-w-2xl" data-testid="command-palette">
        <div className={clsx("flex items-center gap-2 px-2.5 h-8 rounded-lg border bg-surface transition-colors",
          open ? "border-primary-400 ring-2 ring-primary-100 bg-white" : "border-surface-border hover:border-primary-300")}>
          <Search className="w-3.5 h-3.5 text-gray-400 shrink-0" />
          <input ref={inputRef} value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} onKeyDown={onKey}
            placeholder={t("search.placeholder")} aria-label={t("search.placeholder")} data-testid="global-search" role="combobox"
            aria-expanded={open} aria-controls="lx-search-panel" autoComplete="off" spellCheck={false}
            className="flex-1 min-w-0 bg-transparent outline-none text-xs text-gray-800 placeholder:text-gray-400" />
          {q
            ? <button type="button" className="text-gray-400 hover:text-gray-700" aria-label={t("search.clear")} onMouseDown={(e) => e.preventDefault()} onClick={() => { setQ(""); inputRef.current?.focus(); }}><X className="w-3.5 h-3.5" /></button>
            : <kbd className="px-1.5 py-0.5 rounded border border-surface-border bg-white text-[10px] font-semibold text-gray-500 tabular-nums shrink-0">{keyLabel}</kbd>}
        </div>
        {open && (
          <div id="lx-search-panel" className="absolute left-0 right-0 top-full mt-1.5 z-[70] bg-white rounded-xl shadow-2xl border border-surface-border flex flex-col max-h-[72vh] min-w-[28rem]">
            {results}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      <button type="button" onClick={openBox} aria-label={t("search.short")} title={`${t("search.short")} (${keyLabel})`} data-testid="global-search-icon"
        className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 shrink-0">
        <Search className="w-4 h-4" />
      </button>
      {open && (
        <div className="fixed inset-0 z-[70] bg-white flex flex-col" role="dialog" aria-modal="true" aria-label={t("search.short")} data-testid="command-palette">
          <div className="flex items-center gap-2 px-3 h-14 border-b border-surface-border shrink-0">
            <Search className="w-4 h-4 text-gray-400 shrink-0" />
            <input ref={sheetInputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} autoFocus
              placeholder={t("search.placeholder")} aria-label={t("search.placeholder")} data-testid="global-search" autoComplete="off" spellCheck={false}
              className="flex-1 min-w-0 bg-transparent outline-none text-base text-gray-800 placeholder:text-gray-400" />
            <button type="button" onClick={close} className="icon-btn text-gray-500" aria-label={t("search.hint_close")}><X className="w-5 h-5" /></button>
          </div>
          {results}
        </div>
      )}
    </>
  );
}
