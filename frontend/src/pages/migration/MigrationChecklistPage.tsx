import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Undo2, Check } from "lucide-react";
import toast from "react-hot-toast";
import clsx from "clsx";
import { Link } from "react-router-dom";
import { PageHeader, Spinner } from "../../components/ui";
import { migrationKitApi } from "../../api/migrationKit";
import { Th, Td, Panel, Hint, Tile, err } from "../canada/_shared";
import { formatDateTime, localDateOf } from "../../lib/datetime";   // 2026-10-25: instants shown in the viewer's zone

/** /migration/checklist — fixed load order with progress; auto steps come from the data, manual steps are ticked. */
export default function MigrationChecklistPage() {
  const qc = useQueryClient();
  const country = "CA";
  const { data, isLoading } = useQuery({ queryKey: ["mig-checklist", country], queryFn: () => migrationKitApi.checklist(country) });
  const { data: last } = useQuery({ queryKey: ["mig-last-batch"], queryFn: migrationKitApi.lastBatch });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["mig-checklist"] }); qc.invalidateQueries({ queryKey: ["mig-last-batch"] }); };
  const tick = useMutation({ mutationFn: (b: { step_key: string; done: boolean }) => migrationKitApi.tick({ country, ...b }), onSuccess: refresh, onError: (e) => toast.error(err(e)) });
  const undo = useMutation({ mutationFn: (url: string) => migrationKitApi.undoByUrl(url), onSuccess: () => { toast.success("Last import undone"); refresh(); }, onError: (e) => toast.error(err(e)) });
  if (isLoading || !data) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="Migration checklist" icon={ListChecks} subtitle="Load in this order. A step is blocked until the required steps before it are done."
        />
      <div className="flex flex-wrap gap-2 mb-3"><Tile label="Done" value={`${data.done} / ${data.total}`} tone={data.complete ? "ok" : "default"} /><Tile label="Progress" value={`${data.percent}%`} tone="accent" /><Tile label="Next step" value={data.next_step || "—"} /></div>
      <div className="h-2 bg-gray-100 rounded mb-3"><div className="h-2 bg-primary-600 rounded" style={{ width: `${data.percent}%` }} /></div>
      {last?.last && <div className="mb-3"><Hint tone="info">Last import: <b>{last.last.file_type}</b> · {last.last.file_name} · {last.last.rows} rows · {formatDateTime(last.last.committed_at)} ({last.last.country})
        <button type="button" className="ml-3 btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" disabled={undo.isPending} onClick={() => { if (confirm("Undo the last import batch? Every row it wrote goes back to its prior state.")) undo.mutate(last.last.undo_url); }}><Undo2 className="w-3 h-3" /> Undo last import batch</button></Hint></div>}
      <Panel title="Load order · Canada">
        <table className="w-full text-sm"><thead><tr><Th>#</Th><Th>Step</Th><Th>Required</Th><Th>How</Th><Th>Status</Th><Th>Evidence</Th><Th /></tr></thead>
          <tbody>{data.steps.map((s) => (
            <tr key={s.key} className={clsx(s.done ? "bg-emerald-50/40" : s.blocked ? "text-gray-400" : "")}>
              <Td mono>{s.order}</Td><Td className="font-medium">{s.title}</Td><Td>{s.required ? <span className="badge-red">yes</span> : <span className="text-[11px]">optional</span>}</Td>
              <Td className="!whitespace-normal max-w-[26rem] text-gray-600">{s.how}</Td>
              <Td>{s.done ? <span className="badge-green">done</span> : s.blocked ? <span className="badge-purple">blocked</span> : <span className="badge-amber">to do</span>}</Td>
              <Td className="text-[11px] text-gray-500">{s.auto ? (s.auto_value ? `${s.auto_value}` : "none yet") : s.manual ? `${s.done_by || ""} · ${localDateOf(s.done_at)}` : "manual"}</Td>
              <Td><div className="flex items-center gap-2"><Link className="text-[11px] text-primary-700 underline" to={s.link}>open</Link>
                {!s.auto && !s.blocked && <button type="button" className="text-[11px] inline-flex items-center gap-1 text-primary-700" onClick={() => tick.mutate({ step_key: s.key, done: !s.manual })}><Check className="w-3 h-3" /> {s.manual ? "untick" : "tick"}</button>}</div></Td>
            </tr>))}</tbody></table>
      </Panel>
    </div>
  );
}
