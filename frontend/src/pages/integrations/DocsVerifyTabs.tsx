import { useState } from "react";
import PrintPdfButton from "../../components/PrintPdfButton";   // print wrap-up 2026-10-24
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Eye, Plus, Send, XCircle } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Dialog, LinkBox } from "../UsersPage";
import { openBlob } from "../../api/identity";
import { BgvRow, Envelope, ProviderInfo, bgvApi, errText, esignApi, when } from "../../api/integrations";
import { LABEL, ProviderPanel, Section, Status } from "./shared";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const MAX_PDF = 10 * 1024 * 1024;

export function EsignTab({ providers, onChanged }: { providers: ProviderInfo[]; onChanged: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["integrations", "esign"], queryFn: esignApi.list });
  const { data: bgvMeta } = useQuery({ queryKey: ["integrations", "bgv", "meta"], queryFn: bgvApi.meta });
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<Envelope | null>(null);
  const [link, setLink] = useState<{ email: string; link: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["integrations", "esign"] });
  const resend = useMutation({ mutationFn: (id: string) => esignApi.resend(id),
    onSuccess: (r, id) => { refresh(); if (r.email_sent) toast.success("Link e-mailed"); else setLink({ email: data?.items.find((e) => e.id === id)?.signer_email || "", link: r.link || "" }); },
    onError: (e: any) => toast.error(errText(e, "Could not send")) });
  const cancel = useMutation({ mutationFn: (id: string) => esignApi.cancel(id, prompt("Reason (kept in the audit trail)") || ""),
    onSuccess: () => { toast.success("Cancelled"); refresh(); }, onError: (e: any) => toast.error(errText(e, "Could not cancel")) });
  const open = async (e: Envelope, signed: boolean) => { try { openBlob(await esignApi.pdf(e.id, signed)); } catch { toast.error("Could not open the PDF"); } };
  const cols: GridColumn<Envelope>[] = [
    { key: "title", label: "Document", render: (e) => <span className="font-medium text-gray-800">{e.title}</span> },
    { key: "kind", label: "Type" }, { key: "signer_name", label: "Signer" },
    { key: "signer_email", label: "Contact", text: (e) => e.signer_email || e.signer_phone || "", render: (e) => e.signer_email || e.signer_phone || "—" },
    { key: "status", label: "Status", render: (e) => <Status s={e.status} /> },
    { key: "otp_status", label: "Code", render: (e) => e.otp_status ? <Status s={e.otp_status === "sent" ? "sent" : "not_configured"} /> : "—" },
    { key: "signed_at", label: "Signed", text: (e) => e.signed_at || "", render: (e) => when(e.signed_at) },
    { key: "created_at", label: "Sent", text: (e) => e.created_at || "", render: (e) => when(e.created_at) },
    { key: "a", label: "", sortable: false, text: () => "", render: (e) => (
      <div className="flex gap-1 justify-end whitespace-nowrap">
        <button className="text-xs text-gray-600 px-1.5 py-0.5 border border-surface-border rounded inline-flex items-center gap-1" onClick={async () => setView(await esignApi.get(e.id))}><Eye className="w-3 h-3" /> Trail</button>
        <button className="text-xs text-gray-600 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => open(e, e.status === "signed")}>{e.status === "signed" ? "Signed PDF" : "PDF"}</button>
        <PrintPdfButton className="text-xs text-gray-600 px-1.5 py-0.5 border border-surface-border rounded inline-flex items-center gap-1" name="document.pdf" fetchPdf={() => esignApi.pdf(e.id, e.status === "signed")} />
        {e.status === "sent" && <button className="text-xs text-gray-600 px-1.5 py-0.5 border border-surface-border rounded inline-flex items-center gap-1" onClick={() => resend.mutate(e.id)}><Send className="w-3 h-3" /> Resend</button>}
        {(e.status === "sent" || e.status === "draft") && <button className="text-xs text-red-600 px-1.5 py-0.5 border border-surface-border rounded inline-flex items-center gap-1" onClick={() => cancel.mutate(e.id)}><XCircle className="w-3 h-3" /></button>}
      </div>) },
  ];
  return (
    <div>
      <div className="text-xs px-3 py-2 rounded-lg bg-amber-50 text-amber-800 border border-amber-200 mb-4">{data?.legal_note}</div>
      <Section title="Documents for signature" sub="Letters, onboarding documents and policies signed with a one-time code."
        right={<button className="btn-primary text-xs flex items-center gap-1" onClick={() => setCreating(true)}><Plus className="w-3.5 h-3.5" /> Send for signature</button>}>
        <DataGrid<Envelope> columns={cols} rows={data?.items || []} rowKey={(e) => e.id} exportName="esign" empty="Nothing sent for signature yet." minWidth={1100} initialSort={{ key: "created_at", dir: "desc" }} />
      </Section>
      <Section title="E-sign providers">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {providers.filter((p) => p.key.startsWith("esign_")).map((p) => <ProviderPanel key={p.key} p={p} onChanged={onChanged} />)}
        </div>
      </Section>
      {creating && <SendDialog employees={bgvMeta?.employees || []} onClose={() => setCreating(false)}
        onDone={(r) => { setCreating(false); refresh(); if (r.email_sent) toast.success(`Sent to ${r.signer_email}`); else if (r.link || r.status === "sent") setLink({ email: r.signer_email || r.signer_name, link: r.link || "" }); else toast.success("Saved"); }} />}
      {link && <LinkBox title={link.link ? "Signing link" : "Signing link not sent"} res={{ email: link.email, email_sent: false, link: link.link }} onClose={() => setLink(null)} />}
      {view && <Dialog title={`Audit trail — ${view.title}`} sub={`${view.signer_name} · ${view.status}`} onClose={() => setView(null)}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs mb-3">
          <div><span className={LABEL}>Original SHA-256</span><code className="break-all">{view.pdf_sha256}</code></div>
          <div><span className={LABEL}>Signed SHA-256</span><code className="break-all">{view.signed_sha256 || "—"}</code></div>
          <div><span className={LABEL}>Typed name / IP</span>{view.typed_name || "—"} · {view.signer_ip || "—"}</div>
          <div><span className={LABEL}>Browser</span>{view.signer_user_agent || "—"}</div>
        </div>
        <table className="w-full text-xs"><tbody>{(view.events || []).map((ev, i) => (
          <tr key={i} className="border-t border-surface-border"><td className="py-1 pr-3 whitespace-nowrap text-gray-500">{ev.at}</td><td className="py-1">{ev.what}</td><td className="py-1 pl-3 text-gray-400">{ev.ip}</td></tr>))}</tbody></table>
        <p className="text-[11px] text-gray-400 mt-3">{view.legal_note}</p>
      </Dialog>}
    </div>
  );
}

