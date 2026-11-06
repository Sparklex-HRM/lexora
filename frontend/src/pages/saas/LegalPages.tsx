import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import Logo from "../../components/Logo";

// PLACEHOLDER LEGAL TEXT — written by the product team as a structure for counsel,
// NOT legal advice and NOT reviewed by a lawyer. Bracketed items must be filled in.
const DRAFT = "DRAFT — to be reviewed by counsel before publication";
const VERSION = "draft-2026-10";

function Page({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-surface-muted dark:bg-gray-950 px-4 py-8">
      <div className="max-w-3xl mx-auto bg-white dark:bg-gray-900 rounded-2xl shadow p-6 sm:p-10">
        <Link to="/login"><Logo className="h-9 w-auto" /></Link>
        <div className="mt-5 mb-6 rounded-lg border border-amber-300 bg-amber-50 text-amber-900 px-4 py-3 text-sm flex gap-2" data-testid="draft-banner">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span><b>{DRAFT}.</b> This is placeholder text describing how Lexora HRM works. It is not legal advice and has not been reviewed by a lawyer. Items in [brackets] are not yet filled in.</span>
        </div>
        <h1 className="font-display text-2xl font-bold text-gray-900 dark:text-gray-100">{title}</h1>
        <p className="text-xs text-gray-400 mb-6">Version {VERSION}</p>
        <div className="prose-sm space-y-4 text-sm leading-6 text-gray-700 dark:text-gray-300 [&_h2]:font-display [&_h2]:font-semibold [&_h2]:text-gray-900 [&_h2]:dark:text-gray-100 [&_h2]:text-base [&_h2]:mt-6 [&_ul]:list-disc [&_ul]:pl-5">
          {children}
        </div>
        <p className="mt-10 text-xs text-gray-400"><Link to="/legal/privacy" className="hover:text-primary-600">Privacy Policy</Link> · <Link to="/legal/terms" className="hover:text-primary-600">Terms of Service</Link> · <Link to="/signup" className="hover:text-primary-600">Sign up</Link> · <Link to="/login" className="hover:text-primary-600">Sign in</Link></p>
      </div>
    </div>
  );
}

export function PrivacyPolicyPage() {
  return (
    <Page title="Privacy Policy">
      <p>This policy explains how [Lexora legal entity name] ("Lexora", "we") handles personal information in the Lexora HRM service in Canada, under the Personal Information Protection and Electronic Documents Act (PIPEDA) and, where they apply, the provincial private-sector privacy laws of Québec, Alberta and British Columbia.</p>
      <h2>1. Two roles</h2>
      <ul>
        <li><b>Your employer is accountable</b> for employee personal information (pay, SIN, bank, attendance …). It decides why and how that information is used, and answers your requests. Its own employee privacy notice is shown to you inside Lexora HRM (Privacy Centre).</li>
        <li><b>Lexora is a service provider</b> for that information and processes it only on your employer's instructions under a data-processing agreement.</li>
        <li>For the <b>account information of the people who sign up and administer an organization</b> (name, work e-mail, mobile, sign-in records, billing details), Lexora decides the purposes and is accountable for it.</li>
      </ul>
      <h2>2. What we collect for accounts</h2>
      <ul>
        <li>Sign-up: company name, province or territory, your name, work e-mail, optional mobile, password (stored only as a one-way hash).</li>
        <li>Security: sign-in time, IP address, device / browser, failed attempts, two-step sign-in status (the authenticator secret is stored encrypted).</li>
        <li>Billing: legal name, GST/HST number, billing province, address and e-mail, invoices. Invoices are paid by EFT or cheque; Lexora does not store card details.</li>
      </ul>
      <h2>3. How employee identifiers are protected</h2>
      <p>Social Insurance Number (SIN), bank account and passport number are encrypted at rest (AES-256-GCM). They are shown masked; a full value is shown only to authorised roles on an explicit "reveal", and every reveal is recorded in a tamper-evident audit log.</p>
      <h2>4. Why we use account information</h2>
      <ul><li>To create and secure your account, provide the service, bill for it (including GST/HST invoices), prevent abuse, and meet legal obligations.</li></ul>
      <h2>5. How long we keep it</h2>
      <p>Account information is kept while the organization uses Lexora HRM and for [period to be confirmed by counsel] afterwards. Payroll and tax records of employees are kept for the period your employer configures (the CRA generally requires six years; each period is marked "needs confirmation" until the employer confirms it).</p>
      <h2>6. Your rights</h2>
      <p>Employees exercise their rights (access, correction, withdrawal of consent, complaints) with their employer through the Privacy Centre in Lexora HRM; access requests are answered within 30 days. Account holders may write to our Privacy Officer: [name, e-mail, postal address — to be filled in]. If not satisfied, you may complain to the Office of the Privacy Commissioner of Canada or your provincial commissioner.</p>
      <h2>7. Service providers and transfers</h2>
      <p>[List of hosting and e-mail providers and the countries where information is stored or accessed — to be filled in.]</p>
      <h2>8. Breaches</h2>
      <p>If a breach of security safeguards occurs we will inform the affected organization without delay, so that it can report breaches that create a real risk of significant harm to the Privacy Commissioner and notify affected people as the law requires. A record of every breach is kept for 24 months.</p>
      <h2>9. Changes</h2>
      <p>We will show the new version and its date here and inside the product before it applies.</p>
    </Page>
  );
}

