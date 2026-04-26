/**
 * Wiederverwendbarer React-Hook für Server-Sent Events (SSE).
 *
 * Kapselt die EventSource-API in einem React-Hook, der:
 * - Eine SSE-Verbindung öffnet, sobald die URL bereitgestellt wird
 * - Eingehende Events als typisierte Daten zurückgibt
 * - Die Verbindung schließt, wenn die Komponente unmountet wird
 * - Den Verbindungsstatus (connecting / open / closed / error) verfolgt
 *
 * Verwendung:
 *   const { data, status } = useSSE<ProgressEvent>('/api/imports/1/progress');
 */

"use client";

import { useEffect, useRef, useState } from "react";

/** Verbindungsstatus des SSE-Streams */
export type SSEStatus = "connecting" | "open" | "closed" | "error";

/** Rückgabe des useSSE-Hooks */
export interface UseSSEResult<T> {
  /** Zuletzt empfangene Daten (null = noch keine Daten) */
  data: T | null;
  /** Aktueller Verbindungsstatus */
  status: SSEStatus;
  /** Letzter Fehlermeldung (null = kein Fehler) */
  error: string | null;
  /** Liste aller bisher empfangenen Events (für Debug-Fenster) */
  events: Array<{ type: string; data: T; timestamp: Date }>;
}

/**
 * Hook, der eine SSE-Verbindung zur angegebenen URL herstellt.
 *
 * @param url - SSE-Endpunkt-URL (null = keine Verbindung)
 * @param eventName - Name des zu empfangenden Events (Standard: "progress")
 * @param stopOnEvents - Events, bei denen die Verbindung geschlossen wird (Standard: ["done"])
 */
export function useSSE<T>(
  url: string | null,
  eventName: string = "progress",
  stopOnEvents: string[] = ["done", "error"]
): UseSSEResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<SSEStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<Array<{ type: string; data: T; timestamp: Date }>>([]);

  // EventSource-Referenz, damit wir sie in cleanup schließen können
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Keine URL = keine Verbindung
    if (!url) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    setError(null);

    // EventSource öffnen
    const es = new EventSource(url);
    esRef.current = es;

    // Verbindung erfolgreich geöffnet
    es.onopen = () => {
      setStatus("open");
    };

    // Fortschritts-Events empfangen
    es.addEventListener(eventName, (event: MessageEvent) => {
      try {
        const parsed: T = JSON.parse(event.data);
        setData(parsed);
        setEvents((prev) => [
          ...prev,
          { type: eventName, data: parsed, timestamp: new Date() },
        ]);
      } catch {
        console.error("SSE: JSON-Parse-Fehler", event.data);
      }
    });

    // Abschluss-Events empfangen (done, error)
    stopOnEvents.forEach((stopEvent) => {
      es.addEventListener(stopEvent, (event: MessageEvent) => {
        try {
          const parsed: T = JSON.parse(event.data);
          setData(parsed);
          setEvents((prev) => [
            ...prev,
            { type: stopEvent, data: parsed, timestamp: new Date() },
          ]);
        } catch {
          // Ignorieren — Abschluss-Event kann auch ohne Daten kommen
        }
        // Verbindung schließen
        es.close();
        setStatus("closed");
      });
    });

    // Verbindungsfehler
    es.onerror = () => {
      setStatus("error");
      setError("SSE-Verbindungsfehler. Ist der Server erreichbar?");
      es.close();
    };

    // Cleanup: Verbindung schließen, wenn Komponente unmountet
    return () => {
      es.close();
      esRef.current = null;
    };
  }, [url]); // Nur bei URL-Änderung neu verbinden

  return { data, status, error, events };
}
