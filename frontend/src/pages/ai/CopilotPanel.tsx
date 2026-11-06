import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { X, Send, Sparkles, ShieldCheck, Check, Ban, BookOpen, AlertTriangle, ExternalLink, ThumbsUp, ThumbsDown, UserRound, LifeBuoy, Lock } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { assistantApi, type AiAction, type AiCitation, type AiCap } from "../../api/ai";
import { errText } from "../../api/platform";
import { deskApi, type DeskCitation, type DeskPayStructure, type DeskReply } from "./desk/api";
import { HR_WRITE } from "../../store/authStore";
import { useCan } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** Ask Lexi — the employee chat. Every question goes through the HR Desk first (server: desk.dispatch):
 *  hard router (sexual harassment / medical / grievance … → fixed message, sealed harassment referral) → pay-change diff (table + narration) →
 *  approved policy clauses (cited chips) → the tool-calling assistant (leave balance, holidays, approvers …) → abstain.
 *  Citations, the AI disclosure line, "Ask a person", thumbs and the hand-off ticket come from the `desk` block of the reply. */
interface Msg { role: "user" | "assistant"; content: string; mode?: string; note?: string; tools?: string[]; masked?: number; citations?: AiCitation[]; cited?: string[]; desk?: DeskReply | null; rated?: "up" | "down"; }

const SUGGESTIONS = ["My leave balance", "Why is my salary less this month?", "How many casual leaves do I get?", "Who approves my leave?",
  "Can I work from home two days a week?", "Explain every line of my latest pay stub", "Raise a ticket: T4 missing"];
import { Monogram } from "../../components/Logo";
const ASK_PERSON = "I would like to talk to a person about this.";

