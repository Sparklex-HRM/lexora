import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { History, ShieldCheck, ShieldAlert, Search, ChevronLeft, ChevronRight, Fingerprint } from "lucide-react";
import { PageHeader, Spinner, Empty, Card } from "../components/ui";
import { complianceApi } from "../api/endpoints";

const PAGE = 25;
const ACTION_TONE = (a: string) =>
  a.startsWith("payroll") ? "bg-primary-50 text-primary-700"
  : a.includes("safety") ? "bg-amber-50 text-amber-700"
  : a.includes("wcb") ? "bg-teal-50 text-teal-700"
  : "bg-gray-100 text-gray-600";
const fmtTs = (t?: string) => t ? new Date(t).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export default function AuditLogPage() {
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const { data, isLoading } = useQuery({
    queryKey: ["audit", q, action, from, to, page],
    queryFn: () => complianceApi.audit({ q: q || undefined, action: action || undefined,
      date_from: from || undefined, date_to: to || undefined, skip: page * PAGE, limit: PAGE }),
  });
  const verify = useMutation({ mutationFn: () => complianceApi.auditVerify() });
  const v = verify.data;
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div>
      <PageHeader title="Audit Log" icon={History} subtitle="Tamper-evident activity trail — HMAC hash chain across payroll & compliance"
        actions={<button className="btn-primary text-sm inline-flex items-center gap-1.5" disabled={verify.isPending} onClick={() => verify.mutate()}><Fingerprint className="w-4 h-4" />{verify.isPending ? "Verifying…" : "Verify integrity"}</button>} />

      {v && (
        v.intact
          ? <div className="mb-4 rounded-xl bg-green-50 border border-green-200 px-4 py-2.5 flex items-center gap-2 text-sm text-green-800"><ShieldCheck className="w-4 h-4" /><span className="font-medium">Chain intact.</span> All {v.chain_length} events verify against the {v.algorithm}. No row has been altered or deleted.</div>
          : <div className="mb-4 rounded-xl bg-red-50 border-2 border-red-300 px-4 py-2.5 text-sm text-red-800"><div className="flex items-center gap-2 font-semibold"><ShieldAlert className="w-4 h-4" />Chain broken — {v.breaks.length} issue(s) detected.</div><ul className="mt-1 ml-6 list-disc">{v.breaks.map((b: any, i: number) => <li key={i}>seq {b.seq}: {b.issue}</li>)}</ul></div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-300 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input className="input !py-1.5 !pl-8 !w-64 text-sm" placeholder="Search summary / actor / action" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        </div>
        <select className="input !py-1.5 !w-auto text-sm" value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }}>
          <option value="">All actions</option>
          <option value="payroll.run.execute">Payroll run</option>
          <option value="payroll.offcycle.execute">Off-cycle pay</option>
          <option value="payroll.finalpay">Final pay</option>
          <option value="payroll.ytd.adjust">YTD adjustment</option>
          <option value="compliance.safety.create,compliance.safety.update">Safety</option>
          <option value="compliance.wcb.rate_set">WCB rate</option>
        </select>
        <div className="flex items-center gap-1 text-xs text-gray-400">
          <input type="date" className="input !py-1.5 !w-auto text-sm" value={from} max={to || undefined} onChange={(e) => { setFrom(e.target.value); setPage(0); }} title="From date" />
          <span>–</span>
          <input type="date" className="input !py-1.5 !w-auto text-sm" value={to} min={from || undefined} onChange={(e) => { setTo(e.target.value); setPage(0); }} title="To date" />
          {(from || to) && <button className="text-gray-400 hover:text-gray-700 px-1" title="Clear dates" onClick={() => { setFrom(""); setTo(""); setPage(0); }}>✕</button>}
        </div>
        <span className="text-xs text-gray-400">{total} event{total === 1 ? "" : "s"}</span>
      </div>

      <Card padded={false}>
        {isLoading || !data ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : !data.items.length ? <Empty>No audit events yet — money and compliance actions will appear here.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                <tr><th className="px-5 py-2.5 font-semibold">#</th><th className="px-3 py-2.5 font-semibold">When</th><th className="px-3 py-2.5 font-semibold">Actor</th><th className="px-3 py-2.5 font-semibold">Action</th><th className="px-3 py-2.5 font-semibold">Detail</th><th className="px-4 py-2.5 font-semibold">Hash</th></tr>
              </thead>
              <tbody>
                {data.items.map((e: any) => (
                  <tr key={e.seq} className="border-t border-surface-border align-top">
                    <td className="px-5 py-2.5 tabular-nums text-gray-400">{e.seq}</td>
                    <td className="px-3 py-2.5 text-gray-600 whitespace-nowrap">{fmtTs(e.ts)}</td>
                    <td className="px-3 py-2.5 text-gray-700">{e.actor_email || "—"}<div className="text-[11px] text-gray-400 capitalize">{(e.actor_role || "").replace(/_/g, " ")}</div></td>
                    <td className="px-3 py-2.5"><span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${ACTION_TONE(e.action)}`}>{e.action}</span></td>
                    <td className="px-3 py-2.5 text-gray-700 max-w-[26rem]"><div>{e.summary}</div>{e.entity_id && <div className="text-[11px] text-gray-400">{e.entity_type}: {e.entity_id}</div>}</td>
                    <td className="px-4 py-2.5 font-mono text-[11px] text-gray-400" title={e.entry_hash}>{e.entry_hash?.slice(0, 10)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pages > 1 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-surface-border text-sm">
            <span className="text-gray-400">Page {page + 1} of {pages}</span>
            <div className="flex gap-1">
              <button className="icon-btn text-gray-400 disabled:opacity-40" disabled={page === 0} onClick={() => setPage((p) => p - 1)}><ChevronLeft className="w-4 h-4" /></button>
              <button className="icon-btn text-gray-400 disabled:opacity-40" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}><ChevronRight className="w-4 h-4" /></button>
            </div>
          </div>
        )}
      </Card>
      <p className="text-[11px] text-gray-400 mt-3">Each event stores <span className="font-mono">HMAC-SHA256(secret, prev_hash + event)</span>; altering or deleting any row breaks every later hash, which <span className="font-medium">Verify integrity</span> detects. Append-only, server-side, cross-service.</p>
    </div>
  );
}
