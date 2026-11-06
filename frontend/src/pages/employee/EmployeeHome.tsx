import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  CalendarDays, Wallet, Inbox, Sparkles, Award, ClipboardCheck, Clock, Send, Gift,
  LifeBuoy, ArrowRight, CheckSquare, LucideIcon,
} from "lucide-react";
import { useAuthStore } from "../../store/authStore";
import { leaveApi, mastersApi, payrollApi, money } from "../../api/endpoints";
import { approvalsApi } from "../../api/platform";
import { engageApi } from "../../api/engage";
import { perfApi } from "../performance/api";
import { usableLeave, eventLeave } from "../../api/ess";
import { REGION_NAMES } from "../../api/regions";
import { MyWeekPanel } from "../settings/AttentionPanel";
import { Salary, SalaryToggle } from "../../components/SalaryToggle";
import { FeedPreview } from "./FeedPage";   // portal batch 3

const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const nice = (iso?: string | null) => iso ? new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString("en-CA", { weekday: "short", day: "numeric", month: "short" }) : "—";
const greet = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };

function Card({ title, icon: Icon, to, link, children, testid }: { title: string; icon: LucideIcon; to?: string; link?: string; children: React.ReactNode; testid?: string }) {
  return (
    <div className="card !p-0 flex flex-col" data-testid={testid}>
      <div className="flex items-center justify-between px-4 pt-3.5 pb-2">
        <h3 className="font-display font-semibold text-gray-800 text-[13px] flex items-center gap-1.5"><Icon className="h-3.5 w-3.5 text-primary-500" />{title}</h3>
        {to && <Link to={to} className="text-[11px] text-primary-600 hover:underline flex items-center gap-0.5">{link || "Open"}<ArrowRight className="w-3 h-3" /></Link>}
      </div>
      <div className="px-4 pb-3.5 flex-1">{children}</div>
    </div>
  );
}