export default function CopilotPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<AiAction[]>([]);
  const [cap, setCap] = useState<AiCap | null>(null);
  const [sources, setSources] = useState<AiCitation[] | null>(null);
  const [deskSession, setDeskSession] = useState<string | null>(null);     // one desk session per panel conversation (server: desk_sessions)
  const scrollRef = useRef<HTMLDivElement>(null);
  const canOpenClauses = useCan(["ai.edit", "ai.admin"], HR_WRITE);
  const status = useQuery({ queryKey: ["assistant", "status"], queryFn: assistantApi.status, enabled: open });
  useEffect(() => { if (status.data) setCap(status.data.cap); }, [status.data]);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [messages, busy, pending]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    const next: Msg[] = [...messages, { role: "user", content: q }];
    setMessages(next); setInput(""); setBusy(true);
    try {
      const res = await assistantApi.chat(q, history, deskSession);
      if (res.desk?.session_id) setDeskSession(res.desk.session_id);
      setMessages([...next, { role: "assistant", content: res.reply, mode: res.mode, note: res.note, tools: res.tools_used, masked: res.masked, citations: res.citations, cited: res.cited_ids, desk: res.desk || null }]);
      if (res.actions?.length) setPending((p) => [...p.filter((a) => a.status === "pending"), ...res.actions]);
      setCap(res.cap);
    } catch (e) {
      setMessages([...next, { role: "assistant", content: errText(e, "The assistant did not answer."), mode: "error" }]);
    } finally { setBusy(false); }
  };
  const decide = async (a: AiAction, ok: boolean) => {
    setBusy(true);
    try {
      const r = ok ? await assistantApi.confirm(a.id) : await assistantApi.cancel(a.id);
      setMessages((m) => [...m, { role: "assistant", content: r.message + (r.executed && r.link ? ` Open: ${r.link}` : ""), mode: "action" }]);
      if (r.status === "pending") setPending((p) => p.map((x) => (x.id === a.id ? { ...x, confirmations: r.confirmations } : x)));
      else setPending((p) => p.filter((x) => x.id !== a.id));
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: errText(e), mode: "error" }]);
      setPending((p) => p.filter((x) => x.id !== a.id));
    } finally { setBusy(false); }
  };
  const rate = async (i: number, rating: "up" | "down") => {
    const m = messages[i];
    if (!m.desk?.session_id || m.rated) return;
    setMessages((all) => all.map((x, j) => (j === i ? { ...x, rated: rating } : x)));
    try { await deskApi.feedback({ session_id: m.desk.session_id, turn_id: m.desk.turn_id, rating }); } catch { /* best effort — the thumbs stay */ }
    if (rating === "down") await send(ASK_PERSON);
  };

  const live = !!status.data?.configured;
  return (
    <>
      {open && <div className="fixed inset-0 z-[55] bg-black/30 sm:hidden" onClick={onClose} />}
      <aside className={`fixed right-0 top-0 bottom-0 z-[60] w-full sm:w-[30rem] bg-white border-l border-surface-border shadow-2xl flex flex-col transition-transform duration-200 ${open ? "translate-x-0" : "translate-x-full"}`}>
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="w-8 h-8 rounded-full bg-primary-950 flex items-center justify-center shrink-0 overflow-hidden"><Monogram className="w-7 h-7" title="Lexi" /></span>
            <div>
              <h2 className="font-display font-semibold text-gray-800 leading-tight">Ask Lexi</h2>
              <p className="text-[11px] leading-tight">
                {status.isLoading ? <span className="text-gray-400">checking…</span>
                  : live ? <span className="text-emerald-700">HR Desk · {status.data?.model} · key: {status.data?.key_source}</span>
                  : <span className="text-amber-700">HR Desk · no model key — policy clauses are quoted, not generated</span>}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="icon-btn text-gray-400" aria-label="Close"><X className="w-4 h-4" /></button>
        </div>
        <div className="px-4 py-1.5 border-b border-surface-border flex items-center justify-between text-[11px] text-gray-500">
          <span className="inline-flex items-center gap-1" title={status.data?.shield}><ShieldCheck className="w-3.5 h-3.5 text-emerald-600" /> Identifiers and amounts are masked before any AI call</span>
          {cap && cap.cap_tokens > 0 && <span className={`tabular-nums ${cap.over ? "text-red-700" : cap.warn ? "text-amber-700" : ""}`} title={`Monthly cap ${cap.cap_tokens.toLocaleString()} tokens (${cap.mode})`}>
            Month {cap.used_tokens.toLocaleString()} / {cap.cap_tokens.toLocaleString()}{cap.blocked ? " · cap reached" : ""}</span>}
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 scrollbar-thin">
          {messages.length === 0 && (
            <div className="text-center py-4">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-500 to-teal-500 flex items-center justify-center mx-auto mb-3"><Sparkles className="w-6 h-6 text-white" /></div>
              <p className="text-sm font-semibold text-gray-700">Ask about leave, pay, attendance, approvals or policy</p>
              <p className="text-xs text-gray-400 mt-1 mb-4">{status.data?.note || status.data?.message || "Policy answers cite the approved clause; pay answers show the pay stub lines behind them. You can ask a person at any time."}</p>
              {status.data && !status.data.linked_employee && <p className="text-xs text-amber-700 mb-3">Your login is not linked to an employee record — leave and pay stub questions will say so.</p>}
              <div className="space-y-2">{SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} className="w-full text-left text-xs px-3 py-2 rounded-lg bg-gray-50 hover:bg-primary-50 hover:text-primary-700 text-gray-600 border border-surface-border">{s}</button>))}</div>
            </div>
          )}
          {messages.map((m, i) => m.role === "user" ? (
            <div key={i} className="flex justify-end"><div className="max-w-[90%] px-3 py-2 rounded-2xl text-sm whitespace-pre-wrap bg-primary-600 text-white rounded-br-sm">{m.content}</div></div>
          ) : m.desk?.kind === "hard_route" ? (
            <FixedMessage key={i} m={m} />
          ) : (
            <div key={i} className="flex justify-start">
              <div className="max-w-[92%] px-3 py-2 rounded-2xl text-sm whitespace-pre-wrap bg-gray-50 text-gray-700 border border-surface-border rounded-bl-sm" data-desk-kind={m.desk?.kind || ""}>
                {m.desk?.structure && <PayChangeTable s={m.desk.structure} />}
                {renderReply(m.content, m.citations || [], m.desk?.citations || [], () => setSources(m.citations || []), canOpenClauses)}
                {m.desk?.citations?.length ? (
                  <div className="mt-2 flex flex-wrap gap-1" data-testid="desk-citations">
                    {m.desk.citations.map((c, k) => <CitationChip key={k} c={c} canOpen={canOpenClauses} />)}
                  </div>) : null}
                {m.desk?.ticket && (
                  <Link to={m.desk.ticket.link} className="mt-2 inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg bg-sky-50 border border-sky-200 text-sky-800">
                    <LifeBuoy className="w-3.5 h-3.5" /> Ticket {m.desk.ticket.number || ""} · {m.desk.ticket.category}{m.desk.ticket.sla_hours ? ` · reply within ${m.desk.ticket.sla_hours} h` : ""} <ExternalLink className="w-3 h-3" />
                  </Link>)}
                {m.desk?.ask_hr && !m.desk.ticket && (
                  <Link to={m.desk.ask_hr.link} className="mt-2 inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg bg-amber-50 border border-amber-200 text-amber-800">
                    <LifeBuoy className="w-3.5 h-3.5" /> Ask HR — open a ticket <ExternalLink className="w-3 h-3" />
                  </Link>)}
                {(m.mode || m.note || m.desk) && (
                  <div className="mt-1.5 text-[10px] text-gray-400 border-t border-surface-border pt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span>{m.desk?.kind === "pay_change" ? (m.desk.narrated ? "Pay stub diff · AI narration" : "Pay stub diff · computed by payroll")
                      : m.desk?.kind === "answer" ? (m.mode === "live" ? "Policy answer · AI, validated against the clauses" : "Policy answer · clause quoted")
                      : m.desk?.kind === "abstain" ? "No grounded answer" : m.desk?.kind === "refused" ? "Refused"
                      : m.mode === "live" ? "AI answer" : m.mode === "rules" ? "Rules-based answer" : m.mode === "action" ? "Action" : m.mode === "refused" ? "Refused" : m.mode === "error" ? "Error" : "Answer"}</span>
                    {m.tools?.length ? <span>· used {m.tools.join(", ")}</span> : null}
                    {typeof m.masked === "number" && m.masked > 0 ? <span>· {m.masked} value(s) masked</span> : null}
                    {m.citations?.length ? <button className="inline-flex items-center gap-1 text-primary-600 hover:underline" onClick={() => setSources(m.citations || [])}><BookOpen className="w-3 h-3" /> {m.citations.length} record(s)</button> : null}
                    {m.desk && m.desk.kind !== "tools" && m.mode !== "error" && (
                      <span className="ml-auto inline-flex items-center gap-1.5">
                        {m.desk.csat && !m.rated && <>
                          <button className="icon-btn !p-0.5 text-gray-400 hover:text-emerald-600" title="Yes, this answered my question" aria-label="Helpful" onClick={() => rate(i, "up")} data-testid="desk-thumb-up"><ThumbsUp className="w-3.5 h-3.5" /></button>
                          <button className="icon-btn !p-0.5 text-gray-400 hover:text-red-600" title="No — ask a person" aria-label="Not helpful" onClick={() => rate(i, "down")} data-testid="desk-thumb-down"><ThumbsDown className="w-3.5 h-3.5" /></button>
                        </>}
                        {m.rated && <span className={m.rated === "up" ? "text-emerald-600" : "text-red-600"}>{m.rated === "up" ? "Thanks — marked as answered" : "Noted — a person will take it"}</span>}
                      </span>)}
                    {m.desk && m.desk.kind !== "tools" && !m.desk.ticket && m.mode !== "error" && (
                      <button className="inline-flex items-center gap-1 text-primary-600 hover:underline" onClick={() => send(ASK_PERSON)} data-testid="desk-ask-person"><UserRound className="w-3 h-3" /> Ask a person</button>)}
                    {m.desk?.disclosure ? <div className="w-full text-gray-400 italic" data-testid="desk-disclosure">{m.desk.disclosure}</div> : null}
                    {m.note ? <div className="w-full text-amber-700">{m.note}</div> : null}
                  </div>)}
              </div>
            </div>
          ))}
          {pending.filter((a) => a.status === "pending").map((a) => <ActionCard key={a.id} a={a} busy={busy} onDecide={decide} />)}
          {busy && <div className="flex justify-start"><div className="bg-gray-50 border border-surface-border rounded-2xl rounded-bl-sm px-3 py-2.5"><span className="flex gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" /><span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: "120ms" }} /><span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: "240ms" }} /></span></div></div>}
        </div>

        {sources && (
          <div className="border-t border-surface-border bg-gray-50 max-h-[40%] overflow-y-auto">
            <div className="px-4 py-2 flex items-center justify-between text-xs font-semibold text-gray-700"><span className="inline-flex items-center gap-1"><BookOpen className="w-3.5 h-3.5" /> Records read for this answer</span>
              <button className="icon-btn text-gray-400" onClick={() => setSources(null)} aria-label="Close sources"><X className="w-3.5 h-3.5" /></button></div>
            {sources.length === 0 && <p className="px-4 pb-3 text-xs text-gray-500">No records were read for this answer.</p>}
            <ul className="px-4 pb-3 space-y-1.5">{sources.map((c) => (
              <li key={c.id} className="text-xs bg-white border border-surface-border rounded-lg px-2.5 py-1.5">
                <div className="flex items-center gap-2"><span className="font-mono text-[10px] px-1 rounded bg-primary-50 text-primary-700">{c.id}</span>
                  <span className="font-medium text-gray-800">{c.label}</span>
                  {c.link && <Link to={c.link} className="ml-auto text-primary-600 inline-flex items-center gap-0.5 hover:underline">open <ExternalLink className="w-3 h-3" /></Link>}</div>
                <div className="text-[10px] text-gray-500">{c.system} · tool {c.tool}{c.records?.length ? ` · ${c.records.length} record(s)` : ""}</div>
              </li>))}</ul>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="border-t border-surface-border p-3 flex items-center gap-2">
          <input className="input !py-2" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about leave, pay, attendance, policy…" disabled={busy} aria-label="Message" />
          <button type="submit" disabled={busy || !input.trim()} className="btn-primary !px-3 !py-2 disabled:opacity-50" aria-label="Send"><Send className="w-4 h-4" /></button>
        </form>
      </aside>
    </>
  );
}

