import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Sparkles } from "lucide-react";
import { err } from "../canada/_shared";
import { leaveEssApi } from "../../api/ess";

const nice = (iso: string) => new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString("en-CA", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const localISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

/** Optional (restricted) holidays: pick up to the company's yearly quota; each pick goes for approval. */
export default function OptionalHolidaysPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["lv", "optional"], queryFn: () => leaveEssApi.optional(), retry: false });
  const pick = useMutation({ mutationFn: (id: string) => leaveEssApi.pick(id),
    onSuccess: () => { toast.success("Sent for approval"); qc.invalidateQueries({ queryKey: ["lv"] }); }, onError: (e) => toast.error(err(e, "Could not pick")) });
  const undo = useMutation({ mutationFn: (id: string) => leaveEssApi.decidePick(id, "cancel"),
    onSuccess: () => { toast.success("Pick withdrawn"); qc.invalidateQueries({ queryKey: ["lv"] }); }, onError: (e) => toast.error(err(e)) });
  if (!data) return null;
  const q = data.quota || { allowed: 0, used: 0, left: 0 };
  const today = localISO();
  return (
    <div className="card !p-0 mb-4" data-testid="optional-holidays">
      <div className="px-4 py-3 border-b border-surface-border flex flex-wrap items-center gap-2">
        <Sparkles className="w-4 h-4 text-primary-500" /><span className="font-display font-semibold text-gray-800 text-sm">Optional holidays {data.year}</span>
        <span className="text-xs text-gray-500">You may take <b>{q.allowed}</b> a year · used {q.used} · <b className="text-primary-600">{q.left} left</b></span>
      </div>
      {!data.holidays.length ? <p className="px-4 py-4 text-sm text-gray-400">HR has not marked any optional holiday for your province this year.</p> : (
        <ul className="divide-y divide-surface-border">{data.holidays.map((h: any) => {
          const p = h.pick;
          const past = (h.holiday_date || "") < today;
          return (
            <li key={h.id} className="px-4 py-2 flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-gray-800 min-w-[10rem]">{h.name}</span>
              <span className="text-xs text-gray-500">{nice(h.holiday_date)}</span>
              <span className="ml-auto flex items-center gap-2">
                {p ? <><span className={p.status === "approved" ? "badge-green" : "badge-amber"}>{p.status === "approved" ? "taken" : "waiting for approval"}</span>
                  {!past && <button className="text-xs text-red-600 hover:underline" onClick={() => undo.mutate(p.id)}>Withdraw</button>}</>
                  : past ? <span className="text-xs text-gray-400">passed</span>
                  : <button className="btn-secondary !py-1 text-xs" disabled={q.left <= 0 || pick.isPending} onClick={() => pick.mutate(h.id)}>Take this day</button>}
              </span>
            </li>);
        })}</ul>)}
    </div>
  );
}
