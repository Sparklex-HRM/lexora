import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { payrollApi } from "../../api/endpoints";
import PayslipModal from "../../components/PayslipModal";
import { useLang, useT } from "../../i18n";
import { fmtDate, fmtMoney } from "../../i18n/format";
import { useAuthStore } from "../../store/authStore";
import { MobileShell, useMe } from "./_mobile";

/** /mobile/payslips — the list (cached offline); the PDF itself needs a network. */
export default function MobilePayslipsPage() {
  const t = useT();
  const lang = useLang();
  const { me } = useMe();
  const cur = "CAD";
  const slips = useQuery({ queryKey: ["m-slips", me?.id], queryFn: () => payrollApi.payslips(me!.id), enabled: !!me });
  const [stub, setStub] = useState<string | null>(null);
  if (!me) return <MobileShell title={t("pay.title")}><div className="card text-sm text-gray-600">{t("common.not_linked")}</div></MobileShell>;
  return (
    <MobileShell title={t("pay.title")}>
      <div className="card !p-0 divide-y divide-surface-border">
        {(slips.data || []).length === 0 && <div className="py-8 text-center text-sm text-gray-400">{t("pay.no_slips")}</div>}
        {(slips.data || []).map((p) => (
          <button key={p.stub_id} className="w-full text-left px-3 py-3 flex items-center justify-between hover:bg-gray-50" onClick={() => setStub(p.stub_id)}>
            <div>
              <div className="font-medium text-gray-800">{fmtDate(p.period_end, lang)}</div>
              <div className="text-xs text-gray-400">{t("pay.paid_on", { date: fmtDate(p.pay_date, lang) })}</div>
            </div>
            <div className="text-right">
              <div className="font-bold tabular-nums">{fmtMoney(p.net_pay, lang, cur)}</div>
              <div className="text-[11px] text-primary-600">{t("common.view")} →</div>
            </div>
          </button>
        ))}
      </div>
      {stub && <PayslipModal stubId={stub} onClose={() => setStub(null)} />}
    </MobileShell>
  );
}