/** A hard-routed reply: a fixed message from the company, not an AI answer — calm, no bubble, no typing feel. */
function FixedMessage({ m }: { m: Msg }) {
  const d = m.desk!;
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3 text-sm text-slate-800" data-desk-kind="hard_route" data-hard-class={d.hard_class || ""}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-1.5"><Lock className="w-3.5 h-3.5" /> Fixed message from your company</div>
      <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
      {d.ticket && <Link to={d.ticket.link} className="mt-2 inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg bg-white border border-slate-200 text-slate-700"><LifeBuoy className="w-3.5 h-3.5" /> Confidential ticket {d.ticket.number || ""}{d.ticket.sla_hours ? ` · a person replies within ${d.ticket.sla_hours} h` : ""}</Link>}
      {d.ask_hr && !d.ticket && <Link to={d.ask_hr.link} className="mt-2 inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg bg-white border border-slate-200 text-slate-700"><LifeBuoy className="w-3.5 h-3.5" /> Ask HR — open a confidential ticket</Link>}
      <div className="mt-2 text-[10px] text-slate-500 italic" data-testid="desk-disclosure">{d.disclosure || "This is a fixed message from your company, not an AI-generated answer."}{d.ic_referral ? " Nothing you wrote was stored; only the Internal Committee can see that you asked." : ""}</div>
    </div>
  );
}

