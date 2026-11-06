import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Award, BookOpen, CalendarDays, ClipboardList, FileText, LifeBuoy, Package, Smile, Timer } from "lucide-react";
import { leaveApi, payrollApi } from "../../api/endpoints";
import { engageApi } from "../../api/engage";
import { assetsApi } from "../../api/assets";
import { count, onQueueChange } from "../../pwa/queue";
import { useLang, useT } from "../../i18n";
import { fmtMoney } from "../../i18n/format";
import { useAuthStore } from "../../store/authStore";
import { MobileShell, Tile, useMe } from "./_mobile";

/** /mobile/home — the phone home: six big tiles, works offline from the last good copy. */
export default function MobileHomePage() {
  const t = useT();
  const lang = useLang();
  const { me, isLoading, user } = useMe();
  const [queued, setQueued] = useState(0);
  useEffect(() => { count().then(setQueued); return onQueueChange(() => { count().then(setQueued); }); }, []);

  const leave = useQuery({ queryKey: ["m-leave-bal", me?.id], queryFn: () => leaveApi.balances(me!.id), enabled: !!me });
  const slips = useQuery({ queryKey: ["m-slips", me?.id], queryFn: () => payrollApi.payslips(me!.id), enabled: !!me });
  const tickets = useQuery({ queryKey: ["m-tickets"], queryFn: () => engageApi.tickets({ scope: "mine" }) });
  const surveys = useQuery({ queryKey: ["m-surveys"], queryFn: () => engageApi.surveys() });
  const learn = useQuery({ queryKey: ["m-learn"], queryFn: () => engageApi.assignments("mine") });
  const assets = useQuery({ queryKey: ["m-assets"], queryFn: () => assetsApi.mine() });

  const leaveDays = (leave.data?.balances || []).reduce((s, b) => s + (Number(b.remaining ?? 0) || 0), 0);
  const last = (slips.data || [])[0];
  const openTickets = (tickets.data?.tickets || []).filter((x) => !["resolved", "closed"].includes(x.status)).length;
  const openSurveys = (surveys.data?.surveys || []).filter((s) => s.is_open && !s.answered).length;
  const toLearn = (learn.data?.assignments || []).filter((a) => a.status !== "completed").length;
  const toAck = assets.data?.pending_ack || 0;
  const name = me?.first_name || user?.first_name || "";

  return (
    <MobileShell title={t("nav.Mobile Home")} back={null}>
      <div className="flex items-center gap-3 mb-4">
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-primary-500 to-teal-500 grid place-items-center text-white text-lg font-bold">{(name[0] || "?").toUpperCase()}</div>
        <div className="min-w-0">
          <div className="font-display text-xl font-bold text-gray-800 truncate">{t("home.hello", { name })} <Smile className="w-4 h-4 inline text-amber-500" /></div>
          <div className="text-xs text-gray-500 truncate">{me?.job_title || ""}{me?.department ? ` · ${me.department}` : ""}</div>
        </div>
      </div>
      {!isLoading && !me && <div className="card text-sm text-gray-600 mb-3">{t("common.not_linked")}</div>}
      {queued > 0 && <div className="rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-sm px-3 py-2 mb-3">{t("home.queued", { n: queued })}</div>}
      <div className="grid grid-cols-1 gap-2.5">
        <Tile to="/mobile/punch" icon={Timer} title={t("home.punch")} sub={t("home.punch_sub")} color="bg-primary-600" badge={queued || null} />
        <Tile to="/mobile/leave" icon={CalendarDays} title={t("home.leave")} sub={t("home.leave_sub", { days: leaveDays })} color="bg-emerald-600" />
        <Tile to="/mobile/payslips" icon={FileText} title={t("home.payslips")} sub={last ? t("home.payslips_sub", { amount: fmtMoney(last.net_pay, lang, "CAD") }) : t("home.no_pay")} color="bg-teal-600" />
        <Tile to="/engage/helpdesk?new=1" icon={LifeBuoy} title={t("home.ask_hr")} sub={t("home.ask_hr_sub", { n: openTickets })} color="bg-rose-600" badge={openTickets || null} />
        <Tile to="/engage/surveys" icon={ClipboardList} title={t("home.surveys")} sub={t("home.surveys_sub", { n: openSurveys })} color="bg-indigo-600" badge={openSurveys || null} />
        <Tile to="/engage/kudos" icon={Award} title={t("home.kudos")} sub={t("home.kudos_sub")} color="bg-amber-600" />
        <Tile to="/engage/learning" icon={BookOpen} title={t("home.learning")} sub={t("home.learning_sub", { n: toLearn })} color="bg-fuchsia-600" badge={toLearn || null} />
        <Tile to="/engage/my-assets" icon={Package} title={t("home.assets")} sub={t("home.assets_sub", { n: toAck })} color="bg-slate-600" badge={toAck || null} />
      </div>
    </MobileShell>
  );
}
