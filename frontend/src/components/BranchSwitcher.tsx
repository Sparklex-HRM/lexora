import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, ChevronDown, Check } from "lucide-react";
import { opsApi } from "../api/endpoints";
import { useAuthStore } from "../store/authStore";

/** Header branch selector, for org-scope users only. Persists the chosen branch
 *  and re-fetches — the gateway resolves it into X-Branch-Id, which services scope by.
 *  Branch-scope users don't see it (locked to their branch server-side). */
export default function BranchSwitcher() {
  const scope = useAuthStore((s) => s.user?.branch_scope);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string>(() => {
    try { return localStorage.getItem("lx-branch") || ""; } catch { return ""; }
  });
  const { data } = useQuery({ queryKey: ["ops", "branches"], queryFn: () => opsApi.list("branches"), enabled: scope === "org" });
  const branches = data || [];
  const label = selected ? (branches.find((b) => b.id === selected)?.name || "Branch") : "All branches";

  if (scope !== "org") return null;   // branch-scope users are locked to their branch

  const pick = (id: string) => {
    setSelected(id);
    try { localStorage.setItem("lx-branch", id); } catch { /* ignore */ }
    setOpen(false);
    qc.invalidateQueries();           // refetch everything with the new branch view
  };

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-gray-50 hover:bg-gray-100 text-gray-600 border border-surface-border">
        <MapPin className="w-3.5 h-3.5 text-primary-500" />
        <span className="hidden sm:inline max-w-[120px] truncate">{label}</span>
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-1 w-56 bg-white border border-surface-border rounded-xl shadow-lg z-40 py-1 max-h-72 overflow-y-auto">
            <Item label="All branches" active={!selected} onClick={() => pick("")} />
            {branches.length === 0 && <div className="px-3 py-2 text-[11px] text-gray-400">No branches yet — add them in Setup → Locations &amp; Branches.</div>}
            {branches.map((b) => (
              <Item key={b.id} label={`${b.name}${b.city ? ` · ${b.city}` : ""}`} active={selected === b.id} onClick={() => pick(b.id)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Item({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className="w-full flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-left hover:bg-primary-50 text-gray-700">
      <span className="truncate">{label}</span>{active && <Check className="w-3.5 h-3.5 text-primary-600 shrink-0" />}
    </button>
  );
}
