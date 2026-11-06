import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { AlertTriangle, CalendarDays, Check, Shuffle, X } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Field, Input, Modal } from "../common/_shared";
import { MyShift, rotaApi, rotaErr, SwapOption } from "../../api/rotations";
import { SwapGrid } from "./ShiftRotationsPage";

/** Employee self-service (G2): my published shifts + swap a shift with a colleague (colleague accepts → manager approves). */
export default function MyShiftsPage() {
  const qc = useQueryClient();
  const shifts = useQuery({ queryKey: ["my-shifts"], queryFn: () => rotaApi.myShifts() });
  const swaps = useQuery({ queryKey: ["rota-swaps", "mine"], queryFn: () => rotaApi.swaps("mine") });
  const [ask, setAsk] = useState<MyShift | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["my-shifts"] }); qc.invalidateQueries({ queryKey: ["rota-swaps"] }); };
  const act = useMutation({ mutationFn: ({ id, a, note }: { id: string; a: "accept" | "decline" | "cancel"; note?: string }) => rotaApi.swapAct(id, a, note),
    onSuccess: (s) => { toast.success(s.status === "pending_manager" ? "Accepted — your manager decides next" : s.status === "declined" ? "Declined" : "Withdrawn"); refresh(); },
    onError: (e) => toast.error(rotaErr(e)) });
  const enabled = shifts.data?.swaps_enabled !== false;
  const cols: GridColumn<MyShift>[] = [
    { key: "work_date", label: "Date", type: "date", width: "120px" },
    { key: "day", label: "Day", text: (s) => new Date(s.work_date + "T00:00:00").toLocaleDateString("en-CA", { weekday: "short" }) },
    { key: "time", label: "Shift", text: (s) => (s.kind === "off" ? (s.shift_code === "HOL" ? "Holiday" : "Day off") : `${s.start_time}–${s.end_time}`),
      render: (s) => s.kind === "off" ? <span className="text-gray-400">{s.shift_code === "HOL" ? "Holiday" : "Day off"}</span> : <b className="text-gray-800">{s.start_time}–{s.end_time}</b> },
    { key: "planned_hours", label: "Hours", type: "hours" },
    { key: "note", label: "Note" },
    { key: "act", label: "", text: () => "", render: (s) => s.kind === "work" && enabled ? (
      s.swap_pending ? <span className="badge-amber">swap asked</span>
        : <button className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" onClick={() => setAsk(s)} data-testid="ask-swap"><Shuffle className="w-3 h-3" />Swap</button>) : null },
  ];
  const incoming = (swaps.data || []).filter((s) => s.for_me);
  return (
    <div>
      <PageHeader title="My shifts" icon={CalendarDays} subtitle="Your published roster for the next four weeks. Need a change? Ask a colleague to swap — they accept, then your manager approves." />
      {!!incoming.length && (
        <div className="card mb-4 border-amber-200 bg-amber-50/40" data-testid="swap-incoming">
          <div className="text-sm font-medium text-amber-900 mb-2">A colleague asked you to swap</div>
          {incoming.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5 border-t border-amber-100 first:border-t-0 text-sm">
              <span><b>{s.requester}</b> gives you {s.requester_shift?.work_date} {s.requester_shift?.start_time}–{s.requester_shift?.end_time} and takes your {s.partner_shift?.work_date} {s.partner_shift?.start_time}–{s.partner_shift?.end_time}{s.reason ? ` — “${s.reason}”` : ""}</span>
              <span className="flex gap-1">
                <button className="btn-primary !py-1 text-xs inline-flex items-center gap-1" onClick={() => act.mutate({ id: s.id, a: "accept" })} data-testid="swap-accept"><Check className="w-3.5 h-3.5" />Accept</button>
                <button className="btn-secondary !py-1 text-xs inline-flex items-center gap-1" onClick={() => act.mutate({ id: s.id, a: "decline" })}><X className="w-3.5 h-3.5" />Decline</button>
              </span>
            </div>))}
        </div>)}
      <div className="card !p-0 mb-4">
        {shifts.isLoading ? <div className="py-10 grid place-items-center"><Spinner /></div>
          : <DataGrid columns={cols} rows={shifts.data?.shifts || []} rowKey={(s) => s.id} exportName="my-shifts" minWidth={640} empty="No published shifts for you in the next four weeks." />}
      </div>
      <h3 className="font-display font-semibold text-gray-800 mb-2 text-sm">My swap requests</h3>
      <div className="card !p-0">
        <SwapGrid rows={swaps.data || []} actions={(s) => (s.status.startsWith("pending") && !s.for_me
          ? <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => act.mutate({ id: s.id, a: "cancel" })}>Withdraw</button> : null)} />
      </div>
      {ask && <AskSwap shift={ask} onClose={() => setAsk(null)} onDone={() => { setAsk(null); refresh(); }} />}
    </div>
  );
}

function AskSwap({ shift, onClose, onDone }: { shift: MyShift; onClose: () => void; onDone: () => void }) {
  const opts = useQuery({ queryKey: ["swap-options", shift.id], queryFn: () => rotaApi.swapOptions(shift.id) });
  const [pick, setPick] = useState<SwapOption | null>(null);
  const [reason, setReason] = useState("");
  const send = useMutation({ mutationFn: () => rotaApi.requestSwap({ my_shift_id: shift.id, partner_shift_id: pick!.id, reason: reason || undefined }),
    onSuccess: () => { toast.success("Swap asked — your colleague gets a notification"); onDone(); }, onError: (e) => toast.error(rotaErr(e)) });
  const cols: GridColumn<SwapOption>[] = [
    { key: "pick", label: "", text: () => "", render: (o) => <input type="radio" name="swap" checked={pick?.id === o.id} disabled={!!o.problems.length} onChange={() => setPick(o)} /> , width: "36px" },
    { key: "employee", label: "Colleague" }, { key: "work_date", label: "Date", type: "date" },
    { key: "time", label: "Their shift", text: (o) => `${o.start_time}–${o.end_time}` },
    { key: "problems", label: "Rules", text: (o) => o.problems.join("; "), render: (o) => o.problems.length
      ? <span className="text-[11px] text-rose-700 inline-flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{o.problems.join("; ")}</span> : <span className="text-[11px] text-emerald-700">OK</span> },
  ];
  return (
    <Modal title={`Swap your ${shift.work_date} ${shift.start_time}–${shift.end_time}`} subtitle="Colleagues in your department with a published shift within two weeks. A swap that breaks the rest or night-shift rules cannot be picked." onClose={onClose} size="xl"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={!pick || send.isPending} onClick={() => send.mutate()} data-testid="swap-send">Ask {pick ? pick.employee.split(" ")[0] : "colleague"}</button></>}>
      {opts.isLoading ? <Spinner /> : <DataGrid columns={cols} rows={opts.data?.options || []} rowKey={(o) => o.id} exportName="swap-options" minWidth={720} pageSizeDefault={10} onRowClick={(o) => !o.problems.length && setPick(o)} empty="No colleague in your department has a shift to swap in these two weeks." />}
      <div className="mt-3 max-w-xl"><Field label="Reason (optional, your colleague and manager see it)"><Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Doctor's appointment" /></Field></div>
    </Modal>
  );
}
