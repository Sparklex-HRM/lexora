import { CalendarClock } from "lucide-react";
import LifecycleAgentPage from "./LifecycleAgentPage";
import WhatsAppChannelCard from "./WhatsAppChannelCard";

/** Attendance Agent (2026-10-26) — /ai/agents/attendance. Office roles. */
export default function AttendanceAgentPage() {
  return (
    <div>
      <LifecycleAgentPage slug="attendance" title="Attendance Agent" icon={CalendarClock} caseLabel="Employee"
        intro="Daily: missing and single punches, late marks, overtime beyond the limits, women's night-shift safeguards, regularisations and leave waiting — resolved before the payroll cut-off." />
      <div className="mt-4"><WhatsAppChannelCard /></div>
    </div>
  );
}
