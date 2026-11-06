import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, ChevronDown, Check } from "lucide-react";
import { Link } from "react-router-dom";
import { entitiesApi, getViewEntity, setViewEntity } from "../api/entities";
import { useAuthStore, ADMIN_ROLES } from "../store/authStore";
import { can } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const PICKER_ROLES = [...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"];

/** Header legal-entity selector (D4). Shown to admins / HR / payroll when the organisation
 *  has more than one legal entity. The choice is persisted (id only) and sent as
 *  X-View-Entity on every call, so employee lists, pay runs, filings and reports narrow
 *  to that group company; "All entities" clears it. */
export default function EntityPicker() {
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string>(getViewEntity);
  const enabled = can(user, "people.view", PICKER_ROLES);
  const { data } = useQuery({ queryKey: ["entities", "headcount"], queryFn: entitiesApi.headcount, enabled, staleTime: 60_000 });
  const entities = data?.entities || [];
  if (!enabled || entities.length < 2) return null;

  const current = entities.find((e) => e.id === selected);
  const label = current ? current.short_code || current.name : "All entities";
  const pick = (id: string) => {
    setSelected(id);
    setViewEntity(id);
    setOpen(false);
    qc.invalidateQueries();           // refetch everything under the new entity view
  };

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title={current ? current.name : "Every legal entity of the group"}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-gray-50 hover:bg-gray-100 text-gray-600 border border-surface-border">
        <Building2 className="w-3.5 h-3.5 text-primary-500" />
        <span className="hidden sm:inline max-w-[140px] truncate">{label}</span>
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-1 w-72 bg-white border border-surface-border rounded-xl shadow-lg z-40 py-1 max-h-80 overflow-y-auto">
            <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Legal entity</div>
            <Item label="All entities" sub="group view" active={!selected} onClick={() => pick("")} />
            {entities.filter((e) => e.is_active).map((e) => (
              <Item key={e.id} label={e.name} sub={`${e.short_code}${e.is_default ? " · default" : ""} · ${e.employee_count ?? 0} people`}
                active={selected === e.id} onClick={() => pick(e.id)} />
            ))}
            {can(user, "settings.admin", ADMIN_ROLES) && (
              <Link to="/settings/entities" onClick={() => setOpen(false)} className="block px-3 py-2 text-[11px] text-primary-600 hover:bg-primary-50 border-t border-surface-border mt-1">Manage legal entities →</Link>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Item({ label, sub, active, onClick }: { label: string; sub?: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className="w-full flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-left hover:bg-primary-50 text-gray-700">
      <span className="min-w-0"><span className="block truncate">{label}</span>{sub && <span className="block text-[11px] text-gray-400 truncate">{sub}</span>}</span>
      {active && <Check className="w-3.5 h-3.5 text-primary-600 shrink-0" />}
    </button>
  );
}
