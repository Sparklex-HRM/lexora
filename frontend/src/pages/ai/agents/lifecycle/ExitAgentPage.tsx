import { LogOut } from "lucide-react";
import LifecycleAgentPage from "./LifecycleAgentPage";

/** Exit Agent (2026-10-26) — /ai/agents/exit. Office roles. The final-pay amount always comes from the payroll engine (POST /payroll/final-pay). */
export default function ExitAgentPage() {
  return <LifecycleAgentPage slug="exit" title="Exit Agent" icon={LogOut} caseLabel="Exit case"
    intro="Notice and last working day, clearance per department chased until signed, the final cheque (vacation owed included) on the provincial final-pay clock, the ROE clock, and relieving / experience letter drafts." />;
}