function SendDialog({ employees, onClose, onDone }: { employees: { id: string; name: string; employee_number: string | null }[]; onClose: () => void; onDone: (r: any) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("letter");
  const [emp, setEmp] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const m = useMutation({ mutationFn: () => {
      const fd = new FormData(); fd.append("file", file!); fd.append("title", title); fd.append("kind", kind); fd.append("employee_id", emp);
      fd.append("signer_name", name); fd.append("signer_email", email); fd.append("signer_phone", phone); fd.append("send", "true");
      return esignApi.create(fd); },
    onSuccess: onDone, onError: (e: any) => toast.error(errText(e, "Could not send")) });
  const errs = collect({
    file: !file ? "Pick the PDF" : file.type && file.type !== "application/pdf" ? "Only PDF files can be signed" : file.size > MAX_PDF ? "The PDF can be up to 10 MB" : null,
    title: v.required(title.trim(), "Title") || v.maxLen(title, 120, "Title"),
    name: !emp && !name.trim() ? "Give the outside signer's name" : v.maxLen(name, 120, "Signer name"),
    email: !emp && !email.trim() ? "Give the outside signer's e-mail — the one-time code goes there" : v.email(email),
    phone: v.mobile(phone),
  });
  const send = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } m.mutate(); };
  return (
    <Dialog title="Send a document for signature" sub="PDF only. The signer gets a link and confirms with a one-time code sent to their e-mail (or WhatsApp when configured)." onClose={onClose}
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={!file || !title.trim() || (!emp && !name.trim()) || m.isPending} onClick={send}>Send</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="md:col-span-2"><label className={LABEL}>PDF<Req /></label><input className="input" type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} /><span className="block text-[11px] text-gray-400 mt-0.5">PDF only, up to 10 MB</span>{file && <FieldError msg={errs.file} />}</div>
        <div><label className={LABEL}>Title<Req /></label><input className="input" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Appointment letter" /></div>
        <div><label className={LABEL}>Type</label><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
          {["letter", "onboarding", "policy", "other"].map((k) => <option key={k} value={k}>{k}</option>)}</select></div>
        <div className="md:col-span-2"><label className={LABEL}>Employee (or leave empty for an outside signer)</label>
          <select className="input" value={emp} onChange={(e) => setEmp(e.target.value)}><option value="">— Not an employee —</option>
            {employees.map((e) => <option key={e.id} value={e.id}>{e.employee_number ? `${e.employee_number} · ` : ""}{e.name}</option>)}</select></div>
        <div><label className={LABEL}>Signer name {emp ? "(from the record if empty)" : <Req />}</label><input className="input" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /><FieldError msg={name || email ? errs.name : null} /></div>
        <div><label className={LABEL}>Signer e-mail {emp ? "(from the record if empty)" : <Req />}</label><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /><FieldError msg={email ? errs.email : null} /></div>
        <div><label className={LABEL}>Signer phone (for WhatsApp code, optional)</label><input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 416 555 0123" /><span className="block text-[11px] text-gray-400 mt-0.5">Mobile number with country code</span><FieldError msg={errs.phone} /></div>
      </div>
    </Dialog>
  );
}

