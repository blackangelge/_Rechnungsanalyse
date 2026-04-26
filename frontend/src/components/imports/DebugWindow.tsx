/**
 * Debug-Fenster: Zeigt alle SSE-Events in Echtzeit als Protokoll.
 *
 * Nützlich um zu sehen, was genau während des Imports passiert.
 * Neue Events werden oben angezeigt (neueste zuerst).
 * Das Fenster ist auf max. 200px Höhe begrenzt und scrollbar.
 */

"use client";

import { ProgressEvent } from "@/lib/api";
import { useSSE } from "@/lib/sse";

interface Props {
  batchId: number;
  initialStatus: string;
}

export default function DebugWindow({ batchId, initialStatus }: Props) {
  const shouldStream = initialStatus === "running" || initialStatus === "pending";
  const sseUrl = shouldStream ? `/api/imports/${batchId}/progress` : null;

  // Alle Events werden gesammelt (useSSE.events enthält die History)
  const { events, status: sseStatus } = useSSE<ProgressEvent>(sseUrl);

  return (
    <div className="rounded-lg border bg-gray-900 p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-300">Debug-Protokoll</h3>
        <span className="text-xs text-gray-500">
          {events.length} Events · SSE: {sseStatus}
        </span>
      </div>

      {/* Scrollbares Event-Protokoll */}
      <div className="max-h-52 overflow-y-auto space-y-1 font-mono text-xs">
        {events.length === 0 ? (
          <p className="text-gray-500">
            {shouldStream ? "Warte auf Events..." : "Import nicht aktiv"}
          </p>
        ) : (
          /* Neueste Events zuerst anzeigen */
          [...events].reverse().map((event, idx) => (
            <div key={idx} className="flex gap-2 text-gray-300">
              {/* Zeitstempel */}
              <span className="shrink-0 text-gray-500">
                {event.timestamp.toLocaleTimeString("de-DE")}
              </span>
              {/* Event-Typ */}
              <span
                className={[
                  "shrink-0 font-semibold",
                  event.type === "done" ? "text-green-400" : "",
                  event.type === "error" ? "text-red-400" : "",
                  event.type === "progress" ? "text-blue-400" : "",
                ].join(" ")}
              >
                [{event.type}]
              </span>
              {/* Event-Inhalt als kompakter Text */}
              <span className="text-gray-400">
                {(event.data as ProgressEvent).processed}/
                {(event.data as ProgressEvent).total} (
                {(event.data as ProgressEvent).percent}%)
                {" · "}
                {(event.data as ProgressEvent).elapsed_seconds}s
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
