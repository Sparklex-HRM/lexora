import { useState } from "react";
import { Loader2, Printer } from "lucide-react";
import toast from "react-hot-toast";
import { printPdfBlob } from "../lib/print";

/**
 * "Print" next to every "Download PDF" (2026-10-24 go-live wrap-up).
 * Fetches the SAME server PDF the download uses and sends it to the printer
 * (lib/print.printPdfBlob — hidden iframe, new-tab fallback). Password-protected
 * PDFs (payslip, Form 130, tax statements) are unlocked in the browser's PDF
 * viewer, so keep the password hint visible next to this button.
 */
export async function errorText(e: any, fallback: string): Promise<string> {
  const data = e?.response?.data;
  if (data instanceof Blob) {
    try {
      const j = JSON.parse(await data.text());
      const d = j?.detail;
      return typeof d === "string" ? d : (d?.message || fallback);
    } catch { return fallback; }
  }
  const d = data?.detail;
  return typeof d === "string" ? d : (d?.message || fallback);
}

export default function PrintPdfButton({
  fetchPdf, name = "document.pdf", label = "Print", className = "btn-secondary text-xs inline-flex items-center gap-1",
  disabled, title = "Print this PDF (A4)", testId,
}: {
  fetchPdf: () => Promise<Blob>;
  name?: string;
  label?: string;
  className?: string;
  disabled?: boolean;
  title?: string;
  testId?: string;
}) {
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      printPdfBlob(await fetchPdf(), name);
    } catch (e: any) {
      toast.error(await errorText(e, "Could not open the PDF for printing"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <button type="button" className={`${className} lx-no-print`} onClick={go} disabled={disabled || busy} title={title} data-testid={testId}>
      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Printer className="w-3.5 h-3.5" />} {label}
    </button>
  );
}
