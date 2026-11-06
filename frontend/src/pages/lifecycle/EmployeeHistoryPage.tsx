import { useNavigate, useParams } from "react-router-dom";
import { History } from "lucide-react";
import { PageHeader } from "../../components/ui";
import EmployeeLifecyclePanel from "../../components/lifecycle/EmployeeLifecyclePanel";
import { Select, useEmployees, empLabel } from "../common/_shared";

/** HR view of one employee's history: salary, career changes, probation, letters.
 *  Route: /employee-history and /employee-history/:id */
export default function EmployeeHistoryPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const employees = useEmployees();
  const emp: any = employees.find((e: any) => e.id === id);
  return (
    <div>
      <PageHeader title="Employee history" icon={History}
        subtitle={emp ? `${emp.first_name} ${emp.last_name} · ${emp.employee_number} · ${emp.job_title || "—"} · ${emp.department || "—"}` : "Pick an employee to see every salary, job and letter change — who, when and why."}
        actions={<Select className="!w-72" value={id} onChange={(e) => nav(`/employee-history/${e.target.value}`)}>
          <option value="">— pick an employee —</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select>} />
      {id ? <EmployeeLifecyclePanel employeeId={id} /> : <div className="card text-center py-12 text-sm text-gray-400">Pick an employee at the top right.</div>}
    </div>
  );
}
