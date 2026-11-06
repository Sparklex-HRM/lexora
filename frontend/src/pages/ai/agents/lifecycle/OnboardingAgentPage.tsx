import { UserPlus } from "lucide-react";
import LifecycleAgentPage from "./LifecycleAgentPage";

/** Onboarding Agent (2026-10-26) — /ai/agents/onboarding. Office roles. */
export default function OnboardingAgentPage() {
  return <LifecycleAgentPage slug="onboarding" title="Onboarding Agent" icon={UserPlus} caseLabel="Joiner"
    intro="Joining checklist, documents vault, appointment-letter draft, SIN request clock, TD1 forms, IT and induction tasks — chased until the joiner is complete for payroll." />;
}
