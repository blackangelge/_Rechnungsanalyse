"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AnalysisHistoryDetail,
  AnalysisHistoryItem,
  DocumentDetail,
  analysisHistoryApi,
  documentsApi,
  extractApiError,
} from "@/lib/api";

const FIELDS: { key: string; label: string; money?: boolean }[] = [
  { key: "vendor_id", label: "Lieferant" },
  { key: "invoice_number", label: "Rechnungsnr." },
  { key: "invoice_date", label: "Rechnungsdatum" },
  { key: "due_date", label: "Fälligkeit" },
  { key: "total_amount_netto", label: "Netto", money: true },
  { key: "total_tax_value", label: "Steuer", money: true },
  { key: "total_amount_brutto", label: "Brutto", money: true },
  { key: "discount_amount", label: "Rabatt", money: true },
  { key: "cash_discount_amount", label: "Skonto", money: true },
  { key: "payment_terms", label: "Zahlungsbedingungen" },
];

const STATUS_LABEL: Record<string, string> = {
  ok: "OK", corrected: "Korrigiert", ai_corrected: "KI-korrigiert",
  needs_review: "Prüfen!", manual_ok: "Geprüft", unchecked: "Ungeprüft",
};

const num = (v: unknown): number | null => {
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isNaN(n) ? null : n;
};

function show(v: unknown, money?: boolean): string {
  if (v == null || v === "") return "–";
  if (money) {
    const n = num(v);
    if (n != null) return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(n);
  }
  return String(v);
}

const same = (a: unknown, b: unknown, money?: boolean) =>
  money ? num(a) === num(b) : String(a ?? "") === String(b ?? "");

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });

/**
 * Frühere KI-Auslesungen eines Belegs: ansehen, mit dem aktuellen Stand vergleichen,
 * wiederherstellen. Die Stände entstehen automatisch beim erneuten Analysieren.
 */
