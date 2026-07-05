/**
 * Echtzeit-Fortschrittsanzeige für einen laufenden Import.
 *
 * Verbindet sich via SSE mit dem Backend und zeigt live:
 * - Kopierte / Gesamt-Dokumente (reiner Datei-Kopiervorgang — die KI-Analyse läuft
 *   asynchron über die Worker-Queue und wird hier NICHT mitgezählt)
 * - Aggregierte KI-Token-Statistiken (Tokens gesamt, KI-Zeit) der bereits analysierten Belege
 * - Geschätzte Restzeit, aktueller Status
 *
 * Kein Fortschrittsbalken: Da der Datei-Kopiervorgang meist in Sekunden abgeschlossen ist,
 * würde ein Balken sofort 100% anzeigen, obwohl die eigentliche KI-Analyse noch läuft —
 * das wirkte wie ein hängender/defekter Fortschritt.
 *
 * Ruft onDone() auf, sobald der Import (Kopiervorgang) abgeschlossen ist (done oder error).
 */

"use client";

import { useEffect } from "react";
import { BatchKiStats, ProgressEvent } from "@/lib/api";
import { useSSE } from "@/lib/sse";

interface Props {
  batchId: number;
  initialStatus: string;
  initialTotal?: number;
  initialProcessed?: number;
  /** Aggregierte KI-Statistiken für diesen Import-Batch */
  kiStats?: BatchKiStats | null;
  /** Wird aufgerufen, wenn der Import abgeschlossen ist (done oder error). */
  onDone?: () => void;
}

/** Formatiert Sekunden als MM:SS oder HH:MM:SS */
function formatDuration(seconds: number): string {
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function ProgressPanel({
  batchId,
  initialStatus,
  initialTotal = 0,
  initialProcessed = 0,
  kiStats,
  onDone,
}: Props) {
  // SSE nur aktiv, wenn Import läuft oder noch nicht begonnen hat
  const shouldStream = initialStatus === "running" || initialStatus === "pending";
  const sseUrl = shouldStream ? `/api/imports/${batchId}/progress` : null;

  const { data, status: sseStatus, error } = useSSE<ProgressEvent>(sseUrl);

  // onDone auslösen, wenn SSE "done" oder "error" meldet
  useEffect(() => {
    if (data?.status === "done" || data?.status === "error") {
      onDone?.();
    }
  }, [data?.status, onDone]);

  // Anzeige: SSE-Daten oder Fallback auf Initialstatus
  const currentStatus = data?.status ?? initialStatus;
  const isRunning = currentStatus === "running" || currentStatus === "pending";
  const isDone = currentStatus === "done";
  const isError = currentStatus === "error";

  const total = data?.total ?? initialTotal;
  const processed = data?.processed ?? initialProcessed;
  const elapsed = data?.elapsed_seconds ?? 0;
  const speed = data?.docs_per_minute ?? 0;

  // Geschätzte Restzeit
  const remaining = (speed > 0 && total > processed)
    ? Math.round((total - processed) / speed * 60)
    : null;

  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-semibold">Import-Fortschritt</h2>
        <span
          className={[
            "rounded-full px-3 py-1 text-xs font-medium",
            isRunning ? "bg-blue-100 text-blue-700" : "",
            isDone ? "bg-green-100 text-green-700" : "",
            isError ? "bg-red-100 text-red-700" : "",
            !isRunning && !isDone && !isError ? "bg-gray-100 text-gray-500" : "",
          ].join(" ")}
        >
          {isDone ? "Abgeschlossen" : isError ? "Fehler" : isRunning ? "Läuft…" : currentStatus}
        </span>
      </div>

      {/* SSE-Verbindungsfehler */}
      {error && (
        <p className="mb-3 text-sm text-red-500">{error}</p>
      )}

      {/* Dokumentenzähler (Datei-Kopiervorgang — die KI-Analyse läuft separat im Hintergrund) */}
      <div className="mb-4 text-xs text-gray-500">
        {processed.toLocaleString("de-DE")} / {total.toLocaleString("de-DE")} Dokumente kopiert
      </div>

      {/* KI-Token-Statistiken */}
      <div className="grid grid-cols-2 gap-3 text-center">
        <div className="rounded bg-gray-50 p-3">
          <p className="text-xl font-bold tabular-nums text-gray-800">
            {kiStats && kiStats.total_tokens > 0
              ? kiStats.total_tokens.toLocaleString("de-DE")
              : "–"}
          </p>
          <p className="text-xs text-gray-500">Tokens gesamt</p>
        </div>
        <div className="rounded bg-gray-50 p-3">
          <p className="text-xl font-bold tabular-nums text-gray-800">
            {kiStats && kiStats.total_duration_seconds > 0
              ? formatDuration(Math.round(kiStats.total_duration_seconds))
              : "–"}
          </p>
          <p className="text-xs text-gray-500">KI-Zeit</p>
        </div>
      </div>

      {/* Restzeit-Schätzung */}
      {isRunning && remaining !== null && remaining > 0 && (
        <p className="mt-3 text-center text-xs text-gray-400">
          Geschätzte Restzeit: ca. {formatDuration(remaining)}
        </p>
      )}

      {/* Abschluss-Meldung */}
      {isError && (
        <p className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          Import mit Fehlern abgeschlossen. Fehlerdokumente sind unten markiert.
        </p>
      )}
    </div>
  );
}
