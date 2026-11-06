import { useEffect, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { salaryShown, setSalaryShown, MASK } from "../api/ess";

/** Shared "Show salary" state: masked by default, kept in sessionStorage for this session only. */
export function useSalaryVisible(): [boolean, (v: boolean) => void] {
  const [shown, setShown] = useState(salaryShown());
  useEffect(() => {
    const on = () => setShown(salaryShown());
    window.addEventListener("lx-salary-visibility", on);
    return () => window.removeEventListener("lx-salary-visibility", on);
  }, []);
  return [shown, (v: boolean) => { setSalaryShown(v); setShown(v); }];
}

export function SalaryToggle({ className = "" }: { className?: string }) {
  const [shown, set] = useSalaryVisible();
  return (
    <button type="button" onClick={() => set(!shown)} data-testid="salary-toggle"
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border border-surface-border text-gray-600 hover:bg-gray-50 ${className}`}>
      {shown ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}{shown ? "Hide salary" : "Show salary"}
    </button>
  );
}

/** An amount that is masked until the viewer chose "Show salary" this session. */
export function Salary({ value, fmt }: { value: number | null | undefined; fmt: (n: number) => string }) {
  const [shown] = useSalaryVisible();
  if (value === null || value === undefined) return <>—</>;
  return <span className="tabular-nums" data-masked={shown ? "0" : "1"}>{shown ? fmt(value) : MASK}</span>;
}
