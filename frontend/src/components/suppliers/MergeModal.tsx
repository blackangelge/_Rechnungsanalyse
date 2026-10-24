"use client";

import { useMemo, useState } from "react";
import { Vendor, VendorMergeResult, extractApiError, vendorsApi } from "@/lib/api";

/** Stammdatenfelder, bei denen abweichende Werte gewählt werden können */
type TextField = "name" | "street" | "postal_code" | "city" | "country" | "hrb_number" | "tax_number" | "vat_id";
const FIELDS: { key: TextField; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "street", label: "Straße" },
  { key: "postal_code", label: "PLZ" },
  { key: "city", label: "Stadt" },
  { key: "country", label: "Land" },
  { key: "hrb_number", label: "HRB-Nr." },
  { key: "tax_number", label: "Steuernr." },
  { key: "vat_id", label: "USt-IdNr." },
];

const filled = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";

/** Vergleichsschlüssel: gleiche Angabe in anderer Schreibweise (IBAN mit Leerzeichen, Groß/Klein) ist kein Konflikt */
function sameKey(field: TextField, value: string): string {
  if (field === "vat_id" || field === "tax_number") {
    return value.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  }
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

/** Vorschlag für den Ziel-Lieferanten: meiste Belege, dann meiste gefüllte Felder, dann kleinste ID */
function suggestTarget(suppliers: Vendor[]): number {
  const score = (s: Vendor) => FIELDS.filter((f) => filled(s[f.key])).length;
  return [...suppliers].sort(
    (a, b) => (b.document_count ?? 0) - (a.document_count ?? 0) || score(b) - score(a) || a.id - b.id,
  )[0].id;
}

export default function MergeModal({
  suppliers,
  onClose,
  onMerged,
}: {
  suppliers: Vendor[];
  onClose: () => void;
  onMerged: (result: VendorMergeResult) => void;
}) {
  const [targetId, setTargetId] = useState<number>(() => suggestTarget(suppliers));
  /** Gewählter Wert je Feld mit Konflikt (key → Wert); fehlt der Key: Standard */
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [updateNames, setUpdateNames] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const target = suppliers.find((s) => s.id === targetId)!;
  const sources = suppliers.filter((s) => s.id !== targetId);
  const docsToMove = sources.reduce((n, s) => n + (s.document_count ?? 0), 0);

  /** Felder mit mindestens zwei verschiedenen Werten → hier entscheidet der Nutzer */
  const conflicts = useMemo(
    () =>
      FIELDS.map((f) => {
        // je Schreibweise-Gruppe nur ein Vertreter; der Wert des Ziels hat Vorrang
        const byKey = new Map<string, string>();
        const ordered = [target, ...suppliers.filter((s) => s.id !== target.id)];
        ordered.map((s) => s[f.key]).filter(filled).forEach((v) => {
          const k = sameKey(f.key, v);
          if (!byKey.has(k)) byKey.set(k, v.trim());
        });
        return { ...f, values: Array.from(byKey.values()) };
      }).filter((c) => c.values.length > 1),
    [suppliers, target],
  );

  /** Standardwert eines Konfliktfelds: Wert des Ziels, sonst der erste vorhandene */
  const defaultFor = (key: TextField, values: string[]) =>
    filled(target[key]) ? (target[key] as string).trim() : values[0];
  const valueFor = (c: (typeof conflicts)[number]) => choices[c.key] ?? defaultFor(c.key, c.values);
  const finalName = conflicts.find((c) => c.key === "name") ? valueFor(conflicts.find((c) => c.key === "name")!) : (
    filled(target.name) ? target.name : suppliers.find((s) => filled(s.name))?.name ?? ""
  );

  async function submit() {
    if (!confirm(
      `${sources.length} Lieferant(en) in „${finalName}" zusammenführen?\n\n` +
      `${updateNames ? `${docsToMove} Beleg(e) werden dem Ziel zugeordnet, ` : ""}die übrigen Lieferanten werden gelöscht. Das kann nicht rückgängig gemacht werden.`,
    )) return;
    setBusy(true);
    setError(null);
    try {
      const fields: Record<string, string | null> = {};
      conflicts.forEach((c) => { fields[c.key] = valueFor(c); });
      const result = await vendorsApi.merge({
        target_id: targetId,
        source_ids: sources.map((s) => s.id),
        fields: Object.keys(fields).length ? fields : undefined,
        update_document_names: updateNames,
      });
      onMerged(result);
    } catch (err) {
      setError(extractApiError(err, "Zusammenführen fehlgeschlagen"));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={busy ? undefined : onClose}>
      <div className="flex max-h-[92vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}
        data-testid="merge-modal">
        <div className="flex items-center justify-between border-b px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Lieferanten zusammenführen</h2>
            <p className="text-sm text-gray-500">Wähle den Lieferanten, der bestehen bleibt. Daten und Belege der anderen werden ihm zugeordnet.</p>
          </div>
          <button onClick={onClose} disabled={busy} className="text-xl leading-none text-gray-400 hover:text-gray-700">✕</button>
        </div>

        <div className="space-y-5 overflow-y-auto px-6 py-5">
          {/* Ziel wählen */}
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Behalten</h3>
            <div className="space-y-2">
              {suppliers.map((s) => (
                <label key={s.id}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${s.id === targetId ? "border-blue-400 bg-blue-50" : "border-gray-200 hover:bg-gray-50"}`}>
                  <input type="radio" name="merge-target" checked={s.id === targetId}
                    onChange={() => { setTargetId(s.id); setChoices({}); }} className="mt-1" />
                  <span className="min-w-0 flex-1 text-sm">
                    <span className="font-medium text-gray-900">#{s.id} {s.name}</span>
                    <span className="ml-2 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">{s.document_count ?? 0} Beleg(e)</span>
                    {s.id === targetId && <span className="ml-2 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">bleibt bestehen</span>}
                    <span className="block text-xs text-gray-500">
                      {[s.street, [s.postal_code, s.city].filter(Boolean).join(" ")].filter(Boolean).join(", ") || "keine Anschrift"}
                      {s.bank_accounts.length > 0 ? ` · ${s.bank_accounts.map((b) => b.iban).filter(Boolean).join(", ")}` : ""}{s.vat_id ? ` · ${s.vat_id}` : ""}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </div>

          {/* Konflikte */}
          {conflicts.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Abweichende Angaben — Endwert wählen</h3>
              <p className="mb-2 text-xs text-gray-500">Leere Felder des bleibenden Lieferanten werden automatisch aus den anderen aufgefüllt.</p>
              <div className="overflow-hidden rounded-lg border border-gray-200">
                {conflicts.map((c) => (
                  <div key={c.key} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-gray-100 px-3 py-2 last:border-0">
                    <span className="w-36 shrink-0 text-xs font-medium text-gray-600">{c.label}</span>
                    <div className="flex flex-1 flex-wrap gap-x-4 gap-y-1">
                      {c.values.map((v) => (
                        <label key={v} className="flex items-center gap-1.5 text-sm text-gray-800">
                          <input type="radio" name={`field-${c.key}`} checked={valueFor(c) === v}
                            onChange={() => setChoices((prev) => ({ ...prev, [c.key]: v }))} />
                          {v}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={updateNames} onChange={(e) => setUpdateNames(e.target.checked)} className="mt-0.5" />
            <span>
              Lieferantenname in den zugehörigen Belegen auf „{finalName}" vereinheitlichen
              <span className="block text-xs text-gray-500">
                Belege sind einem Lieferanten über dessen Namen zugeordnet. Ohne diese Option behalten sie ihren bisherigen Namen und gehören danach zu keinem Lieferanten mehr (Belegzähler, Workflow). Der Originalwortlaut bleibt in der KI-Rohantwort erhalten.
              </span>
            </span>
          </label>

          <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
            <b>{sources.length}</b> Lieferant(en) werden gelöscht, <b>{updateNames ? docsToMove : 0}</b> Beleg(e) dem Lieferanten
            „<b>{finalName}</b>" zugeordnet. Bankverbindungen der anderen werden übernommen. Das kann nicht rückgängig gemacht werden.
          </div>
          {error && <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 border-t px-6 py-4">
          <button onClick={onClose} disabled={busy} className="btn btn-secondary btn-sm">Abbrechen</button>
          <button onClick={submit} disabled={busy}
            className="rounded bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {busy ? "Führe zusammen…" : "Zusammenführen"}
          </button>
        </div>
      </div>
    </div>
  );
}
