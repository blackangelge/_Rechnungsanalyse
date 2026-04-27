/**
 * Seite: Import-Dashboard (/dashboard)
 *
 * Übersicht aller Import-Batches mit Auto-Refresh alle 10 Sekunden.
 * Zeigt eine Warnung wenn keine KI-Konfiguration verfügbar ist
 * (noAI=true aus worker-stats) oder Dokumente in der Queue warten.
 *
 * Lädt parallel:
 *   - Alle Import-Batches (importsApi.list) — alle 10 s
 *   - Worker-Status (workerApi.getStats) — alle 30 s
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ImportBatch, WorkerStats, importsApi, workerApi, extractApiError } from "@/lib/api";
import BatchTable from "@/components/dashboard/BatchTable";

export default function DashboardPage() {
  const [batches, setBatches]           = useState<ImportBatch[]>([]);
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [workerStats, setWorkerStats]   = useState<WorkerStats | null>(null);

  /** Alle Import-Batches laden (ohne Dokumente — schneller als die Detail-Ansicht). */
  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await importsApi.list();
      setBatches(data);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der Imports"));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorkerStats = useCallback(async () => {
    try {
      setWorkerStats(await workerApi.getStats());
    } catch {
      // Fehler still ignorieren — nur ein Hinweis, kein kritischer Fehler
    }
  }, []);

  useEffect(() => {
    load();
    loadWorkerStats();
    const interval = setInterval(load, 10_000);
    const wInterval = setInterval(loadWorkerStats, 30_000);
    return () => { clearInterval(interval); clearInterval(wInterval); };
  }, [load, loadWorkerStats]);

  const runningCount = batches.filter((b) => b.status === "running").length;
  const noAI         = workerStats?.no_ai_available ?? false;
  const queueLen     = workerStats?.queue_length ?? 0;

  return (
    <div className="space-y-4">
      {/* Kopfzeile */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Import-Dashboard</h1>
          {runningCount > 0 && (
            <p className="text-sm text-blue-600">
              {runningCount} Import{runningCount > 1 ? "s" : ""} aktiv — wird alle 10 s aktualisiert
            </p>
          )}
        </div>
        <button onClick={load} className="text-xs text-blue-600 hover:underline">
          Aktualisieren
        </button>
      </div>

      {/* KI-Warnung: keine aktive KI */}
      {noAI && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="font-semibold">⚠ Keine KI verfügbar!</span>{" "}
          Alle KI-Konfigurationen sind deaktiviert oder temporär gesperrt.
          {queueLen > 0 && (
            <span> {queueLen} Dokument{queueLen !== 1 ? "e" : ""} warten in der Warteschlange.</span>
          )}
          {" "}→{" "}
          <a href="/settings/ai" className="underline font-medium">KI-Einstellungen</a>
          {" "}oder{" "}
          <a href="/logs" className="underline font-medium">KI-Statistiken</a>
        </div>
      )}

      {/* Status */}
      {loading && batches.length === 0 && (
        <p className="text-sm text-gray-500">Lade...</p>
      )}
      {error && <p className="text-sm text-red-500">{error}</p>}

      {/* Batch-Tabelle */}
      <BatchTable
        batches={batches}
        onDeleted={(id) => setBatches((prev) => prev.filter((b) => b.id !== id))}
      />

      {/* "+ Neuer Import"-Button unten rechts */}
      <div className="flex justify-end pt-2">
        <Link
          href="/imports/new"
          className="rounded-full bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow hover:bg-blue-700 transition-colors"
        >
          + Neuer Import
        </Link>
      </div>
    </div>
  );
}
