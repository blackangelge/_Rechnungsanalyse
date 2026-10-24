"use client";

import { useEffect, useState } from "react";
import { OrphanName, Vendor, vendorsApi, extractApiError } from "@/lib/api";

const CONF: Record<string, { label: string; cls: string }> = {
  high: { label: "sicher", cls: "bg-green-100 text-green-700" },
  medium: { label: "wahrscheinlich", cls: "bg-amber-100 text-amber-800" },
  low: { label: "unsicher", cls: "bg-red-100 text-red-700" },
};

/**
 * Belege, deren Lieferantenname zu keinem Lieferanten gehört (z. B. nach früherem Zusammenführen).
 * Es wird nichts automatisch geändert: Vorschläge sind vorausgewählt, wenn „sicher"; Umschreiben erst nach Klick.
 */
export default function OrphanModal({
  vendors,
  onClose,
  onDone,
}: {
  vendors: Vendor[];
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const [data, setData] = useState<{ total: number; orphans: OrphanName[] } | null>(null);
  const [target, setTarget] = useState<Record<string, number | "">>({});
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    vendorsApi.orphanNames()
      .then((d) => {
        setData(d);
        const t: Record<string, number | ""> = {};
        const c = new Set<string>();
        for (const o of d.orphans) {
          t[o.name] = o.suggestion?.vendor_id ?? "";
          if (o.suggestion?.confidence === "high" && !o.suggestion.ambiguous) c.add(o.name);
        }
        setTarget(t);
        setChecked(c);
      })
      .catch((e) => setError(extractApiError(e, "Suche fehlgeschlagen")));
  }, []);

  const chosen = (data?.orphans ?? []).filter((o) => checked.has(o.name) && target[o.name] !== "");
  const docs = chosen.reduce((n, o) => n + o.document_count, 0);

  async function apply() {
    if (!confirm(`${docs.toLocaleString("de-DE")} Beleg(e) von ${chosen.length} Name(n) den gewählten Lieferanten zuordnen?\n\nDie Änderung steht im Änderungsprotokoll (Einstellungen → Automatisierung) und lässt sich dort rückgängig machen.`)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await vendorsApi.reassignNames(chosen.map((o) => ({ name: o.name, target_id: target[o.name] as number })));
      onDone(`${r.moved_documents.toLocaleString("de-DE")} Beleg(e) zugeordnet.`);
    } catch (e) {
      setError(extractApiError(e, "Zuordnung fehlgeschlagen"));
      setBusy(false);
    }
  }

  const sorted = [...vendors].sort((a, b) => a.name.localeCompare(b.name, "de"));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="orphan-modal">
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-lg bg-white shadow-xl">
        <div className="border-b px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">Verwaiste Belege zuordnen</h2>
          <p className="mt-1 text-sm text-gray-500">
            Diese Lieferantennamen stehen auf Belegen, gehören aber zu keinem Lieferanten (mehr) — z. B. nach einem früheren
            Zusammenführen. Wähle je Name den richtigen Lieferanten; die Belege erhalten dann dessen Namen.
          </p>
        </div>
        <div className="flex-1 overflow-auto px-6 py-4">
          {error && <div className="mb-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
          {!data && !error && <p className="text-sm text-gray-500">Suche läuft…</p>}
          {data && data.orphans.length === 0 && <p className="text-sm text-green-700">Keine verwaisten Belege gefunden.</p>}
          {data && data.orphans.length > 0 && (
            <>
              {data.total > data.orphans.length && (
                <p className="mb-2 text-xs text-gray-500">Es werden die {data.orphans.length} häufigsten von {data.total} Namen angezeigt; nach dem Zuordnen erneut prüfen.</p>
              )}
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-gray-500">
                    <th className="w-8 py-2" />
                    <th className="py-2">Name auf den Belegen</th>
                    <th className="py-2 text-right">Belege</th>
                    <th className="py-2 pl-4">Zuordnen zu</th>
                    <th className="py-2 pl-2">Vorschlag</th>
                  </tr>
                </thead>
                <tbody>
                  {data.orphans.map((o) => (
                    <tr key={o.name} className="border-b align-top">
                      <td className="py-2">
                        <input type="checkbox" checked={checked.has(o.name)} disabled={target[o.name] === ""}
                          onChange={(e) => setChecked((s) => { const n = new Set(s); e.target.checked ? n.add(o.name) : n.delete(o.name); return n; })} />
                      </td>
                      <td className="py-2 font-medium text-gray-800">{o.name}</td>
                      <td className="py-2 text-right text-gray-600">{o.document_count.toLocaleString("de-DE")}</td>
                      <td className="py-2 pl-4">
                        <select className="w-64 rounded border border-gray-300 px-2 py-1 text-sm" value={target[o.name] ?? ""}
                          onChange={(e) => setTarget((t) => ({ ...t, [o.name]: e.target.value === "" ? "" : Number(e.target.value) }))}>
                          <option value="">— nicht zuordnen —</option>
                          {sorted.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                        </select>
                      </td>
                      <td className="py-2 pl-2 text-xs">
                        {o.suggestion ? (
                          <span className={`rounded px-1.5 py-0.5 ${CONF[o.suggestion.confidence]?.cls}`}>
                            {CONF[o.suggestion.confidence]?.label}: {o.suggestion.reason}{o.suggestion.ambiguous ? " (mehrdeutig)" : ""}
                          </span>
                        ) : <span className="text-gray-400">kein Vorschlag</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
        <div className="flex items-center justify-between border-t px-6 py-3">
          <span className="text-sm text-gray-600">{chosen.length} Name(n) · {docs.toLocaleString("de-DE")} Beleg(e) gewählt</span>
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Schließen</button>
            <button onClick={apply} disabled={busy || chosen.length === 0} data-testid="orphan-apply"
              className="btn btn-primary">
              Ausgewählte zuordnen
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
