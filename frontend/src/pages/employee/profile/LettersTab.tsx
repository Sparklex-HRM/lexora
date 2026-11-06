import { Link } from "react-router-dom";
import { LetterList } from "../../../components/lifecycle/EmployeeLifecyclePanel";
import type { TabProps } from "../EmployeeProfilePage";
import { Denied, EmptyLine, Section } from "./_bits";

/** Letters issued to this employee (HR: every status · the employee: issued and
 *  visible only). Reuses the lifecycle LetterList (Open PDF). */
export default function LettersTab({ profile }: TabProps) {
  const letters = profile.letters;
  const canIssue = profile.permissions.can_hr_write;           // HR only — not the pay office (2026-10-21)
  if (letters === null) return <Section title="Letters"><Denied what="letters" /></Section>;
  return (
    <div>
      {canIssue && (
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs text-gray-500">Offer, confirmation, promotion, increment and relieving letters issued through the letter engine.</p>
          <Link to="/letters" className="btn-secondary !py-1 text-xs">Issue a letter →</Link>
        </div>
      )}
      {!letters.length
        ? <Section title="Letters"><EmptyLine action={canIssue ? "Issue the first letter" : undefined} to="/letters">No letters issued to this employee yet.</EmptyLine></Section>
        : <LetterList letters={letters} />}
    </div>
  );
}
