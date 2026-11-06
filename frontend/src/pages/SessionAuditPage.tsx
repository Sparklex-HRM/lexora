import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { KeyRound, Download, ShieldCheck } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { securityApi, saveBlob } from "../api/privacy";

const th = "px-3 py-2 text-left text-[11px] uppercase tracking-wide text-gray-400 font-semibold";
const td = "px-3 py-1.5 text-sm text-gray-700 border-t border-surface-border/70";
const inputCls = "w-full border border-surface-border rounded-lg px-3 py-1.5 text-sm bg-white";

export default function SessionAuditPage() {
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"" | "ok" | "fail">("");
  const { data, isLoading } = useQuery({ queryKey: ["sessions", q, only], queryFn: () => securityApi.sessions({ q: q || undefined, success: only === "" ? undefined : only === "ok", limit: 300 }) });
  const { data: policy } = useQuery({ queryKey: ["password-policy"], queryFn: securityApi.passwordPolicy });
  const items: any[] = data?.items || [];
  const fails = items.filter((i) => !i.success).length;
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const change = useMutation({ mutationFn: () => securityApi.changePassword(pw.current_password, pw.new_password),
    onSuccess: () => { toast.success("Password changed"); setPw({ current_password: "", new_password: "" }); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not change password") });
  const csv = () => {
    const head = ["at", "email", "role", "ip", "success", "reason", "user_agent"];
    saveBlob(new Blob([[head.join(","), ...items.map((r) => head.map((h) => JSON.stringify(r[h] ?? "")).join(","))].join("\n")], { type: "text/csv" }), "sign-in-audit.csv");
  };
  return (
    <div>
      <PageHeader title="Session audit" icon={KeyRound} subtitle="Every sign-in attempt — who, when, from where, and whether it worked" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Attempts shown" value={data?.total ?? 0} icon={ShieldCheck} accent />
        <StatTile label="Failed" value={fails} sub="wrong password / org code / locked" />
        <StatTile label="Locked (rate-limited)" value={items.filter((i) => i.reason === "locked").length} />
        <StatTile label="Distinct IPs" value={new Set(items.map((i) => i.ip)).size} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div className="lg:col-span-3 card !p-0">
          <div className="px-4 py-2.5 border-b border-surface-border flex flex-wrap items-center gap-2">
            <span className="font-display font-semibold text-gray-800">Sign-ins</span>
            <input className="border border-surface-border rounded-lg px-2 py-1 text-xs ml-auto" placeholder="Search email / IP / reason" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="border border-surface-border rounded-lg px-2 py-1 text-xs" value={only} onChange={(e) => setOnly(e.target.value as any)}><option value="">all</option><option value="ok">successful</option><option value="fail">failed</option></select>
            <button onClick={csv} className="text-xs inline-flex items-center gap-1 border border-surface-border rounded-lg px-2 py-1 bg-white hover:bg-gray-50"><Download className="w-3 h-3" /> CSV</button>
          </div>
          {isLoading ? <div className="grid place-items-center py-10"><Spinner /></div> : (
            <div className="overflow-x-auto max-h-[70vh]"><table className="w-full">
              <thead className="sticky top-0 bg-white"><tr><th className={th}>When</th><th className={th}>Email</th><th className={th}>Role</th><th className={th}>IP</th><th className={th}>Result</th><th className={th}>Browser</th></tr></thead>
              <tbody>{items.map((r) => <tr key={r.id} className={r.success ? "" : "bg-rose-50/40"}>
                <td className={`${td} text-xs whitespace-nowrap`}>{(r.at || "").slice(0, 19).replace("T", " ")}</td>
                <td className={`${td} font-medium`}>{r.email}</td><td className={`${td} text-xs`}>{r.role || "—"}</td>
                <td className={`${td} font-mono text-xs`}>{r.ip}</td>
                <td className={td}><span className={`badge ${r.success ? "badge-green" : "badge-red"}`}>{r.success ? "ok" : (r.reason || "failed").replace("_", " ")}</span></td>
                <td className={`${td} text-[11px] text-gray-400 max-w-xs truncate`}>{r.user_agent}</td></tr>)}
                {!items.length && <tr><td colSpan={6}><Empty>No sign-ins recorded yet.</Empty></td></tr>}</tbody>
            </table></div>)}
        </div>
        <div className="card">
          <div className="font-display font-semibold text-gray-800 mb-1">Change my password</div>
          <p className="text-[11px] text-gray-400 mb-2">{policy?.text}</p>
          <input type="password" className={`${inputCls} mb-1.5`} placeholder="Current password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
          <input type="password" className={`${inputCls} mb-2`} placeholder="New password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
          <button onClick={() => change.mutate()} disabled={!pw.current_password || !pw.new_password || change.isPending} className="w-full rounded-lg bg-primary-600 text-white text-sm font-semibold py-2 disabled:opacity-50">Change password</button>
        </div>
      </div>
    </div>
  );
}
