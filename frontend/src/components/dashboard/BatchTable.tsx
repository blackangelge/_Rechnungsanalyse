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
}

/** Status-Badge Styles */
const STATUS_STYLES: Record<string, string> = {
  pending: "bg-gray-100 text-gray-500",
  running: "bg-blue-100 text-blue-700",
  done: "bg-green-100 text-green-700",
  error: "bg-red-100 text-red-700",
};

export default function BatchTable({ batches, onDeleted }: Props) {
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
      <div className="rounded-lg border bg-white p-8 text-center text-sm text-gray-400 shadow-sm">
        Keine Imports gefunden. Starte einen neuen Import.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead className="border-b bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
          <tr>
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
        <tbody className="divide-y">
          {batches.map((batch) => {
            // Fortschritt — nur statusbasiert (keine Dokumentenzähler im Batch-Objekt)
            const isDone    = batch.status === "done";
            const isRunning = batch.status === "running";
            const isError   = batch.status === "error";
            const percent   = isDone ? 100 : 0;

            return (
              <tr key={batch.id} className="hover:bg-gray-50">
                {/* ID */}
                <td className="px-4 py-3 font-mono text-xs text-gray-400">
                  #{batch.id}
                </td>

                {/* Firma */}
                <td className="px-4 py-3 font-medium text-gray-800">
                  {batch.company_name}
                </td>

                {/* Jahr */}
                <td className="px-4 py-3 text-gray-600">{batch.year}</td>

                {/* Status */}
                <td className="px-4 py-3">
                  <span
                    className={[
                      "rounded-full px-2 py-0.5 text-xs font-medium",
                      STATUS_STYLES[batch.status] ?? "bg-gray-100 text-gray-500",
                    ].join(" ")}
                  >
                    {batch.status}
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
