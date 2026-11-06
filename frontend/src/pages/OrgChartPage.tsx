import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Network, Users, Building2, UserCog } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { employeeApi } from "../api/endpoints";

const initials = (e: any) => ((e.first_name?.[0] || "") + (e.last_name?.[0] || "")).toUpperCase() || "?";
const isManager = (e: any) => /manager|director|lead|head|chief|vp|president/i.test(`${e.job_title || ""}`);

export default function OrgChartPage() {
  const { data, isLoading } = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });

  const groups = useMemo(() => {
    const map: Record<string, any[]> = {};
    (data || []).filter((e: any) => e.status !== "terminated").forEach((e: any) => {
      const d = e.department || "Unassigned";
      (map[d] || (map[d] = [])).push(e);
    });
    Object.values(map).forEach((list) => list.sort((a, b) => (isManager(b) ? 1 : 0) - (isManager(a) ? 1 : 0)));
    return Object.entries(map).sort((a, b) => b[1].length - a[1].length);
  }, [data]);

  const active = (data || []).filter((e: any) => e.status !== "terminated");
  const managers = active.filter(isManager).length;

  return (
    <div>
      <PageHeader title="Org Chart" icon={Network} subtitle="Reporting structure, live from the employee directory" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <StatTile label="Active employees" value={active.length} icon={Users} />
        <StatTile label="Departments" value={groups.length} icon={Building2} />
        <StatTile label="People managers" value={managers} icon={UserCog} accent />
        <StatTile label="Avg team size" value={groups.length ? Math.round(active.length / groups.length) : 0} />
      </div>

      {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : !active.length ? <div className="card"><Empty>No employees yet — add them under People → Employees.</Empty></div>
        : (
          <div className="grid lg:grid-cols-2 gap-4">
            {groups.map(([dept, people]) => (
              <div key={dept} className="card">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg bg-primary-50 grid place-items-center"><Building2 className="w-4 h-4 text-primary-600" /></div>
                    <h3 className="font-display font-semibold text-gray-800">{dept}</h3>
                  </div>
                  <span className="text-xs text-gray-400">{people.length} {people.length === 1 ? "person" : "people"}</span>
                </div>
                <div className="space-y-1.5">
                  {people.map((e: any) => (
                    <div key={e.id} className={`flex items-center gap-2.5 rounded-lg px-2 py-1.5 ${isManager(e) ? "bg-primary-50/50" : "hover:bg-gray-50"}`}>
                      <div className={`w-8 h-8 rounded-full grid place-items-center text-white text-xs font-semibold shrink-0 ${isManager(e) ? "bg-gradient-to-br from-primary-500 to-teal-500" : "bg-gradient-to-br from-primary-300 to-teal-300"}`}>{initials(e)}</div>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-gray-800 truncate">{e.first_name} {e.last_name}{isManager(e) && <span className="ml-1.5 text-[9px] font-bold uppercase tracking-wide text-primary-600">lead</span>}</div>
                        <div className="text-xs text-gray-400 truncate">{e.job_title || "—"}{e.work_location ? ` · ${e.work_location}` : ""}</div>
                      </div>
                      {e.manager_name && <div className="text-[10px] text-gray-400 text-right shrink-0 hidden sm:block">reports to<div className="text-gray-600">{e.manager_name}</div></div>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}
