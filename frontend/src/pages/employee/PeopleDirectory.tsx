import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Users, Mail } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { avatarClass } from "../../components/CommandPalette";
import { employeeApi, DirectoryPerson } from "../../api/endpoints";

/**
 * Read-only people directory for the employee role (S1). Data comes from
 * GET /employees/directory, which only ever returns work facts: name, designation,
 * department, work e-mail, manager, work location. No pay, SIN / bank,
 * personal phone or address — and no add / edit / exit / bulk actions here.
 */
export default function PeopleDirectory() {
  const nav = useNavigate();
  const [dept, setDept] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["employees", "directory"], queryFn: employeeApi.directory });
  const all = useMemo(() => (Array.isArray(data) ? data : []), [data]);
  const deptOpts = useMemo(() => [...new Set(all.map((p) => p.department).filter(Boolean))].sort() as string[], [all]);
  const rows = useMemo(() => all.filter((p) => !dept || p.department === dept), [all, dept]);
  const place = (p: DirectoryPerson) => p.work_location || p.branch_name || "";

  const columns: GridColumn<DirectoryPerson>[] = [
    { key: "name", label: "Name", text: (p) => p.display_name,
      render: (p) => (
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`w-7 h-7 rounded-full grid place-items-center text-[11px] font-bold shrink-0 ${avatarClass(p.id || p.employee_number || "")}`} aria-hidden="true">{p.initials}</span>
          <div className="min-w-0">
            <div className="font-medium text-gray-800 truncate">{p.display_name}{p.is_self && <span className="badge-teal ml-1.5">You</span>}</div>
            <div className="text-xs text-gray-400 truncate">{p.employee_number || ""}</div>
          </div>
        </div>
      ) },
    { key: "job_title", label: "Designation", text: (p) => p.job_title || "" },
    { key: "department", label: "Department", text: (p) => p.department || "" },
    { key: "manager_name", label: "Reports to", text: (p) => p.manager_name || "",
      render: (p) => p.manager_name ? <span>{p.manager_name}</span> : <span className="text-gray-300">—</span> },
    { key: "location", label: "Location", text: place,
      render: (p) => place(p) ? <span>{place(p)}</span> : <span className="text-gray-300">—</span> },
    { key: "work_email", label: "Work e-mail", text: (p) => p.work_email || "",
      render: (p) => p.work_email
        ? <a href={`mailto:${p.work_email}`} onClick={(ev) => ev.stopPropagation()} className="inline-flex items-center gap-1 text-primary-600 hover:underline"><Mail className="w-3.5 h-3.5" />{p.work_email}</a>
        : <span className="text-gray-300">—</span> },
  ];

  return (
    <div className="pb-10">
      <PageHeader title="People" icon={Users}
        subtitle="Colleagues' work details — name, designation, department, manager, location and work e-mail." />
      {isLoading ? <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div> : (
        <DataGrid<DirectoryPerson> columns={columns} rows={rows} rowKey={(p) => p.id} exportName="people-directory"
          searchPlaceholder="Search name, designation, department, location…" empty="Nobody matches."
          initialSort={{ key: "name", dir: "asc" }} minWidth={900}
          searchText={(p) => `${p.display_name} ${p.employee_number || ""} ${p.job_title || ""} ${p.department || ""} ${place(p)} ${p.work_email || ""} ${p.manager_name || ""}`}
          onRowClick={(p) => { if (p.is_self) nav(`/employees/${p.id}`); }}
          filters={
            <select className="input !py-1.5 !w-auto text-sm" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Department filter">
              <option value="">All departments</option>{deptOpts.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>} />
      )}
    </div>
  );
}
