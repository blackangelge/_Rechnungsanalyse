"use client";

import { useMemo, useState } from "react";
import { Vendor } from "@/lib/api";

type Status = "same" | "diff" | "partial" | "empty";

interface Row {
  label: string;
  values: string[][];          // je Lieferant: Liste der Werte (Bankverbindungen können mehrere sein)
  status: Status;
}

const filled = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";

/** Schreibweise-unabhängiger Vergleichsschlüssel (IBAN mit Leerzeichen, Rechtsform, Groß/Klein …) */
const key = (v: string, strict: boolean) =>
  strict ? v.trim().replace(/\s+/g, " ").toLowerCase() : v.replace(/[^A-Za-z0-9äöüÄÖÜß]/g, "").toLowerCase();

const STATUS_UI: Record<Status, { label: string; cls: string }> = {
  same: { label: "identisch", cls: "bg-green-100 text-green-700" },
  diff: { label: "abweichend", cls: "bg-red-100 text-red-700" },
  partial: { label: "nur teilweise", cls: "bg-amber-100 text-amber-800" },
  empty: { label: "überall leer", cls: "bg-gray-100 text-gray-500" },
};

function rowStatus(values: string[][], strict: boolean): Status {
  const sets = values.map((vs) => new Set(vs.map((v) => key(v, strict))));
  const nonEmpty = sets.filter((s) => s.size > 0);
  if (nonEmpty.length === 0) return "empty";
  const union = new Set(nonEmpty.flatMap((s) => Array.from(s)));
  const allEqual = nonEmpty.every((s) => s.size === union.size);
  if (!allEqual) return "diff";
  return nonEmpty.length < sets.length ? "partial" : "same";
}

export default function CompareModal({
  suppliers,
  onClose,
  onMerge,
}: {
  suppliers: Vendor[];
  onClose: () => void;
  onMerge?: (suppliers: Vendor[]) => void;
}) {
  const [onlyDiff, setOnlyDiff] = useState(false);

  const rows: Row[] = useMemo(() => {
    const single = (label: string, pick: (s: Vendor) => string | null | undefined, strict = false): Row => {
      const values = suppliers.map((s) => (filled(pick(s)) ? [pick(s)!.trim()] : []));
      return { label, values, status: rowStatus(values, strict) };
    };
    const iban = suppliers.map((s) => s.bank_accounts.map((b) => b.iban).filter(filled).map((v) => v.trim()));
    const bank = suppliers.map((s) => s.bank_accounts.map((b) => b.bank_name).filter(filled).map((v) => v.trim()));
    const bic = suppliers.map((s) => s.bank_accounts.map((b) => b.bic).filter(filled).map((v) => v.trim()));
    return [
      single("Name", (s) => s.name),
      single("Straße", (s) => s.street),
      single("PLZ", (s) => s.postal_code),
      single("Stadt", (s) => s.city),
      single("Land", (s) => s.country),
      single("HRB-Nr.", (s) => s.hrb_number),
      single("Steuernr.", (s) => s.tax_number),
      single("USt-IdNr.", (s) => s.vat_id),
      { label: "IBAN", values: iban, status: rowStatus(iban, false) },
      { label: "Bank", values: bank, status: rowStatus(bank, false) },
      { label: "BIC", values: bic, status: rowStatus(bic, false) },
    ];
  }, [suppliers]);

  const count = (st: Status) => rows.filter((r) => r.status === st).length;
  const visible = onlyDiff ? rows.filter((r) => r.status === "diff" || r.status === "partial") : rows;

  // Zellen mit einem Wert, den kein anderer Lieferant (in Schreibweise-unabhängigem Vergleich) hat
  const isOdd = (row: Row, i: number) =>
    row.status === "diff" &&
    row.values[i].some((v) => !row.values.some((o, j) => j !== i && o.some((w) => key(w, false) === key(v, false))));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="compare-modal">
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Lieferanten vergleichen</h2>
            <p className="mt-1 text-xs text-gray-600">
              <span className="font-medium text-green-700">{count("same")} identisch</span> ·{" "}
              <span className="font-medium text-red-700">{count("diff")} abweichend</span> ·{" "}
              <span className="font-medium text-amber-700">{count("partial")} nur teilweise ausgefüllt</span> ·{" "}
              <span className="text-gray-500">{count("empty")} überall leer</span>
              <span className="ml-2 text-gray-400">(Schreibweise wie Groß/Klein oder Leerzeichen zählt nicht als Unterschied)</span>
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Schließen">✕</button>
        </div>

        <div className="overflow-auto px-6 py-4">
          <label className="mb-3 flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} data-testid="only-diff" />
            Nur Unterschiede anzeigen
          </label>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs text-gray-600">
                <th className="px-3 py-2">Feld</th>
                {suppliers.map((s) => (
                  <th key={s.id} className="px-3 py-2">
                    #{s.id} <span className="font-normal text-gray-400">· {s.document_count ?? 0} Beleg(e)</span>
                  </th>
                ))}
                <th className="px-3 py-2">Ergebnis</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.label} className="border-t align-top" data-testid="compare-row" data-status={r.status}>
                  <td className="px-3 py-2 font-medium text-gray-700">{r.label}</td>
                  {r.values.map((vs, i) => (
                    <td
                      key={suppliers[i].id}
                      className={`px-3 py-2 ${
                        r.status === "same" ? "bg-green-50/60"
                          : isOdd(r, i) ? "bg-red-50"
                          : vs.length === 0 && r.status !== "empty" ? "bg-amber-50/70" : ""
                      }`}
                    >
                      {vs.length === 0 ? <span className="text-gray-300">—</span> : vs.map((v) => <div key={v} className="break-all">{v}</div>)}
                    </td>
                  ))}
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_UI[r.status].cls}`}>
                      {STATUS_UI[r.status].label}
                    </span>
                  </td>
                </tr>
              ))}
              {visible.length === 0 && (
                <tr><td colSpan={suppliers.length + 2} className="px-3 py-6 text-center text-gray-400">Keine Unterschiede 🎉</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex justify-end gap-2 border-t px-6 py-3">
          <button onClick={onClose} className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
            Schließen
          </button>
          {onMerge && (
            <button
              onClick={() => onMerge(suppliers)}
              className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              Zusammenführen…
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
