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
import { Alert, Icon, PageHeader, Spinner } from "@/components/ui";

export default function DashboardPage() {
  const [batches, setBatches]           = useState<ImportBatch[]>([]);
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [workerStats, setWorkerStats]   = useState<WorkerStats | null>(null);
  const [showHidden, setShowHidden]     = useState(false);
  const [selected, setSelected]         = useState<Set<number>>(new Set());
  const [actionMsg, setActionMsg]       = useState<string | null>(null);

  /** Alle Import-Batches laden (ohne Dokumente — schneller als die Detail-Ansicht). */
  const load = useCallback(async () => {
    try {
      setError(null);
      // Ausgeblendete werden immer mitgeladen (Anzahl für den Schalter); angezeigt nur auf Wunsch.
      const data = await importsApi.list({ include_hidden: true });
      setBatches(data);
      // Auswahl um nicht mehr vorhandene Imports bereinigen (Auto-Refresh, Löschen)
      setSelected((prev) => {
        const ids = new Set(data.map((b) => b.id));
        const next = new Set(Array.from(prev).filter((id) => ids.has(id)));
        return next.size === prev.size ? prev : next;
      });
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

  const visibleBatches = batches.filter((b) => showHidden || !b.hidden_at);
  const hiddenCount = batches.filter((b) => b.hidden_at).length;
  const selectedBatches = batches.filter((b) => selected.has(b.id));
  const selectedHidden = selectedBatches.filter((b) => b.hidden_at).length;

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleSelectAll() {
    const visible = visibleBatches.map((b) => b.id);
    setSelected((prev) => (visible.every((id) => prev.has(id)) ? new Set() : new Set(visible)));
  }

  /** Imports aus-/einblenden (Auswahl oder einzelner Import) */
  async function setHidden(ids: number[], hidden: boolean) {
    setActionMsg(null);
    try {
      const r = await importsApi.setHidden(ids, hidden);
      setActionMsg(
        `${r.updated} Import${r.updated !== 1 ? "s" : ""} ${hidden ? "ausgeblendet" : "eingeblendet"}.` +
        (hidden ? " Rückgängig: Ausgeblendete anzeigen, dann Einblenden." : ""),
      );
      setSelected(new Set());
      await load();
    } catch {
      setError("Aus-/Einblenden fehlgeschlagen");
    }
  }

  const runningCount = batches.filter((b) => b.status === "running").length;
  const noAI         = workerStats?.no_ai_available ?? false;
  const queueLen     = workerStats?.queue_length ?? 0;

  const kpis = [
    { label: "Aktive Imports", value: runningCount, tone: runningCount > 0 ? "text-blue-700" : "text-gray-900" },
    { label: "In der Warteschlange", value: queueLen, tone: "text-gray-900" },
    { label: "In Bearbeitung", value: workerStats?.in_progress ?? 0, tone: "text-gray-900" },
    { label: "Fehlgeschlagen", value: workerStats?.failed_tasks ?? 0, tone: (workerStats?.failed_tasks ?? 0) > 0 ? "text-red-600" : "text-gray-900" },
    { label: "Von der KI verarbeitet", value: workerStats?.documents_processed_total ?? 0, tone: "text-gray-900" },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Dashboard"
        subtitle={runningCount > 0 ? `${runningCount} Import${runningCount > 1 ? "s" : ""} aktiv — wird alle 10 s aktualisiert` : "Alle Imports im Überblick"}
        actions={<>
          {hiddenCount > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} className="rounded border-gray-300 text-blue-600" />
              Ausgeblendete anzeigen ({hiddenCount})
            </label>
          )}
          <button onClick={load} className="btn btn-secondary btn-sm">Aktualisieren</button>
          <Link href="/imports/new" className="btn btn-primary"><Icon name="plus" /> Neuer Import</Link>
        </>}
      />

      {workerStats && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {kpis.map((k) => (
            <div key={k.label} className="card px-4 py-3">
              <p className="text-xs font-medium text-gray-500">{k.label}</p>
              <p className={`mt-1 text-2xl font-semibold tabular-nums ${k.tone}`}>{k.value.toLocaleString("de-DE")}</p>
            </div>
          ))}
        </div>
      )}

      {noAI && (
        <Alert tone="error">
          <b>Keine KI verfügbar!</b> Alle KI-Konfigurationen sind deaktiviert oder temporär gesperrt.
          {queueLen > 0 && <> {queueLen} Dokument{queueLen !== 1 ? "e" : ""} warten in der Warteschlange.</>}
          {" "}→ <Link href="/settings/ai" className="font-medium underline">KI-Konfiguration</Link> oder <Link href="/logs" className="font-medium underline">KI-Statistiken</Link>
        </Alert>
      )}

      {actionMsg && <div data-testid="action-msg"><Alert tone="success" onClose={() => setActionMsg(null)}>{actionMsg}</Alert></div>}

      {selected.size > 0 && (
        <div className="card flex flex-wrap items-center gap-3 border-blue-200 bg-blue-50 px-4 py-2.5" data-testid="selection-bar">
          <span className="text-sm font-medium text-blue-800">{selected.size} Import{selected.size !== 1 ? "s" : ""} ausgewählt</span>
          <Link href={`/export-vorlagen?batch_ids=${Array.from(selected).join(",")}`} className="btn btn-secondary btn-sm">Exportieren …</Link>
          {selectedHidden < selected.size && (
            <button onClick={() => setHidden(selectedBatches.filter((b) => !b.hidden_at).map((b) => b.id), true)} className="btn btn-secondary btn-sm">Ausblenden</button>
          )}
          {selectedHidden > 0 && (
            <button onClick={() => setHidden(selectedBatches.filter((b) => b.hidden_at).map((b) => b.id), false)} className="btn btn-secondary btn-sm">Einblenden</button>
          )}
          <button onClick={() => setSelected(new Set())} className="btn btn-ghost btn-sm ml-auto">Auswahl aufheben</button>
        </div>
      )}

      {loading && batches.length === 0 && <Spinner />}
      {error && <Alert tone="error">{error}</Alert>}

      <BatchTable
        batches={visibleBatches}
        onDeleted={(id) => setBatches((prev) => prev.filter((b) => b.id !== id))}
        selectedIds={selected}
        onToggleSelect={toggleSelect}
        onToggleSelectAll={toggleSelectAll}
        onSetHidden={(id, hidden) => setHidden([id], hidden)}
      />
    </div>
  );
}
