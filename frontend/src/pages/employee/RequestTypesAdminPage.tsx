import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowDown, ArrowUp, ListChecks, Plus, Trash2 } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { PageHeader } from "../../components/ui";
import { Modal, inputCls, err } from "../canada/_shared";
import { essRequestsApi, FIELD_TYPES, FieldType, RequestType } from "../../api/essRequests";
import { FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

/** Mirrors the server rule for request-type and field keys (custom_fields_logic._KEY_RE). */
const KEY_RULE = /^[a-z][a-z0-9_]{0,39}$/;
const MAX_FIELDS = 30;
/** min / max apply to number and date answers only (server: custom_fields_logic.validate_definition). */
const RANGED = new Set(["number", "date"]);

interface DraftField { label: string; key: string; type: FieldType; required: boolean; options: string; help: string; min: string; max: string; regex: string }
interface Draft {
  id?: string; key: string; name: string; category: string; description: string; handler_note: string;
  active: boolean; sort_order: number; fields: DraftField[];
}
const blankField = (): DraftField => ({ label: "", key: "", type: "text", required: false, options: "", help: "", min: "", max: "", regex: "" });
const fromType = (t?: RequestType | null): Draft => t ? {
  id: t.id, key: t.key, name: t.name, category: t.category || "", description: t.description || "",
  handler_note: t.handler_note || "", active: t.active, sort_order: t.sort_order,
  fields: (t.fields || []).map((f) => ({ label: f.label, key: f.key, type: f.type, required: !!f.required,
    options: (f.options || []).join(", "), help: f.validation?.help || "",
    min: f.validation?.min != null ? String(f.validation.min) : "", max: f.validation?.max != null ? String(f.validation.max) : "",
    regex: f.validation?.regex || "" })),
} : { key: "", name: "", category: "", description: "", handler_note: "", active: true, sort_order: 100, fields: [blankField()] };
const CHOICE = new Set(["select", "multiselect"]);

function TypeModal({ initial, onClose, takenKeys = [] }: { initial: RequestType | null; onClose: () => void; takenKeys?: string[] }) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(fromType(initial));
  const [tried, setTried] = useState(false);
  const kept = d.fields.filter((f) => f.label.trim());
  const fieldErr = (f: DraftField, i: number): string | null => {
    if (!f.label.trim()) return null;
    const k = f.key.trim();
    if (k && !KEY_RULE.test(k)) return `Field ${i + 1}: key must start with a letter and use only a–z, 0–9 and _ (max 40)`;
    if (k && d.fields.some((o, j) => j !== i && o.label.trim() && o.key.trim() === k)) return `Field ${i + 1}: two fields share the key '${k}'`;
    if (CHOICE.has(f.type) && !f.options.split(",").map((x) => x.trim()).filter(Boolean).length) return `Field ${i + 1} (${f.label.trim()}): list the choices, separated by commas`;
    if (RANGED.has(f.type) && f.min && f.max && (f.type === "date" ? f.max < f.min : Number(f.max) < Number(f.min))) return `Field ${i + 1} (${f.label.trim()}): min cannot be more than max`;
    if (f.type === "number" && ((f.min && !Number.isFinite(Number(f.min))) || (f.max && !Number.isFinite(Number(f.max))))) return `Field ${i + 1} (${f.label.trim()}): min / max must be numbers`;
    return v.maxLen(f.label, 120, `Field ${i + 1} label`) || v.maxLen(f.help, 300, `Field ${i + 1} help text`);
  };
  const keyNow = d.id ? "" : d.key.trim();
  const errs = collect({
    name: v.required(d.name, "Name") || v.maxLen(d.name.trim(), 120, "Name"),
    key: keyNow && !KEY_RULE.test(keyNow) ? "Key must start with a letter and use only a–z, 0–9 and _ (max 40)"
      : keyNow && takenKeys.includes(keyNow) ? `Another request type already uses the key '${keyNow}'` : null,
    category: v.maxLen(d.category, 60, "Category"),
    description: v.maxLen(d.description, 600, "Description"),
    handler_note: v.maxLen(d.handler_note, 300, "Who handles it"),
    sort_order: !Number.isInteger(d.sort_order) || d.sort_order < 0 ? "Order must be a whole number, 0 or more" : null,
    fields: kept.length > MAX_FIELDS ? `A request form can have at most ${MAX_FIELDS} fields` : d.fields.map(fieldErr).find(Boolean) || null,
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const dropped = d.fields.length - kept.length;
  const submit = () => { setTried(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  const Lb = ({ t, req }: { t: string; req?: boolean }) => <span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">{t}{req && <Req />}</span>;
  const Hint = ({ t }: { t: string }) => <span className="block text-[11px] text-gray-500 mt-0.5">{t}</span>;
  const set = (patch: Partial<Draft>) => setD((s) => ({ ...s, ...patch }));
  const setF = (i: number, patch: Partial<DraftField>) => setD((s) => ({ ...s, fields: s.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)) }));
  const move = (i: number, by: number) => setD((s) => {
    const f = [...s.fields]; const j = i + by;
    if (j < 0 || j >= f.length) return s;
    [f[i], f[j]] = [f[j], f[i]];
    return { ...s, fields: f };
  });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: d.name.trim(), category: d.category.trim(), description: d.description.trim(), handler_note: d.handler_note.trim(),
        active: d.active, sort_order: d.sort_order, ...(d.id ? {} : d.key ? { key: d.key } : {}),
        fields: d.fields.filter((f) => f.label.trim()).map((f, i) => ({
          label: f.label.trim(), ...(f.key.trim() ? { key: f.key.trim() } : {}), type: f.type, required: f.required,
          options: CHOICE.has(f.type) ? f.options.split(",").map((x) => x.trim()).filter(Boolean) : null,
          validation: (() => {
            const val: Record<string, string> = {};
            if (f.help.trim()) val.help = f.help.trim();
            if (f.regex.trim()) val.regex = f.regex.trim();          // kept from the saved form (not edited here)
            if (RANGED.has(f.type) && f.min.trim()) val.min = f.min.trim();
            if (RANGED.has(f.type) && f.max.trim()) val.max = f.max.trim();
            return Object.keys(val).length ? val : null;
          })(), sort_order: (i + 1) * 10,
        })),
      };
      return d.id ? essRequestsApi.updateType(d.id, body) : essRequestsApi.createType(body);
    },
    onSuccess: () => { toast.success("Request type saved"); qc.invalidateQueries({ queryKey: ["ess-request-types"] }); onClose(); },
    onError: (e) => toast.error(err(e, "Could not save")),
  });
  return (
    <Modal title={d.id ? `Edit — ${initial?.name}` : "New request type"} sub="The form uses the same field types as custom fields." onClose={onClose} width="max-w-5xl"
      footer={<>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={save.isPending} onClick={submit}>Save</button></>}>
      <FormSection title="Request type">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <label className="block"><Lb t="Name" req /><input className={inputCls} value={d.name} maxLength={120} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Address proof letter" /><FieldError msg={show("name")} /></label>
        <label className="block"><Lb t="Key" />
          <input className={inputCls} value={d.key} disabled={!!d.id} maxLength={40} placeholder="made from the name"
            onChange={(e) => set({ key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} />
          <Hint t={d.id ? "Fixed once created — exports and approval rules use it." : "Permanent ID for exports and approval rules. Made from the name if left blank; cannot be changed later."} /><FieldError msg={show("key")} />
        </label>
        <label className="block"><Lb t="Category" /><input className={inputCls} value={d.category} maxLength={60} placeholder="Letters, Records, Work…" onChange={(e) => set({ category: e.target.value })} />
          <Hint t="Groups this type on the employee's request page." /><FieldError msg={show("category")} /></label>
        <label className="block sm:col-span-2"><Lb t="Description" /><input className={inputCls} value={d.description} maxLength={600} onChange={(e) => set({ description: e.target.value })} placeholder="What the employee gets and when to use it" /><FieldError msg={show("description")} /></label>
        <label className="block"><Lb t="Who handles it" /><input className={inputCls} value={d.handler_note} maxLength={300} placeholder="e.g. HR operations, 2 working days" onChange={(e) => set({ handler_note: e.target.value })} />
          <Hint t="Shown to employees — who acts on it and how long it takes." /><FieldError msg={show("handler_note")} /></label>
        <label className="block"><Lb t="Order" /><input type="number" min={0} step={1} className={inputCls} value={d.sort_order} onChange={(e) => set({ sort_order: e.target.value === "" ? 0 : Number(e.target.value) })} />
          <Hint t="Lower numbers are listed first." /><FieldError msg={show("sort_order")} /></label>
        <label className="text-xs flex items-center gap-2 sm:mt-5"><input type="checkbox" checked={d.active} onChange={(e) => set({ active: e.target.checked })} /> Employees can raise it</label>
      </div>
      <p className="text-[11px] text-gray-500 mt-2">Who approves a request is set in the approval workflows (request kind "employee request"), not here.</p>
      </FormSection>

      <div className="flex items-center justify-between mb-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-primary-700">Form fields <span className="text-gray-400 normal-case font-normal">({kept.length} of max {MAX_FIELDS})</span></h4>
        <button className="btn-secondary !py-1 text-xs inline-flex items-center gap-1" onClick={() => set({ fields: [...d.fields, blankField()] })}><Plus className="w-3.5 h-3.5" />Add field</button>
      </div>
      <FieldError msg={show("fields")} />
      {dropped > 0 && <p className="text-[11px] text-amber-700 mb-1">{dropped} row(s) without a label will not be saved.</p>}
      <div className="overflow-x-auto border border-surface-border rounded-lg">
        <table className="w-full text-xs min-w-[1040px]">
          <thead className="bg-surface text-gray-500 text-[11px] uppercase">
            <tr><th className="px-2 py-1.5 text-left">Label</th><th className="px-2 text-left">Key</th><th className="px-2 text-left">Type</th>
              <th className="px-2">Required</th><th className="px-2 text-left">Options (comma)</th><th className="px-2 text-left">Min</th><th className="px-2 text-left">Max</th><th className="px-2 text-left">Help text</th><th className="px-2" /></tr>
          </thead>
          <tbody className="divide-y divide-surface-border">{d.fields.map((f, i) => (
            <tr key={i}>
              <td className="px-2 py-1"><input className={inputCls} value={f.label} onChange={(e) => setF(i, { label: e.target.value })} /></td>
              <td className="px-2"><input className={inputCls} value={f.key} placeholder="auto" onChange={(e) => setF(i, { key: e.target.value })} /></td>
              <td className="px-2"><select className={inputCls} value={f.type} onChange={(e) => setF(i, { type: e.target.value as FieldType })}>
                {FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></td>
              <td className="px-2 text-center"><input type="checkbox" checked={f.required} onChange={(e) => setF(i, { required: e.target.checked })} /></td>
              <td className="px-2"><input className={inputCls} value={f.options} disabled={!CHOICE.has(f.type)}
                placeholder={CHOICE.has(f.type) ? "visa, bank, rent" : "—"} onChange={(e) => setF(i, { options: e.target.value })} /></td>
              {(["min", "max"] as const).map((k) => (
                <td key={k} className="px-2 w-28"><input className={inputCls} type={f.type === "date" ? "date" : "number"} value={f[k]} disabled={!RANGED.has(f.type)}
                  placeholder={RANGED.has(f.type) ? "" : "—"} title={RANGED.has(f.type) ? `Smallest / largest ${f.type === "date" ? "date" : "number"} allowed` : "Only for number and date fields"}
                  onChange={(e) => setF(i, { [k]: e.target.value } as Partial<DraftField>)} /></td>))}
              <td className="px-2"><input className={inputCls} value={f.help} maxLength={300} onChange={(e) => setF(i, { help: e.target.value })} /></td>
              <td className="px-2 whitespace-nowrap">
                <button className="p-1 text-gray-400 hover:text-gray-700" title="Up" onClick={() => move(i, -1)}><ArrowUp className="w-3.5 h-3.5" /></button>
                <button className="p-1 text-gray-400 hover:text-gray-700" title="Down" onClick={() => move(i, 1)}><ArrowDown className="w-3.5 h-3.5" /></button>
                <button className="p-1 text-red-500 hover:text-red-700" title="Remove" onClick={() => set({ fields: d.fields.filter((_, j) => j !== i) })}><Trash2 className="w-3.5 h-3.5" /></button>
              </td>
            </tr>))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-gray-400 mt-2">A file field asks the employee to attach a document; it is stored with their documents. Min / max apply to number and date fields. Changing a form never changes requests already raised.</p>
    </Modal>
  );
}

/** HR: the request types employees can raise, each with its own form. */
export default function RequestTypesAdminPage() {
  const { data = [] } = useQuery({ queryKey: ["ess-request-types", "all"], queryFn: () => essRequestsApi.types(true) });
  const [edit, setEdit] = useState<RequestType | null | "new">(null);
  const cols: GridColumn<RequestType>[] = [
    { key: "name", label: "Request type" },
    { key: "key", label: "Key" },
    { key: "category", label: "Category" },
    { key: "fields", label: "Fields", type: "number", text: (t) => String(t.fields.length), render: (t) => t.fields.length },
    { key: "field_list", label: "Form", text: (t) => t.fields.map((f) => f.label).join(", "),
      render: (t) => <span className="truncate block max-w-[22rem]">{t.fields.map((f) => f.label + (f.required ? "*" : "")).join(", ")}</span> },
    { key: "handler_note", label: "Who handles it" },
    { key: "active", label: "Status", text: (t) => (t.active ? "active" : "off"),
      render: (t) => <span className={t.active ? "badge-green" : "badge-purple"}>{t.active ? "active" : "off"}</span> },
    { key: "updated_at", label: "Updated", type: "date" },
  ];
  return (
    <div className="max-w-[1400px] mx-auto">
      <PageHeader title="Request types" icon={ListChecks} subtitle="What employees can ask for, the form they fill and who handles it."
        actions={<button className="btn-primary inline-flex items-center gap-1" onClick={() => setEdit("new")}><Plus className="w-4 h-4" />New type</button>} />
      <DataGrid columns={cols} rows={data} rowKey={(t) => t.id} exportName="request-types" minWidth={980}
        onRowClick={(t) => setEdit(t)} empty="No request types yet — add the first one." />
      {edit && <TypeModal initial={edit === "new" ? null : edit} onClose={() => setEdit(null)} takenKeys={data.map((t) => t.key)} />}
    </div>
  );
}
