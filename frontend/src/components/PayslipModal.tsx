import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Printer, Download, FileText } from "lucide-react";
import { payrollApi } from "../api/endpoints";
import { Spinner } from "./ui";
import { useModalLayer } from "./Modal";   // Esc closes (top-most only), scroll lock
import { useAuthStore } from "../store/authStore";

/**
 * Pay stub viewer — renders the server-generated PDF in an in-app viewer
 * (iframe), with Download + Print. The lightweight JSON detail is fetched only
 * for the header line (employee name / pay date).
 */
export default function PayslipModal({ stubId, onClose }: { stubId: string; onClose: () => void }) {
  useModalLayer(onClose);
  const org = useAuthStore((s) => s.user?.org_name);
  const { data } = useQuery({ queryKey: ["payslip", stubId], queryFn: () => payrollApi.payslipDetail(stubId) });
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    let objUrl: string | null = null;
    let cancelled = false;
    setErr(null); setUrl(null);
    (async () => {
      try {
        const blob = await payrollApi.payslipPdf(stubId, org || undefined);
        if (cancelled) return;
        objUrl = URL.createObjectURL(blob);
        setUrl(objUrl);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || "Could not load the pay stub PDF");
      }
    })();
    return () => { cancelled = true; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [stubId, org]);

  const download = () => {
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = `pay-stub-${data?.employee?.employee_number || stubId}.pdf`;
    a.click();
  };
  // Print the same PDF (the browser viewer handles it).
  const print = () => {
    try { iframeRef.current?.contentWindow?.focus(); iframeRef.current?.contentWindow?.print(); }
    catch { if (url) window.open(url, "_blank", "noopener"); }
  };

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div role="dialog" aria-modal="true" aria-label="Pay stub" className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(70rem,96vw)] h-[92vh] overflow-hidden flex flex-col">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary-100 flex items-center justify-center"><FileText className="w-5 h-5 text-primary-600" /></div>
            <div>
              <h2 className="font-display font-semibold text-gray-800">Pay stub</h2>
              <p className="text-[11px] text-gray-500">{data?.employee?.name || "…"} · pay date {data?.period?.pay_date || "—"}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={download} disabled={!url} className="icon-btn text-gray-400 disabled:opacity-40" title="Download PDF"><Download className="w-4 h-4" /></button>
            <button onClick={print} disabled={!url} className="btn-secondary text-xs inline-flex items-center gap-1 disabled:opacity-40" title="Print pay stub" data-testid="payslip-print"><Printer className="w-3.5 h-3.5" /> Print</button>
            <button onClick={onClose} className="icon-btn text-gray-400" title="Close"><X className="w-4 h-4" /></button>
          </div>
        </div>

        <div className="flex-1 bg-gray-100 min-h-0">
          {err ? (
            <div className="grid place-items-center h-full text-sm text-rose-600 px-6 text-center">{err}</div>
          ) : !url ? (
            <div className="grid place-items-center h-full"><Spinner className="h-8 w-8" /></div>
          ) : (
            <iframe ref={iframeRef} src={url} title="Pay stub PDF" className="w-full h-full border-0" />
          )}
        </div>
      </div>
    </div>
  );
}