function CitationChip({ c, canOpen }: { c: DeskCitation; canOpen: boolean }) {
  const label = c.policy === "payroll" ? (c.label || "Pay stub") : `${c.policy || "Policy"} · cl. ${c.clause_id || "?"}${c.version ? ` v${c.version}` : ""}`;
  const title = c.label || label;
  const cls = "inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border border-primary-200 bg-primary-50 text-primary-700";
  if (c.deep_link && (canOpen || c.policy === "payroll")) return <Link to={c.deep_link} className={cls + " hover:bg-primary-100"} title={title} data-testid="desk-citation"><BookOpen className="w-3 h-3" /> {label}</Link>;
  return <span className={cls} title={title + (c.effective_from ? ` — effective ${c.effective_from}` : "")} data-testid="desk-citation"><BookOpen className="w-3 h-3" /> {label}</span>;
}

/** The deterministic payslip diff above the narration: one row per changed line, the rule behind each, net change. */
function PayChangeTable({ s }: { s: DeskPayStructure }) {
  const f = (n: number) => n.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = (n: number) => (n > 0 ? "+" : "") + f(n);
  return (
    <div className="mb-2 -mx-1 overflow-x-auto" data-testid="desk-pay-table">
      <table className="w-full text-[11px] tabular-nums">
        <thead><tr className="text-left text-gray-400 uppercase tracking-wide"><th className="px-1 py-0.5 font-semibold">Line</th><th className="px-1 py-0.5 text-right font-semibold">{s.prev_period || "—"}</th><th className="px-1 py-0.5 text-right font-semibold">{s.period}</th><th className="px-1 py-0.5 text-right font-semibold">Δ</th><th className="px-1 py-0.5 font-semibold">Rule</th></tr></thead>
        <tbody>
          {s.lines.map((l) => (
            <tr key={l.component} className="border-t border-surface-border align-top" title={l.rule_text}>
              <td className="px-1 py-0.5 text-gray-700 whitespace-nowrap">{l.label}</td>
              <td className="px-1 py-0.5 text-right text-gray-500">{f(l.prev)}</td>
              <td className="px-1 py-0.5 text-right text-gray-800">{f(l.curr)}</td>
              <td className={`px-1 py-0.5 text-right font-medium ${l.delta < 0 ? "text-red-600" : "text-emerald-700"}`}>{sign(l.delta)}</td>
              <td className="px-1 py-0.5"><span className={`font-mono text-[9px] px-1 rounded ${l.rule_code === "UNEXPLAINED" ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}>{l.rule_code}</span></td>
            </tr>))}
          <tr className="border-t-2 border-surface-border font-semibold"><td className="px-1 py-0.5 text-gray-800">Net pay</td><td className="px-1 py-0.5" /><td className="px-1 py-0.5" /><td className={`px-1 py-0.5 text-right ${s.net_delta < 0 ? "text-red-600" : "text-emerald-700"}`}>{sign(s.net_delta)}</td><td className="px-1 py-0.5 text-gray-400 font-normal">{s.lop_days ? `${s.lop_days} LOP day(s)` : ""}</td></tr>
        </tbody>
      </table>
      {s.needs_payroll_review && <div className="mt-1 text-[10px] text-amber-700 inline-flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> ${f(Math.abs(s.unexplained_delta))} is not explained by any rule — payroll has been asked to check.</div>}
    </div>
  );
}

/** The confirmation card: what, for whom, effect, risk; one or two explicit confirmations. */
function ActionCard({ a, busy, onDecide }: { a: AiAction; busy: boolean; onDecide: (a: AiAction, ok: boolean) => void }) {
  const c = a.card; const high = c.risk === "high"; const left = (a.requires_confirmations || 1) - (a.confirmations || 0);
  return (
    <div className={`card text-sm ${high ? "border-amber-300 bg-amber-50/50" : "border-primary-200 bg-primary-50/40"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="font-semibold text-gray-800">{high ? <span className="inline-flex items-center gap-1 text-amber-800"><AlertTriangle className="w-4 h-4" /> High-risk action — confirm twice</span> : "Confirm before anything is submitted"}</div>
        <span className="text-[10px] text-gray-400 whitespace-nowrap">{a.channel === "mcp" ? "via MCP" : "proposed by AI"}</span>
      </div>
      <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-x-2 gap-y-1 text-xs">
        <dt className="text-gray-500">What</dt><dd className="text-gray-800 font-medium">{c.what}</dd>
        <dt className="text-gray-500">For whom</dt><dd className="text-gray-800">{c.for_whom}</dd>
        <dt className="text-gray-500">Effect</dt><dd className="text-gray-700">{c.effect}</dd>
        {a.expires_at && <><dt className="text-gray-500">Expires</dt><dd className="text-gray-500">{new Date(a.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</dd></>}
      </dl>
      <div className="flex items-center gap-2 mt-3">
        <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1" disabled={busy} onClick={() => onDecide(a, true)}>
          <Check className="w-3.5 h-3.5" /> {left > 1 ? `Confirm (1 of ${a.requires_confirmations})` : a.confirmations > 0 ? "Confirm again — do it" : "Confirm"}</button>
        <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1" disabled={busy} onClick={() => onDecide(a, false)}><Ban className="w-3.5 h-3.5" /> Cancel</button>
        <span className="text-[10px] text-gray-400 ml-auto">Nothing is written until you confirm.</span>
      </div>
    </div>
  );
}

/** Links in-app paths; turns [S1] (record) and [C1] (policy clause) tags into clickable chips. Source footer lines are kept as plain text. */
function renderReply(text: string, citations: AiCitation[], clauses: DeskCitation[], openSources: () => void, canOpen: boolean): ReactNode {
  const parts = text.split(/(\[[SC]\d+\]|\/[a-z][a-z0-9\-/]*(?:\?[^\s)]*)?)/g);
  return parts.map((p, i) => {
    if (/^\[S\d+\]$/.test(p)) {
      const id = p.slice(1, -1); const c = citations.find((x) => x.id === id);
      return <button key={i} type="button" title={c ? `${c.label} — ${c.system}` : id} onClick={openSources}
        className="align-super text-[9px] font-mono px-1 rounded bg-primary-100 text-primary-700 hover:bg-primary-200 mx-0.5">{id}</button>;
    }
    if (/^\[C\d+\]$/.test(p)) {
      const tag = p.slice(1, -1); const c = clauses.find((x) => x.tag === tag);
      const cls = "align-super text-[9px] font-mono px-1 rounded bg-teal-100 text-teal-800 hover:bg-teal-200 mx-0.5";
      if (c?.deep_link && canOpen) return <Link key={i} to={c.deep_link} title={c.label} className={cls}>{tag}</Link>;
      return <span key={i} title={c?.label || tag} className={cls}>{tag}</span>;
    }
    if (/^\/[a-z]/.test(p)) return <Link key={i} to={p} className="text-primary-600 underline">{p}</Link>;
    return <span key={i}>{p}</span>;
  });
}
