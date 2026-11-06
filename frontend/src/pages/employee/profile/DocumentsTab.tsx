import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Eye, Upload, Loader2, CheckCircle2 } from "lucide-react";
import { employeeFormApi, openBlob, errText, type StoredDoc } from "../../../api/identity";
import type { TabProps } from "../EmployeeProfilePage";
import { Denied, EmptyLine, LABEL, Loading, Section, TD, TH, fmtDate, useTabQuery } from "./_bits";

/** The documents on file (same store the edit form uses). HR can upload and mark as
 *  checked here; removal stays on Edit → Documents. */
export default function DocumentsTab({ profile, refresh }: TabProps) {
  const e = profile.employee;
  const qc = useQueryClient();
  const canWrite = profile.permissions.can_hr_write;
  const key = ["employees", e.id, "documents"];
  const docs = useTabQuery<StoredDoc[]>(key, () => employeeFormApi.docs(e.id));
  const { data: opts } = useQuery({ queryKey: ["employees", "form-options"], queryFn: employeeFormApi.options, enabled: canWrite });
  const types = opts?.doc_types?.CA || [{ value: "other", label: "Other" }];
  const [docType, setDocType] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const label = (v: string) => Object.values(opts?.doc_types || {}).flat().find((d) => d.value === v)?.label || v.replace(/_/g, " ");
  const reload = () => { qc.invalidateQueries({ queryKey: key }); refresh(); };

  const pick = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    try { await employeeFormApi.uploadDoc(e.id, file, docType || types[0].value); toast.success("Uploaded"); reload(); }
    catch (err) { toast.error(errText(err, "Could not upload the file")); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  const open = async (d: StoredDoc) => {
    try { openBlob(await employeeFormApi.downloadDoc(e.id, d.id)); } catch (err) { toast.error(errText(err, "Could not open the file")); }
  };
  const verify = async (d: StoredDoc) => {
    try { await employeeFormApi.verifyDoc(e.id, d.id, !d.verified); reload(); } catch (err) { toast.error(errText(err, "Could not update the document")); }
  };

  return (
    <Section title={`Documents${docs.data ? ` (${docs.data.length})` : ""}`} flush
      action={canWrite && (
        <div className="flex items-center gap-2">
          <select className="input !py-1 !w-auto text-xs" value={docType || types[0].value} onChange={(ev) => setDocType(ev.target.value)}>
            {types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
          <input ref={input} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={(ev) => pick(ev.target.files?.[0])} />
          <button className="btn-primary !py-1 text-xs flex items-center gap-1.5 disabled:opacity-60" disabled={busy} onClick={() => input.current?.click()}>
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />} Upload</button>
        </div>
      )}>
      {docs.isLoading ? <Loading /> : docs.denied ? <Denied what="documents" />
        : !(docs.data || []).length ? <EmptyLine>No documents on file yet.{canWrite && " Use Upload (top right) to add an ID proof, offer letter or certificate."}</EmptyLine>
        : (
          <table className="w-full text-sm min-w-[680px]">
            <thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide">
              <tr><th className={TH}>Type</th><th className={TH}>File</th><th className={`${TH} text-right`}>Size</th><th className={TH}>Uploaded by</th><th className={TH}>On</th><th className={TH}>Checked</th><th className={`${TH} text-right`}>Actions</th></tr>
            </thead>
            <tbody>
              {(docs.data || []).map((d) => (
                <tr key={d.id} className="border-t border-surface-border">
                  <td className={`${TD} capitalize`}>{label(d.doc_type)}</td>
                  <td className={`${TD} !whitespace-normal`}>{d.file_name}</td>
                  <td className={`${TD} text-right tabular-nums`}>{Math.max(1, Math.round(d.size_bytes / 1024))} KB</td>
                  <td className={TD}>{d.uploaded_by_name || "—"}</td>
                  <td className={TD}>{fmtDate(d.uploaded_at)}</td>
                  <td className={TD}>{d.verified ? <span className="badge-green">Checked</span> : <span className="badge-amber">Not yet</span>}</td>
                  <td className={TD}><div className="flex justify-end gap-1">
                    <button className="icon-btn text-gray-400 hover:text-primary-600" title="Open" onClick={() => open(d)}><Eye className="w-4 h-4" /></button>
                    {canWrite && <button className="icon-btn text-gray-400 hover:text-green-600" title={d.verified ? "Mark as not checked" : "Mark as checked"} onClick={() => verify(d)}><CheckCircle2 className="w-4 h-4" /></button>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      {canWrite && <p className={`${LABEL} !normal-case !font-normal px-4 py-2 border-t border-surface-border`}>{opts?.doc_rules?.allowed || "JPG, PNG or PDF"} · up to {opts?.doc_rules?.max_mb || 5} MB · to remove a file open Edit → Documents.</p>}
    </Section>
  );
}