export function VerifyTab({ providers, onChanged }: { providers: ProviderInfo[]; onChanged: () => void }) {
  const qc = useQueryClient();
  const { data: bgv } = useQuery({ queryKey: ["integrations", "bgv"], queryFn: bgvApi.list });
  const { data: meta } = useQuery({ queryKey: ["integrations", "bgv", "meta"], queryFn: bgvApi.meta });
  const [bgvOpen, setBgvOpen] = useState<BgvRow | "new" | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["integrations"] });
  const bcols: GridColumn<BgvRow>[] = [
    { key: "employee_name", label: "Employee", render: (r) => <span className="font-medium text-gray-800">{r.employee_name}</span> },
    { key: "check_type_label", label: "Check" }, { key: "vendor", label: "Vendor" }, { key: "reference", label: "Reference" },
    { key: "status", label: "Status", text: (r) => r.status_label, render: (r) => <Status s={r.status} /> }, { key: "mode", label: "Mode" },
    { key: "requested_at", label: "Requested", text: (r) => r.requested_at || "", render: (r) => when(r.requested_at) },
    { key: "completed_at", label: "Completed", text: (r) => r.completed_at || "", render: (r) => when(r.completed_at) },
    { key: "result_note", label: "Result" }, { key: "recorded_by", label: "By" },
    { key: "a", label: "", sortable: false, text: () => "", render: (r) => <button className="text-xs text-gray-600 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => setBgvOpen(r)}>Update</button> },
  ];
  return (
    <div>
      <Section title="Background verification (BGV)" sub="Manual mode is built: HR records each check and its result. Vendor-API mode is an interface only."
        right={<button className="btn-primary text-xs flex items-center gap-1" onClick={() => setBgvOpen("new")}><Plus className="w-3.5 h-3.5" /> Record a check</button>}>
        <DataGrid<BgvRow> columns={bcols} rows={bgv?.items || []} rowKey={(r) => r.id} exportName="bgv" empty="No checks recorded yet." minWidth={1200} />
      </Section>
      <Section title="Verification providers">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {providers.filter((p) => p.key === "bgv").map((p) => <ProviderPanel key={p.key} p={p} onChanged={onChanged} />)}
        </div>
      </Section>
      {bgvOpen && meta && <BgvDialog meta={meta} row={bgvOpen === "new" ? null : bgvOpen} onClose={() => setBgvOpen(null)} onDone={() => { setBgvOpen(null); refresh(); toast.success("Saved"); }} />}
    </div>
  );
}