export default function AnalysisHistoryPanel({
  doc,
  onChange,
}: {
  doc: DocumentDetail;
  onChange: (doc: DocumentDetail) => void;
}) {
  const [items, setItems] = useState<AnalysisHistoryItem[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AnalysisHistoryDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await analysisHistoryApi.list(doc.id));
    } catch {
      setItems([]);
    }
  }, [doc.id]);

  useEffect(() => {
    setOpenId(null);
    setDetail(null);
    setErr(null);
    load();
  }, [load, doc.extraction?.updated_at]);

  if (items.length === 0) return null;

  async function open(id: number) {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    setErr(null);
    try {
      setDetail(await analysisHistoryApi.get(doc.id, id));
    } catch (e) {
      setErr(extractApiError(e, "Stand konnte nicht geladen werden"));
    }
  }

  async function restore(id: number) {
    if (!confirm("Diesen Stand wiederherstellen? Der aktuelle Stand bleibt in der Historie erhalten.")) return;
    setBusy(true);
    setErr(null);
    try {
      await analysisHistoryApi.restore(doc.id, id);
      onChange(await documentsApi.get(doc.id));
      await load();
    } catch (e) {
      setErr(extractApiError(e, "Wiederherstellen fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!confirm("Diesen früheren Stand endgültig löschen?")) return;
    setBusy(true);
    try {
      await analysisHistoryApi.remove(doc.id, id);
      if (openId === id) {
        setOpenId(null);
        setDetail(null);
      }
      await load();
    } catch (e) {
      setErr(extractApiError(e, "Löschen fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  }

  const curSum = doc.order_positions.reduce((a, p) => a + (num(p.total_price) ?? 0), 0);

  return (
    <details className="mb-6 rounded-lg border border-gray-200 bg-white" data-testid="analysis-history">
      <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-gray-800">
        Frühere Analysen ({items.length})
        <span className="ml-2 text-xs font-normal text-gray-500">
          bisherige Stände vergleichen und wiederherstellen
        </span>
      </summary>
      <div className="border-t border-gray-100 p-4">
        {err && <div className="mb-3 rounded-lg border border-red-200 bg-red-50 p-2 text-sm text-red-700">{err}</div>}
        <ul className="space-y-2">
          {items.map((h) => (
            <li key={h.id} className="rounded border border-gray-200" data-testid="history-entry">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
                <span className="font-medium text-gray-800">{fmtDate(h.created_at)}</span>
                {h.workflow_name && <span className="text-gray-600">Workflow: {h.workflow_name}</span>}
                <span className="text-gray-600">Rechnungsnr. {h.invoice_number ?? "–"}</span>
                <span className="text-gray-600">{show(h.total_amount, true)}</span>
                <span className="text-gray-600">{h.position_count} Pos.</span>
                {h.validation_status && (
                  <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700">
                    {STATUS_LABEL[h.validation_status] ?? h.validation_status}
                  </span>
                )}
                {h.pdf_version != null && <span className="text-xs text-gray-400">PDF v{h.pdf_version}</span>}
                <span className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => open(h.id)}
                    className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
                  >
                    {openId === h.id ? "Schließen" : "Vergleichen"}
                  </button>
                  <button
                    type="button"
                    disabled={busy || doc.status === "processing"}
                    onClick={() => restore(h.id)}
                    className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    Wiederherstellen
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => remove(h.id)}
                    className="rounded border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                  >
                    Löschen
                  </button>
                </span>
              </div>

              {openId === h.id && (
                <div className="border-t border-gray-100 px-3 py-3 text-sm">
                  {!detail ? (
                    <div className="text-gray-400">Lade…</div>
                  ) : (
                    <>
                      <table className="mb-3 w-full text-left text-xs" data-testid="history-compare">
                        <thead>
                          <tr className="text-gray-500">
                            <th className="py-1 pr-3 font-medium">Feld</th>
                            <th className="py-1 pr-3 font-medium">Früherer Stand</th>
                            <th className="py-1 font-medium">Aktuell</th>
                          </tr>
                        </thead>
                        <tbody>
                          {FIELDS.map((f) => {
                            const old = (detail.extraction as Record<string, unknown>)[f.key];
                            const cur = (doc.extraction as unknown as Record<string, unknown> | null)?.[f.key];
                            const diff = !same(old, cur, f.money);
                            return (
                              <tr key={f.key} className={diff ? "bg-amber-50" : ""}>
                                <td className="py-0.5 pr-3 text-gray-500">{f.label}</td>
                                <td className="py-0.5 pr-3 text-gray-800">{show(old, f.money)}</td>
                                <td className={`py-0.5 ${diff ? "font-medium text-amber-900" : "text-gray-800"}`}>
                                  {show(cur, f.money)}
                                </td>
                              </tr>
                            );
                          })}
                          <tr className={detail.order_positions.length !== doc.order_positions.length ? "bg-amber-50" : ""}>
                            <td className="py-0.5 pr-3 text-gray-500">Positionen</td>
                            <td className="py-0.5 pr-3 text-gray-800">{detail.order_positions.length}</td>
                            <td className="py-0.5 text-gray-800">{doc.order_positions.length}</td>
                          </tr>
                          <tr
                            className={
                              Math.abs(detail.order_positions.reduce((a, p) => a + (num(p.total_price) ?? 0), 0) - curSum) > 0.004
                                ? "bg-amber-50" : ""
                            }
                          >
                            <td className="py-0.5 pr-3 text-gray-500">Summe Positionen</td>
                            <td className="py-0.5 pr-3 text-gray-800">
                              {show(detail.order_positions.reduce((a, p) => a + (num(p.total_price) ?? 0), 0), true)}
                            </td>
                            <td className="py-0.5 text-gray-800">{show(curSum, true)}</td>
                          </tr>
                          <tr className={same(detail.used_system_prompt, doc.used_system_prompt) ? "" : "bg-amber-50"}>
                            <td className="py-0.5 pr-3 text-gray-500">Systemprompt</td>
                            <td className="py-0.5 pr-3 text-gray-800">
                              {same(detail.used_system_prompt, doc.used_system_prompt) ? "gleich" : "unterschiedlich"}
                            </td>
                            <td />
                          </tr>
                        </tbody>
                      </table>
                      {detail.used_system_prompt && (
                        <details className="text-xs text-gray-600">
                          <summary className="cursor-pointer">Systemprompt dieses Stands</summary>
                          <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2">
                            {detail.used_system_prompt}
                          </pre>
                        </details>
                      )}
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