/** Home for the EMPLOYEE role: only what is theirs — no org KPIs. */
export default function EmployeeHome() {
  const user = useAuthStore((s) => s.user);
  const today = localToday();
  const inbox = useQuery({ queryKey: ["home", "inbox"], queryFn: approvalsApi.inbox, retry: false });
  const mine = useQuery({ queryKey: ["home", "mine"], queryFn: approvalsApi.mine, retry: false });
  const bal = useQuery({ queryKey: ["lv", "balances", "me"], queryFn: () => leaveApi.balances(), retry: false });
  const hol = useQuery({ queryKey: ["m", "stat-holidays"], queryFn: () => mastersApi.list("stat-holidays"), retry: false });
  const slips = useQuery({ queryKey: ["payslips", "me"], queryFn: () => payrollApi.payslips(), retry: false });
  const reviews = useQuery({ queryKey: ["perf", "reviews", "mine"], queryFn: () => perfApi.reviews("mine"), retry: false });
  const kudos = useQuery({ queryKey: ["home", "kudos"], queryFn: () => engageApi.kudos(), retry: false });

  const waiting = (inbox.data?.items || []).filter((i: any) => i.status === "pending");
  const myPending = (mine.data?.items || []).filter((i: any) => i.status === "pending");
  const province = user?.province || "";
  const nextHol = (hol.data || []).filter((h: any) => (h.holiday_date || "") >= today && (!h.province || h.province === province) && !h.is_optional)
    .sort((a: any, b: any) => a.holiday_date.localeCompare(b.holiday_date)).slice(0, 4);
  const lastSlip = [...(slips.data || [])].sort((a, b) => (b.pay_date || "").localeCompare(a.pay_date || ""))[0];
  const usable = (bal.data?.balances || []).filter((b: any) => (b.category || "usable") === "usable" && b.entitled > 0);
  const events = eventLeave(bal.data);
  const due = (reviews.data || []).filter((r: any) => r.employee_id === user?.employee_id && ["self", "ack", "published"].includes(r.stage));
  const myKudos = (kudos.data?.kudos || []).filter((k: any) => k.to_employee_id === user?.employee_id && k.status === "approved").slice(0, 3);

  const quick = [
    { to: "/leave?apply=1", label: "Apply leave", icon: CalendarDays },
    { to: "/attendance", label: "Fix attendance", icon: Clock },
    { to: "/my-pay", label: "Payslips", icon: Wallet },
    { to: "/engage/kudos", label: "Give kudos", icon: Award },
    { to: "/engage/helpdesk", label: "Ask HR", icon: LifeBuoy },
    { to: "/raise-request", label: "Raise a request", icon: Send },
  ];

  return (
    <div className="space-y-4" data-testid="employee-home">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-display font-bold text-gray-800">{greet()}, {user?.first_name || "there"}</h1>
          <p className="text-sm text-gray-400">{new Date().toLocaleDateString("en-CA", { weekday: "long", day: "numeric", month: "long", year: "numeric" })} · {user?.org_name}</p>
        </div>
        <SalaryToggle />
      </div>

      <div className="grid grid-cols-4 sm:grid-cols-8 gap-2">
        {quick.map((q) => (
          <Link key={q.label} to={q.to} className="card !p-2 flex flex-col items-center gap-1 hover:shadow-md transition-shadow text-center">
            <span className="h-9 w-9 rounded-xl grid place-items-center bg-primary-50 text-primary-600"><q.icon className="h-4 w-4" /></span>
            <span className="text-[10px] sm:text-[11px] text-gray-600 leading-tight">{q.label}</span>
          </Link>
        ))}
      </div>

      <MyWeekPanel />

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <Card title="Waiting on me" icon={CheckSquare} to="/approvals" testid="home-waiting">
          {!waiting.length ? <p className="text-sm text-gray-400">Nothing is waiting for your decision.</p> : (
            <ul className="divide-y divide-surface-border text-sm">{waiting.slice(0, 4).map((i: any) => (
              <li key={i.id} className="py-1.5"><div className="text-gray-800 truncate">{i.title}</div><div className="text-[11px] text-gray-400">{i.kind_label}</div></li>))}</ul>)}
        </Card>
        <Card title="My requests" icon={Inbox} to="/my-requests" link="All requests" testid="home-my-requests">
          {!myPending.length ? <p className="text-sm text-gray-400">None of your requests is waiting.</p> : (
            <ul className="divide-y divide-surface-border text-sm">{myPending.slice(0, 4).map((i: any) => (
              <li key={i.id} className="py-1.5 flex justify-between gap-2"><span className="truncate text-gray-800">{i.title}</span><span className="badge-amber shrink-0">{i.waiting_on ? `with ${String(i.waiting_on).split(",")[0]}` : "pending"}</span></li>))}</ul>)}
        </Card>
        <Card title="Latest payslip" icon={Wallet} to="/my-pay" link="My pay" testid="home-payslip">
          {!lastSlip ? <p className="text-sm text-gray-400">No payslip yet.</p> : (
            <div>
              <div className="text-2xl font-bold text-gray-800"><Salary value={lastSlip.net_pay} fmt={money} /></div>
              <div className="text-xs text-gray-500">net pay · paid {nice(lastSlip.pay_date)} · gross <Salary value={lastSlip.gross} fmt={money} /></div>
            </div>)}
        </Card>
        <Card title="Leave you can take" icon={CalendarDays} to="/leave" link="Apply" testid="home-leave">
          <div className="text-2xl font-bold text-primary-600 tabular-nums">{usableLeave(bal.data)} <span className="text-sm font-normal text-gray-500">days</span></div>
          <div className="flex flex-wrap gap-1.5 mt-1.5">{usable.map((b: any) => <span key={b.id} className="text-[11px] px-2 py-0.5 rounded-full bg-surface border border-surface-border">{b.leave_type_code} {b.remaining}/{b.entitled}</span>)}</div>
          {events.length > 0 && <p className="text-[11px] text-gray-400 mt-1.5">Event leave, not counted: {events.map((e) => `${e.name} ${e.remaining}`).join(" · ")}</p>}
        </Card>
        <Card title={`Holidays${province ? ` · ${REGION_NAMES.CA[province] || province}` : ""}`} icon={Sparkles} to="/stat-holidays" link="Full list" testid="home-holidays">
          {!nextHol.length ? <p className="text-sm text-gray-400">No upcoming holiday on the list.</p> : (
            <ul className="text-sm space-y-1">{nextHol.map((h: any) => <li key={h.id} className="flex justify-between gap-2"><span className="truncate">{h.name}</span><span className="text-gray-500 text-xs shrink-0">{nice(h.holiday_date)}</span></li>)}</ul>)}
        </Card>
        <Card title="Review due" icon={ClipboardCheck} to="/performance/reviews" testid="home-review">
          {!due.length ? <p className="text-sm text-gray-400">No review needs you right now.</p> : due.slice(0, 2).map((r: any) => (
            <div key={r.id} className="text-sm"><div className="text-gray-800">{r.stage === "self" ? "Your self review is open" : r.stage === "ack" ? "Acknowledge your review" : "Your review is published"}</div>
              <div className="text-[11px] text-gray-400">{(r.goals || []).length} goal(s) · manager {r.manager_name || "—"}</div></div>))}
        </Card>
        <Card title="Kudos for you" icon={Award} to="/engage/kudos" testid="home-kudos">
          {!myKudos.length ? <p className="text-sm text-gray-400">No kudos yet — give one to a colleague.</p> : (
            <ul className="space-y-1.5 text-sm">{myKudos.map((k: any) => <li key={k.id}><span className="text-gray-800">“{k.message}”</span><div className="text-[11px] text-gray-400">{k.from_name} · {k.value_tag || "Kudos"}</div></li>)}</ul>)}
        </Card>
        <div data-testid="home-feed"><FeedPreview /></div>
        <Card title="Benefits" icon={Gift} to="/my-benefits" testid="home-benefits">
          <p className="text-sm text-gray-500">Your coverage and enrolment</p>
        </Card>
      </div>
    </div>
  );
}