function BgvDialog({ meta, row, onClose, onDone }: { meta: { employees: { id: string; name: string; employee_number: string | null }[]; check_types: { key: string; label: string }[]; statuses: { key: string; label: string }[] }; row: BgvRow | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ employee_id: row?.employee_id || "", check_type: row?.check_type || "identity", vendor: row?.vendor || "",
    reference: row?.reference || "", result_note: row?.result_note || "", status: row?.status || "requested", document_id: row?.document_id || "" });
  const set = (k: string, v: string) => setF({ ...f, [k]: v });
  // creating takes no document id, so a pasted id is linked with a follow-up update (it used to be dropped silently)
  const m = useMutation({ mutationFn: async () => { if (row) return bgvApi.update(row.id, { status: f.status, vendor: f.vendor.trim(), reference: f.reference.trim(), result_note: f.result_note, document_id: f.document_id.trim() });
      const c: any = await bgvApi.create({ employee_id: f.employee_id, check_type: f.check_type, vendor: f.vendor.trim(), reference: f.reference.trim(), result_note: f.result_note, status: f.status });
      if (f.document_id.trim() && c?.id) await bgvApi.update(c.id, { document_id: f.document_id.trim() } as any);
      return c; },
    onSuccess: onDone, onError: (e: any) => toast.error(errText(e, "Could not save")) });
  return (
    <Dialog title={row ? `Update check — ${row.employee_name}` : "Record a background check"} sub="What the vendor checked, its reference and the result. Upload the report under the employee's Documents and paste its id here if you want it linked." onClose={onClose}
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button className="btn-primary text-xs disabled:opacity-60" disabled={(!row && !f.employee_id) || m.isPending} onClick={() => { const errs = collect({ employee_id: !row ? v.required(f.employee_id, "Employee") : null, vendor: v.maxLen(f.vendor, 120, "Vendor"), reference: v.maxLen(f.reference, 120, "Vendor reference"), document_id: f.document_id.trim() && !/^[A-Za-z0-9-]{8,36}$/.test(f.document_id.trim()) ? "That does not look like a document id — copy it from the employee's Documents" : null }); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } m.mutate(); }}>Save</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {!row && <div><label className={LABEL}>Employee</label><select className="input" value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)}><option value="">—</option>
          {meta.employees.map((e) => <option key={e.id} value={e.id}>{e.employee_number ? `${e.employee_number} · ` : ""}{e.name}</option>)}</select></div>}
        {!row && <div><label className={LABEL}>Check type</label><select className="input" value={f.check_type} onChange={(e) => set("check_type", e.target.value)}>{meta.check_types.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></div>}
        <div><label className={LABEL}>Status</label><select className="input" value={f.status} onChange={(e) => set("status", e.target.value)}>{meta.statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></div>
        <div><label className={LABEL}>Vendor</label><input className="input" maxLength={120} value={f.vendor} onChange={(e) => set("vendor", e.target.value)} placeholder="e.g. AuthBridge" /></div>
        <div><label className={LABEL}>Vendor reference</label><input className="input" maxLength={120} value={f.reference} onChange={(e) => set("reference", e.target.value)} /></div>
        <div><label className={LABEL}>Report document id (optional)</label><input className="input font-mono text-xs" maxLength={36} value={f.document_id} onChange={(e) => set("document_id", e.target.value)} /><span className="block text-[11px] text-gray-400 mt-0.5">Upload the report under the employee's Documents first, then paste its id</span></div>
        <div className="md:col-span-3"><label className={LABEL}>Result / notes</label><textarea className="input h-20" value={f.result_note} onChange={(e) => set("result_note", e.target.value)} /></div>
      </div>
    </Dialog>
  );
}
