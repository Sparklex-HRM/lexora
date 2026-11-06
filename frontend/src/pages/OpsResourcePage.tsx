import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  MapPin, Handshake, FolderKanban, Store, Users2, ReceiptText, Plus, Trash2, LucideIcon,
  FolderOpen, ListTodo, BookOpen, Search,
} from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { Pager } from "../components/Pager";
import { opsApi, OpsKind, money } from "../api/endpoints";

interface Field { k: string; label: string; type?: "text" | "number" | "date" | "select"; opts?: string[]; placeholder?: string; }
interface Col { k: string; label: string; kind?: "money" | "badge" }
interface Cfg { title: string; icon: LucideIcon; subtitle: string; noun: string; fields: Field[]; cols: Col[]; kpis: (rows: any[]) => { label: string; value: any; sub?: string }[]; }

const money0 = (n: number) => "$" + (Number(n) || 0).toLocaleString("en-CA");
const PROV = ["ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE"];

const CONFIG: Record<string, Cfg> = {
  branches: {
    title: "Locations & Branches", icon: MapPin, subtitle: "Your org → branch structure", noun: "Branch",
    fields: [{ k: "name", label: "Name", placeholder: "Toronto HQ" }, { k: "code", label: "Code", placeholder: "TOR" },
      { k: "city", label: "City", placeholder: "Toronto" }, { k: "province", label: "Province", type: "select", opts: PROV },
      { k: "address", label: "Address" }],
    cols: [{ k: "name", label: "Name" }, { k: "code", label: "Code" }, { k: "city", label: "City" }, { k: "province", label: "Province" }],
    kpis: (r) => [{ label: "Branches", value: r.length }, { label: "Provinces", value: new Set(r.map((x) => x.province).filter(Boolean)).size }],
  },
  clients: {
    title: "Client Management", icon: Handshake, subtitle: "Customers you place workers with", noun: "Client",
    fields: [{ k: "name", label: "Client", placeholder: "Acme Corp" }, { k: "code", label: "Code" },
      { k: "contact_name", label: "Contact" }, { k: "email", label: "Email", placeholder: "ap@acme.ca" },
      { k: "phone", label: "Phone" }, { k: "payment_terms", label: "Terms", type: "select", opts: ["Net 15", "Net 30", "Net 45", "Due on receipt"] },
      { k: "city", label: "City" }, { k: "province", label: "Province", type: "select", opts: PROV }],
    cols: [{ k: "name", label: "Client" }, { k: "contact_name", label: "Contact" }, { k: "email", label: "Email" }, { k: "payment_terms", label: "Terms" }],
    kpis: (r) => [{ label: "Clients", value: r.length }],
  },
  projects: {
    title: "Project Management", icon: FolderKanban, subtitle: "Projects, budgets & staffing", noun: "Project",
    fields: [{ k: "name", label: "Project", placeholder: "Warehouse fit-out" }, { k: "code", label: "Code" },
      { k: "client", label: "Client" }, { k: "status", label: "Status", type: "select", opts: ["active", "on_hold", "completed"] },
      { k: "budget", label: "Budget ($)", type: "number" }, { k: "start_date", label: "Start", type: "date" }, { k: "end_date", label: "End", type: "date" }],
    cols: [{ k: "name", label: "Project" }, { k: "client", label: "Client" }, { k: "status", label: "Status", kind: "badge" }, { k: "budget", label: "Budget", kind: "money" }],
    kpis: (r) => [{ label: "Projects", value: r.length }, { label: "Total budget", value: money0(r.reduce((s, x) => s + (x.budget || 0), 0)) }],
  },
  vendors: {
    title: "Vendor Management", icon: Store, subtitle: "Subcontractors & suppliers", noun: "Vendor",
    fields: [{ k: "name", label: "Vendor", placeholder: "SubCo Ltd" }, { k: "code", label: "Code" },
      { k: "contact_name", label: "Contact" }, { k: "email", label: "Email" }, { k: "phone", label: "Phone" },
      { k: "service_type", label: "Service", placeholder: "Staffing / trades" }],
    cols: [{ k: "name", label: "Vendor" }, { k: "service_type", label: "Service" }, { k: "contact_name", label: "Contact" }, { k: "email", label: "Email" }],
    kpis: (r) => [{ label: "Vendors", value: r.length }],
  },
  roster: {
    title: "Roster", icon: Users2, subtitle: "Deploy the workforce to clients & sites", noun: "Assignment",
    fields: [{ k: "name", label: "Employee", placeholder: "Aisha Khan" }, { k: "client", label: "Client" }, { k: "project", label: "Project" },
      { k: "branch", label: "Branch" }, { k: "role", label: "Role", placeholder: "Forklift op" },
      { k: "start_date", label: "Start", type: "date" }, { k: "end_date", label: "End", type: "date" },
      { k: "bill_rate", label: "Bill rate ($/h)", type: "number" }, { k: "pay_rate", label: "Pay rate ($/h)", type: "number" }],
    cols: [{ k: "name", label: "Employee" }, { k: "client", label: "Client" }, { k: "role", label: "Role" },
      { k: "bill_rate", label: "Bill", kind: "money" }, { k: "pay_rate", label: "Pay", kind: "money" }, { k: "margin", label: "Margin/h", kind: "money" }],
    kpis: (r) => [{ label: "Placements", value: r.length },
      { label: "Avg margin/h", value: money(r.length ? r.reduce((s, x) => s + (x.margin || 0), 0) / r.length : 0) }],
  },
  invoices: {
    title: "Invoicing", icon: ReceiptText, subtitle: "Bill clients from approved time", noun: "Invoice",
    fields: [{ k: "name", label: "Invoice #", placeholder: "INV-1001" }, { k: "client", label: "Client" },
      { k: "issue_date", label: "Issued", type: "date" }, { k: "due_date", label: "Due", type: "date" },
      { k: "amount", label: "Amount ($)", type: "number" }, { k: "tax", label: "Tax ($)", type: "number" },
      { k: "status", label: "Status", type: "select", opts: ["draft", "sent", "paid", "overdue"] }],
    cols: [{ k: "name", label: "Invoice #" }, { k: "client", label: "Client" }, { k: "total", label: "Total", kind: "money" },
      { k: "due_date", label: "Due" }, { k: "status", label: "Status", kind: "badge" }],
    kpis: (r) => [{ label: "Invoices", value: r.length },
      { label: "Billed", value: money0(r.reduce((s, x) => s + (x.total || 0), 0)) },
      { label: "Outstanding", value: money0(r.filter((x) => x.status !== "paid").reduce((s, x) => s + (x.total || 0), 0)) }],
  },
  documents: {
    title: "Documents", icon: FolderOpen, subtitle: "Employee document vault", noun: "Document",
    fields: [{ k: "name", label: "Title", placeholder: "Offer letter" },
      { k: "category", label: "Category", type: "select", opts: ["contract", "td1", "void_cheque", "permit", "policy", "other"] },
      { k: "employee", label: "Employee" }, { k: "expiry_date", label: "Expiry", type: "date" },
      { k: "status", label: "Status", type: "select", opts: ["on_file", "pending_signature", "expired"] }],
    cols: [{ k: "name", label: "Title" }, { k: "category", label: "Category" }, { k: "employee", label: "Employee" },
      { k: "expiry_date", label: "Expiry" }, { k: "status", label: "Status", kind: "badge" }],
    kpis: (r) => [{ label: "Documents", value: r.length }, { label: "Expiring", value: r.filter((x) => x.expiry_date).length }],
  },
  tasks: {
    title: "Tasks", icon: ListTodo, subtitle: "Assigned work across HR", noun: "Task",
    fields: [{ k: "name", label: "Task", placeholder: "Collect TD1 from new hire" }, { k: "assignee", label: "Assignee" },
      { k: "due_date", label: "Due", type: "date" }, { k: "priority", label: "Priority", type: "select", opts: ["low", "normal", "high"] },
      { k: "status", label: "Status", type: "select", opts: ["open", "in_progress", "done"] }],
    cols: [{ k: "name", label: "Task" }, { k: "assignee", label: "Assignee" }, { k: "due_date", label: "Due" },
      { k: "priority", label: "Priority", kind: "badge" }, { k: "status", label: "Status", kind: "badge" }],
    kpis: (r) => [{ label: "Tasks", value: r.length }, { label: "Open", value: r.filter((x) => x.status !== "done").length }],
  },
  policies: {
    title: "Policies", icon: BookOpen, subtitle: "Policy library & acknowledgements", noun: "Policy",
    fields: [{ k: "name", label: "Policy", placeholder: "Code of Conduct" }, { k: "version", label: "Version", placeholder: "v1.0" },
      { k: "category", label: "Category" }, { k: "effective_date", label: "Effective", type: "date" },
      { k: "status", label: "Status", type: "select", opts: ["draft", "active", "archived"] }],
    cols: [{ k: "name", label: "Policy" }, { k: "version", label: "Version" }, { k: "category", label: "Category" },
      { k: "effective_date", label: "Effective" }, { k: "status", label: "Status", kind: "badge" }],
    kpis: (r) => [{ label: "Policies", value: r.length }, { label: "Active", value: r.filter((x) => x.status === "active").length }],
  },
};

