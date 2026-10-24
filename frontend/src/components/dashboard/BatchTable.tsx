/**
 * Tabelle aller Import-Batches für das Dashboard.
 *
 * Zeigt je Batch:
 * - Firma, Jahr, Status, Dokumente-Anzahl, Erstellungsdatum
 * - Link zur Detailseite
 * - Fortschrittsbalken für laufende Imports
 */

"use client";

import Link from "next/link";
import { useState } from "react";
import { ImportBatch, importsApi } from "@/lib/api";

interface Props {
  batches: ImportBatch[];
  onDeleted: (id: number) => void;
  /** Mehrfachauswahl (z.B. für gemeinsamen Excel-Export oder Ausblenden) */
  selectedIds: Set<number>;
  onToggleSelect: (id: number) => void;
  onToggleSelectAll: () => void;
  /** Einzelnen Import aus-/einblenden */
  onSetHidden: (id: number, hidden: boolean) => void;
}

/** Status-Badge Styles */
const STATUS_BADGES: Record<string, { cls: string; label: string }> = {
  pending: { cls: "badge-gray", label: "Ausstehend" },
  running: { cls: "badge-blue", label: "Läuft" },
  done: { cls: "badge-green", label: "Fertig" },
  error: { cls: "badge-red", label: "Fehler" },
};

export default function BatchTable({
  batches, onDeleted, selectedIds, onToggleSelect, onToggleSelectAll, onSetHidden,
}: Props) {
  const allSelected = batches.length > 0 && batches.every((b) => selectedIds.has(b.id));
  const someSelected = !allSelected && batches.some((b) => selectedIds.has(b.id));
  const [deletingId, setDeletingId] = useState<number | null>(null);

  async function handleDelete(id: number) {
    if (!confirm("Import-Batch und alle zugehörigen Dokumente löschen?")) return;
    setDeletingId(id);
    try {
      await importsApi.delete(id);
      onDeleted(id);
    } finally {
      setDeletingId(null);
    }
  }

  if (batches.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-10 text-center">
        <p className="text-sm font-medium text-gray-800">Noch keine Imports</p>
        <p className="mt-1 text-sm text-gray-500">Starte einen neuen Import über „Neuer Import“.</p>
      </div>
    );
  }

  return (
    <div className="card overflow-x-auto">
      <table className="table-ra">
        <thead>
          <tr>
            <th className="w-10 px-3 py-3">
              <input
                type="checkbox" checked={allSelected} aria-label="Alle Imports auswählen"
                ref={(el) => { if (el) el.indeterminate = someSelected; }}
                onChange={onToggleSelectAll}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </th>
            <th className="px-4 py-3">ID</th>
            <th className="px-4 py-3">Firma</th>
            <th className="px-4 py-3">Jahr</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Fortschritt</th>
            <th className="px-4 py-3">Kommentar</th>
            <th className="px-4 py-3">Erstellt am</th>
            <th className="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody>
          {batches.map((batch) => {
            // Fortschritt — nur statusbasiert (keine Dokumentenzähler im Batch-Objekt)
            const isDone    = batch.status === "done";
            const isRunning = batch.status === "running";
            const isError   = batch.status === "error";
            const percent   = isDone ? 100 : 0;

            return (
              <tr
                key={batch.id}
                data-testid={`batch-row-${batch.id}`}
                className={[
                  selectedIds.has(batch.id) ? "bg-blue-50" : "hover:bg-gray-50",
                  batch.hidden_at ? "opacity-60" : "",
                ].join(" ")}
              >
                {/* Auswahl */}
                <td className="px-3 py-3">
                  <input
                    type="checkbox" checked={selectedIds.has(batch.id)}
                    onChange={() => onToggleSelect(batch.id)}
                    aria-label={`Import ${batch.company_name} ${batch.year} auswählen`}
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                </td>

                {/* ID */}
                <td className="px-4 py-3 font-mono text-xs text-gray-400">
                  #{batch.id}
                </td>

                {/* Firma */}
                <td className="px-4 py-3 font-medium text-gray-800">
                  {batch.company_name}
                  {batch.hidden_at && (
                    <span className="ml-2 rounded-full bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-600">
                      ausgeblendet
                    </span>
                  )}
                </td>

                {/* Jahr */}
                <td className="px-4 py-3 text-gray-600">{batch.year}</td>

                {/* Status */}
                <td className="px-4 py-3">
                  <span className={`badge ${(STATUS_BADGES[batch.status] ?? STATUS_BADGES.pending).cls}`}>
                    {(STATUS_BADGES[batch.status] ?? { label: batch.status }).label}
                  </span>
                </td>

                {/* Fortschrittsbalken */}
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-24 overflow-hidden rounded-full bg-gray-100">
                      {isRunning ? (
                        <div className="h-2 w-1/2 rounded-full bg-blue-400 animate-pulse" />
                      ) : (
                        <div
                          className={[
                            "h-2 rounded-full transition-all",
                            isDone ? "bg-green-500" : isError ? "bg-red-400" : "bg-gray-300",
                          ].join(" ")}
                          style={{ width: `${percent}%` }}
                        />
                      )}
                    </div>
                    <span className="text-xs text-gray-400">
                      {isRunning ? "…" : `${percent}%`}
                    </span>
                  </div>
                </td>

                {/* Kommentar */}
                <td className="px-4 py-3 text-xs text-gray-400">
                  {batch.comment || "—"}
                </td>

                {/* Erstellungsdatum */}
                <td className="px-4 py-3 text-xs text-gray-400">
                  {new Date(batch.created_at).toLocaleString("de-DE")}
                </td>

                {/* Aktionen */}
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/imports/${batch.id}`}
                      className="rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50"
                    >
                      Details →
                    </Link>
                    <Link
                      href={`/export-vorlagen?batch_ids=${batch.id}`}
                      title="Mit einer Export-Vorlage als Excel exportieren"
                      className="rounded px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50"
                    >
                      Export
                    </Link>
                    <button
                      onClick={() => onSetHidden(batch.id, !batch.hidden_at)}
                      title={batch.hidden_at ? "Wieder einblenden" : "Ausblenden (nichts wird gelöscht)"}
                      className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
                    >
                      {batch.hidden_at ? "Einblenden" : "Ausblenden"}
                    </button>
                    <button
                      onClick={() => handleDelete(batch.id)}
                      disabled={deletingId === batch.id}
                      className="rounded px-2 py-1 text-xs text-red-500 hover:bg-red-50 disabled:opacity-40"
                    >
                      {deletingId === batch.id ? "…" : "Löschen"}
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
