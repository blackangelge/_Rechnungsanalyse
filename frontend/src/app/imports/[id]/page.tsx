/**
 * Seite: Import-Detailansicht (/imports/[id])
 *
 * Zeigt für einen Import-Batch:
 * - Fortschrittsanzeige mit SSE-Echtzeit-Updates
 * - Dokumententabelle (paginiert) — nach Abschluss des Imports
 *
 * Während der Import läuft: SSE für Echtzeit-Fortschritt + Polling als Fallback.
 * Nach Abschluss: Dokumentliste wird einmalig geladen.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { BatchKiStats, ImportBatch, DocumentItem, importsApi } from "@/lib/api";
import ProgressPanel from "@/components/imports/ProgressPanel";
import DocumentsTable from "@/components/imports/DocumentsTable";

export default function ImportDetailPage() {
  const params = useParams();
  const batchId = parseInt(params.id as string);

  // Batch-Metadaten (ohne Dokumente) — immer geladen
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  // Dokumente — nur nach Abschluss des Imports
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null);
  const [docsLoading, setDocsLoading] = useState(false);
  // Aggregierte KI-Statistiken für diesen Batch
  const [kiStats, setKiStats] = useState<BatchKiStats | null>(null);

  const [metaLoading, setMetaLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Ref to track whether batch was ever successfully loaded (avoids `batch` in deps)
  const batchLoadedRef = useRef(false);
  // Ref to track whether documents have been loaded (avoid double-load)
  const docsLoadedRef = useRef(false);
  // Polling-Timer
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Datumsfilter für den Excel-Export (von/bis, optional) + welches Datum geprüft wird
  const [exportDateFrom, setExportDateFrom] = useState("");
  const [exportDateTo, setExportDateTo] = useState("");
  const [exportDateField, setExportDateField] = useState<"invoice_date" | "import_date">("invoice_date");

  /** Nur Batch-Metadaten laden (schnell, kein JOIN über alle Dokumente) */
  const loadMeta = useCallback(async () => {
    try {
      setError(null);
      const data = await importsApi.getStatus(batchId);
      setBatch(data);
      batchLoadedRef.current = true;
      return data;
    } catch (err: unknown) {
      const isNetworkErr =
        err instanceof Error &&
        (err.message.includes("Network Error") ||
          err.message.includes("ECONNRESET") ||
          err.message.includes("socket hang up"));
      if (isNetworkErr && !batchLoadedRef.current) {
        setError("Backend kurzzeitig nicht erreichbar — bitte Seite neu laden.");
      } else if (!isNetworkErr) {
        setError("Fehler beim Laden des Imports");
      }
      return null;
    } finally {
      setMetaLoading(false);
    }
  }, [batchId]);

  /** KI-Statistiken laden (Token-Summen + Gesamtdauer) */
  const loadKiStats = useCallback(async () => {
    try {
      const stats = await importsApi.kiStats(batchId);
      setKiStats(stats);
    } catch {
      // Fehler ignorieren — Stats sind optional
    }
  }, [batchId]);

  /** Dokumente laden — einmalig nach Import-Abschluss */
  const loadDocuments = useCallback(async () => {
    if (docsLoadedRef.current) return; // kein Doppel-Load
    docsLoadedRef.current = true;
    setDocsLoading(true);
    try {
      const data = await importsApi.get(batchId);
      setDocuments(data.documents);
      setBatch(data);
    } catch {
      docsLoadedRef.current = false; // Retry erlauben
      setError("Fehler beim Laden der Dokumente");
    } finally {
      setDocsLoading(false);
    }
  }, [batchId]);

  /** Polling-Stop */
  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  /** Polling starten: prüft alle 3 s den Batch-Status (immer aktiv während Import läuft) */
  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return; // läuft bereits
    pollTimerRef.current = setInterval(async () => {
      try {
        const data = await importsApi.getStatus(batchId);
        setBatch(data);
        loadKiStats(); // KI-Stats bei jedem Poll aktualisieren
        if (data.status === "done" || data.status === "error") {
          stopPolling();
          loadDocuments();
        }
      } catch {
        // Netzwerkfehler ignorieren — nächster Poll-Versuch in 3 s
      }
    }, 3000);
  }, [batchId, loadDocuments, loadKiStats, stopPolling]);

  // Initialer Ladevorgang
  useEffect(() => {
    loadKiStats(); // KI-Stats direkt beim Laden holen
    loadMeta().then((data) => {
      if (!data) return;
      if (data.status === "done" || data.status === "error") {
        // Import bereits abgeschlossen → Dokumente sofort laden
        loadDocuments();
      } else {
        // Import läuft → Polling starten (aktualisiert initialTotal/initialProcessed in ProgressPanel)
        startPolling();
      }
    });
    return () => stopPolling();
  }, [loadMeta, loadDocuments, loadKiStats, startPolling, stopPolling]);

  /** Callback vom ProgressPanel: Import ist abgeschlossen (via SSE) */
  const handleImportDone = useCallback(() => {
    stopPolling(); // Polling stoppen, SSE hat gewonnen
    loadKiStats(); // Finale KI-Stats laden
    loadDocuments();
  }, [loadDocuments, loadKiStats, stopPolling]);

  /** Manueller Refresh der Dokumentliste */
  const handleManualRefresh = useCallback(() => {
    docsLoadedRef.current = false;
    loadDocuments();
  }, [loadDocuments]);

  /** Ordner-Sync bzw. automatischen Export nachträglich umschalten */
  const handleToggleAutomation = useCallback(
    async (field: "folder_sync" | "auto_export", value: boolean) => {
      try {
        const updated = await importsApi.updateAutomation(batchId, { [field]: value });
        setBatch(updated);
      } catch {
        setError("Einstellung konnte nicht gespeichert werden");
      }
    },
    [batchId]
  );

  if (metaLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-gray-500">
        <span className="animate-spin">⟳</span> Lade Import…
      </div>
    );
  }

  if (error && !batch) {
    return <p className="text-sm text-red-500">{error}</p>;
  }

  if (!batch) return null;

  const isActive = batch.status === "running" || batch.status === "pending";

  const exportParams = new URLSearchParams();
  if (exportDateFrom) exportParams.set("date_from", exportDateFrom);
  if (exportDateTo) exportParams.set("date_to", exportDateTo);
  if (exportDateFrom || exportDateTo) exportParams.set("date_field", exportDateField);
  const exportHref = `/api/imports/${batchId}/export/${exportParams.toString() ? `?${exportParams.toString()}` : ""}`;

  return (
    <div className="space-y-6">
      {/* Kopfzeile */}
      <div>
        <h1 className="text-xl font-semibold text-gray-900">
          Import: {batch.company_name} {batch.year}
        </h1>
        <p className="text-sm text-gray-500">
          {batch.import_folder_path}
          {batch.comment && (
            <> · <span className="italic">{batch.comment}</span></>
          )}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-1.5 text-gray-600">
            <input
              type="checkbox"
              checked={!!batch.folder_sync}
              onChange={(e) => handleToggleAutomation("folder_sync", e.target.checked)}
            />
            Ordner-Sync
            {batch.folder_sync && batch.last_synced_at && (
              <span className="text-xs text-gray-400">
                (zuletzt geprüft: {new Date(batch.last_synced_at).toLocaleString("de-DE")})
              </span>
            )}
          </label>
          <label className="flex items-center gap-1.5 text-gray-600">
            <input
              type="checkbox"
              checked={!!batch.auto_export}
              onChange={(e) => handleToggleAutomation("auto_export", e.target.checked)}
            />
            Automatischer Export
            {batch.auto_export && batch.last_exported_at && (
              <span className="text-xs text-gray-400">
                (zuletzt exportiert: {new Date(batch.last_exported_at).toLocaleString("de-DE")})
              </span>
            )}
          </label>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Fortschrittsanzeige */}
      <ProgressPanel
        batchId={batchId}
        initialStatus={batch.status}
        initialTotal={batch.total_documents ?? 0}
        initialProcessed={batch.status === "done" ? (batch.total_documents ?? 0) : 0}
        kiStats={kiStats}
        onDone={handleImportDone}
      />

      {/* Während Import läuft: Hinweis statt Dokumententabelle */}
      {isActive && (
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-5 py-4 text-sm text-blue-700">
          <span className="mr-2 animate-spin inline-block">⟳</span>
          Import läuft — die Dokumentenliste wird nach Abschluss automatisch geladen.
        </div>
      )}

      {/* Dokumententabelle — nur nach Abschluss */}
      {!isActive && (
        <div className="relative left-1/2 w-screen -translate-x-1/2 px-6">
          <div className="mb-3 flex items-center gap-3">
            <h2 className="font-semibold text-gray-900">Dokumente</h2>
            <button
              onClick={handleManualRefresh}
              disabled={docsLoading}
              className="rounded border border-gray-300 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            >
              {docsLoading ? "Lade…" : "↻ Aktualisieren"}
            </button>
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <select
                value={exportDateField}
                onChange={(e) => setExportDateField(e.target.value as "invoice_date" | "import_date")}
                className="rounded border border-gray-300 px-2 py-1"
              >
                <option value="invoice_date">Rechnungsdatum</option>
                <option value="import_date">Importdatum</option>
              </select>
              <input
                id="export-date-from"
                type="date"
                value={exportDateFrom}
                onChange={(e) => setExportDateFrom(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1"
              />
              <span>bis</span>
              <input
                type="date"
                value={exportDateTo}
                onChange={(e) => setExportDateTo(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1"
              />
            </div>
            <a
              href={exportHref}
              download
              className="rounded border border-emerald-600 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
            >
              ↓ Excel exportieren
            </a>
            <a
              href={`/api/imports/${batchId}/export/new/`}
              download
              title="Nur Belege, die seit dem letzten Abruf (manuell oder automatisch) fertig analysiert wurden"
              className="rounded border border-purple-600 bg-purple-50 px-3 py-1 text-xs font-medium text-purple-700 hover:bg-purple-100"
            >
              ↓ Neue Belege exportieren
            </a>
          </div>

          {docsLoading && !documents && (
            <div className="rounded-lg border bg-white px-6 py-10 text-center text-sm text-gray-400 shadow-sm">
              <span className="animate-spin inline-block mr-2">⟳</span>
              Lade Dokumente…
            </div>
          )}

          {documents !== null && (
            <DocumentsTable
              documents={documents}
              onRefresh={handleManualRefresh}
            />
          )}
        </div>
      )}
    </div>
  );
}
