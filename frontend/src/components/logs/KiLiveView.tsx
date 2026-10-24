"use client";

/**
 * Live-Ansicht je KI-Rechner: welche Belege gerade dort laufen und wie lange schon.
 *
 * Aktualisiert sich selbst: alle 2 s ein Abruf (ohne sich zu überlappen, pausiert bei verstecktem Tab), dazwischen zählt die
 * Laufzeit jede Sekunde im Browser weiter (Laufzeit zum Abrufzeitpunkt + seitdem vergangene Zeit). Fällt ein Abruf aus, bleibt
 * die letzte Anzeige stehen und ein Hinweis erscheint.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Badge, Card, EmptyState } from "@/components/ui";
import { KiLive, KiLiveRun, workerApi } from "@/lib/api";

const POLL_MS = 2000;
const WARN_S = 120;       // ab 2 Minuten gelb
const SLOW_S = 600;       // ab 10 Minuten rot

export function fmtElapsed(s: number): string {
  const total = Math.max(0, Math.floor(s));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")} h` : `${m}:${String(sec).padStart(2, "0")} min`;
}

export default function KiLiveView() {
  const [live, setLive] = useState<KiLive | null>(null);
  const [fetchedAt, setFetchedAt] = useState(0);            // performance.now() beim letzten erfolgreichen Abruf
  const [now, setNow] = useState(0);
  const [failed, setFailed] = useState(false);
  const busy = useRef(false);

  useEffect(() => {
    let stopped = false;
    async function poll() {
      if (busy.current || document.hidden) return;
      busy.current = true;
      try {
        const data = await workerApi.live();
        if (!stopped) { setLive(data); setFetchedAt(performance.now()); setNow(performance.now()); setFailed(false); }
      } catch {
        if (!stopped) setFailed(true);
      } finally {
        busy.current = false;
      }
    }
    poll();
    const pollTimer = window.setInterval(poll, POLL_MS);
    const tick = window.setInterval(() => setNow(performance.now()), 1000);
    const onVisible = () => { if (!document.hidden) poll(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { stopped = true; window.clearInterval(pollTimer); window.clearInterval(tick); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  const elapsed = (r: KiLiveRun) => r.elapsed_seconds + (failed ? 0 : Math.max(0, (now - fetchedAt) / 1000));

  if (!live) {
    return <p className="text-sm text-gray-400">{failed ? "Live-Ansicht konnte nicht geladen werden." : "Lade Live-Ansicht …"}</p>;
  }

  return (
    <div data-testid="ki-live">
      <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-gray-600">
        <span><b className="tabular-nums text-gray-900">{live.queue_pending.toLocaleString("de-DE")}</b> in der Warteschlange</span>
        <span><b className="tabular-nums text-gray-900">{live.running_total}</b> laufen gerade auf KI-Rechnern</span>
        {live.waiting_for_slot > 0 && <span title="Vom Worker übernommen, aber noch ohne freien Slot oder in Vorbereitung (PDF rendern, Seitenerkennung …)"><b className="tabular-nums text-gray-900">{live.waiting_for_slot}</b> warten auf einen freien Slot</span>}
        {live.paused && <Badge tone="amber">Dispatcher pausiert</Badge>}
        {!live.worker_online && <Badge tone="red">Worker-Container nicht erreichbar</Badge>}
        <span className={`ml-auto flex items-center gap-1.5 text-xs ${failed ? "text-red-600" : "text-gray-400"}`}>
          <span className={`inline-block h-2 w-2 rounded-full ${failed ? "bg-red-500" : "animate-pulse bg-emerald-500"}`} />
          {failed ? "Aktualisierung unterbrochen — letzte Daten" : "aktualisiert sich automatisch"}
        </span>
      </div>

      {live.servers.length === 0 ? (
        <EmptyState title="Keine KI-Konfiguration vorhanden" text="Lege unter Einstellungen → KI-Konfiguration einen KI-Rechner an." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {live.servers.map((s) => {
            const state = !s.active ? { tone: "gray" as const, text: "inaktiv" } : s.is_down ? { tone: "red" as const, text: "gesperrt / nicht erreichbar" } : { tone: "green" as const, text: "bereit" };
            return (
              <Card key={s.id} title={s.name} pad={false}
                actions={<div className="flex items-center gap-2"><Badge tone={state.tone}>{state.text}</Badge>
                  <span className="flex items-center gap-1" title={`${s.busy} von ${s.parallel_request} Slots belegt`} aria-label={`${s.busy} von ${s.parallel_request} Slots belegt`}>
                    {Array.from({ length: Math.max(s.parallel_request, s.busy) }).map((_, i) => (
                      <span key={i} className={`inline-block h-2.5 w-2.5 rounded-full ${i < s.busy ? "bg-blue-600" : "bg-gray-200"}`} />
                    ))}
                    <span className="ml-1 text-xs tabular-nums text-gray-500">{s.busy}/{s.parallel_request}</span>
                  </span></div>}>
                <div className="px-5 pb-1 pt-0 text-xs text-gray-400">{[s.model_name, s.url].filter(Boolean).join(" · ")}</div>
                {s.running.length === 0 ? (
                  <p className="px-5 py-4 text-sm text-gray-400">{s.active && !s.is_down ? "Leerlauf — keine Anfrage." : "—"}</p>
                ) : (
                  <table className="table-ra">
                    <thead><tr><th>Beleg</th><th>Import</th><th className="text-right">Seiten</th><th className="text-right">Läuft seit</th></tr></thead>
                    <tbody>
                      {s.running.map((r) => {
                        const e = elapsed(r);
                        return (
                          <tr key={r.task_id}>
                            <td className="max-w-[16rem] truncate" title={r.filename}>
                              {r.document_id != null ? <Link className="text-blue-600 hover:underline" href={`/belege?doc_id=${r.document_id}`}>#{r.document_id}</Link> : "–"} <span className="text-gray-700">{r.filename ?? ""}</span>
                            </td>
                            <td className="whitespace-nowrap text-gray-500">{r.company ? `${r.company} ${r.year ?? ""}` : "–"}</td>
                            <td className="text-right tabular-nums">{r.pages ?? "–"}</td>
                            <td className={`whitespace-nowrap text-right font-medium tabular-nums ${e >= SLOW_S ? "text-red-600" : e >= WARN_S ? "text-amber-600" : "text-gray-800"}`}
                              title={e >= SLOW_S ? "Läuft ungewöhnlich lange" : undefined}>{fmtElapsed(e)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