export function TermsPage() {
  return (
    <Page title="Terms of Service">
      <p>These terms are between [Lexora legal entity name, business number, registered address] ("Lexora") and the organization that signs up ("Customer"). The person who signs up confirms they may bind the Customer.</p>
      <h2>1. The service</h2>
      <p>Lexora HRM is software for HR, attendance, leave and Canadian payroll (CPP/QPP, EI, federal and provincial income tax, T4 / RL-1, ROE). Lexora HRM calculates from the rules and rates configured in the product; the Customer remains responsible for its remittances and filings with the CRA and Revenu Québec, and should have its payroll set-up confirmed by its own accountant.</p>
      <h2>2. Trial</h2>
      <p>A new organization gets a [14]-day free trial. If no plan is chosen by the end of the trial, the account becomes <b>read-only</b>: the Customer can still sign in, view and download pay stubs, year-end slips and reports, but cannot add or change data.</p>
      <h2>3. Plans, fees and GST/HST</h2>
      <ul>
        <li>Fees are in Canadian dollars per active employee per month with a plan minimum, as shown on the pricing page at the time of purchase. GST or HST is added by the Customer's billing province.</li>
        <li>Invoices are issued monthly and paid by EFT or cheque. If an invoice is not paid, full access continues for a grace period of [7] days, after which the account becomes read-only until payment.</li>
        <li>Plan limits (such as the maximum number of employees) apply as listed for each plan.</li>
      </ul>
      <h2>4. Customer data</h2>
      <p>The Customer owns its data. Lexora processes it only to provide the service, under the Data Processing Terms in the product. The Customer can export all of its data at any time. On termination, data is returned or deleted after [period], except what law requires to be kept.</p>
      <h2>5. Security</h2>
      <p>Two-step sign-in is mandatory for administrator and payroll roles. The Customer keeps its users' credentials safe and tells Lexora promptly about any suspected misuse.</p>
      <h2>6. Acceptable use</h2>
      <p>No unlawful use, no attempt to access other customers' data, no automated abuse of the service.</p>
      <h2>7. Liability</h2>
      <p>[Limitation of liability, warranty disclaimer and indemnity — to be drafted by counsel.]</p>
      <h2>8. Governing law</h2>
      <p>[Laws of the Province of [Ontario] and the federal laws of Canada applicable there; courts at [city] — to be confirmed by counsel.]</p>
    </Page>
  );
}