export default function OpsResourcePage() {
  const { resource = "branches" } = useParams();
  const kind = resource as OpsKind;
  const cfg = CONFIG[resource];
  const qc = useQueryClient();
  const [row, setRow] = useState<Record<string, any>>({});
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  const { data, isLoading } = useQuery({ queryKey: ["ops", kind], queryFn: () => opsApi.list(kind), enabled: !!cfg });
  const add = useMutation({
    mutationFn: (body: Record<string, any>) => opsApi.create(kind, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ops", kind] }); setRow({}); toast.success(`${cfg.noun} added`); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not add"),
  });
  const del = useMutation({
    mutationFn: (id: string) => opsApi.remove(kind, id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ops", kind] }); toast.success("Removed"); },
  });

  const rows = data || [];
  const filtered = useMemo(() => {
    if (!cfg) return rows;
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r: any) => cfg.cols.some((c) => String(r[c.k] ?? "").toLowerCase().includes(needle)));
  }, [rows, q, cfg]);
  useEffect(() => { setPage(1); }, [q, pageSize, kind]);
  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const curPage = Math.min(page, pageCount);
  const paged = filtered.slice((curPage - 1) * pageSize, curPage * pageSize);

  if (!cfg) return <div className="card m-1">Unknown resource.</div>;
  const submit = () => { if (!row.name) return toast.error("Name is required"); add.mutate(row); };
  const cell = (r: any, c: Col) => {
    const v = r[c.k];
    if (c.kind === "money") return <span className="tabular-nums">{money(v || 0)}</span>;
    if (c.kind === "badge") return <span className={`badge-${badgeTone(v)}`}>{String(v || "—")}</span>;
    return v ?? "—";
  };

  return (
    <div>
      <PageHeader title={cfg.title} icon={cfg.icon} subtitle={cfg.subtitle} />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        {cfg.kpis(rows).map((k, i) => <StatTile key={k.label} label={k.label} value={k.value} accent={i === 0} sub={k.sub} />)}
      </div>

      {/* Add row */}
      <div className="card mb-4">
        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Add {cfg.noun.toLowerCase()}</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 items-end">
          {cfg.fields.map((f) => (
            <div key={f.k}>
              <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">{f.label}</label>
              {f.type === "select" ? (
                <select className="input" value={row[f.k] ?? ""} onChange={(e) => setRow((r) => ({ ...r, [f.k]: e.target.value }))}>
                  <option value="">—</option>{f.opts!.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input className="input" type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"} placeholder={f.placeholder}
                  value={row[f.k] ?? ""} onChange={(e) => setRow((r) => ({ ...r, [f.k]: f.type === "number" ? parseFloat(e.target.value) || 0 : e.target.value }))} />
              )}
            </div>
          ))}
          <button className="btn-primary flex items-center justify-center gap-1.5 h-9" onClick={submit} disabled={add.isPending}>
            <Plus className="w-4 h-4" /> Add
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="card !p-0">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-surface-border">
          <div className="relative w-full max-w-xs">
            <Search className="w-4 h-4 text-gray-300 absolute left-3 top-1/2 -translate-y-1/2" />
            <input className="input !pl-9 !py-1.5 text-sm w-full" placeholder={`Search ${cfg.noun.toLowerCase()}s…`}
              value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <span className="text-xs text-gray-400 tabular-nums whitespace-nowrap">{total} {cfg.noun.toLowerCase()}{total !== 1 ? "s" : ""}</span>
        </div>
        {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : rows.length === 0 ? <Empty>No {cfg.title.toLowerCase()} yet — add one above.</Empty>
          : total === 0 ? <Empty>No {cfg.noun.toLowerCase()}s match “{q}”.</Empty>
          : (
            <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>{cfg.cols.map((c) => <th key={c.k} className={`px-4 py-2.5 font-semibold ${c.kind === "money" ? "text-right" : ""}`}>{c.label}</th>)}<th /></tr>
                </thead>
                <tbody>
                  {paged.map((r) => (
                    <tr key={r.id} className="border-t border-surface-border hover:bg-gray-50">
                      {cfg.cols.map((c) => <td key={c.k} className={`px-4 py-2.5 text-gray-700 ${c.kind === "money" ? "text-right" : ""}`}>{cell(r, c)}</td>)}
                      <td className="px-4 py-2.5 text-right"><button onClick={() => del.mutate(r.id)} className="icon-btn text-gray-300 hover:text-red-500"><Trash2 className="w-4 h-4" /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={total} page={curPage} pageSize={pageSize} onPage={setPage} onPageSize={setPageSize} />
            </>
          )}
      </div>
    </div>
  );
}

function badgeTone(status: string): string {
  const s = (status || "").toLowerCase();
  if (["paid", "active", "completed", "done", "on_file"].includes(s)) return "green";
  if (["overdue", "expired", "high"].includes(s)) return "red";
  if (["on_hold", "draft", "sent", "pending_signature", "open"].includes(s)) return "amber";
  return "teal";
}
