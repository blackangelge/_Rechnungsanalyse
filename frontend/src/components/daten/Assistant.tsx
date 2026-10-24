"use client";

import React, { useEffect, useRef, useState } from "react";
import { AssistantProposal, AssistantReanalysis, AssistantReply, AssistantStep, dbAssistantApi, extractApiError } from "@/lib/api";

type Msg =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; reply?: AssistantReply; applied?: Record<number, string>; dismissed?: Set<number> };

const EXAMPLES = [
  "Wie viele Belege hat jeder Import, und wie viele davon sind in „Prüfen!“?",
  "Zeige alle Positionen mit Kalkammonsalpeter und den Preis je Tonne pro Lieferant.",
  "Welche Positionen haben Menge in kg, aber einen Preis je Tonne? Nenne die Beleg-IDs.",
  "Sende alle Belege, bei denen ein Gesamtpreis fehlt, erneut zur KI-Analyse.",
];

function StepView({ s, i }: { s: AssistantStep; i: number }) {
  return (
    <details className="rounded border border-gray-200 bg-gray-50 text-xs">
      <summary className="cursor-pointer px-2 py-1 text-gray-600">
        Abfrage {i + 1}: {s.ok ? `${s.row_count} Zeile(n)${s.truncated ? " (gekürzt)" : ""}` : <span className="text-red-600">Fehler</span>}
      </summary>
      <pre className="overflow-x-auto whitespace-pre-wrap border-t border-gray-200 p-2 font-mono text-[11px] text-gray-700">{s.sql}</pre>
      {!s.ok && <div className="border-t border-gray-200 p-2 text-red-700">{s.error}</div>}
      {s.ok && s.columns && s.rows && s.rows.length > 0 && (
        <div className="overflow-x-auto border-t border-gray-200">
          <table className="min-w-full text-[11px]">
            <thead><tr>{s.columns.map((c) => <th key={c} className="px-2 py-1 text-left font-medium text-gray-600">{c}</th>)}</tr></thead>
            <tbody>
              {s.rows.map((r, ri) => (
                <tr key={ri} className="border-t border-gray-100">
                  {r.map((v, ci) => <td key={ci} className="max-w-[16rem] truncate px-2 py-0.5" title={String(v ?? "")}>{v === null ? <span className="text-gray-300">NULL</span> : String(v)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {(s.row_count ?? 0) > s.rows.length && <div className="px-2 py-1 text-gray-400">… nur die ersten {s.rows.length} Zeilen angezeigt (die KI sah mehr)</div>}
        </div>
      )}
    </details>
  );
}

function ProposalView({
  p, state, onApply, onDismiss, busy,
}: {
  p: AssistantProposal;
  state: string | undefined;           // undefined = offen, sonst Ergebnistext
  onApply: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  return (
    <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm" data-testid="proposal">
      <div className="mb-1 font-medium text-amber-900">Änderungsvorschlag: {p.table} #{String(p.id)}</div>
      {p.reason && <div className="mb-2 text-xs text-amber-800">{p.reason}</div>}
      <table className="mb-2 text-xs">
        <tbody>
          {Object.entries(p.changes).map(([k, v]) => (
            <tr key={k}>
              <td className="pr-3 font-medium text-gray-700">{k}</td>
              <td className="pr-2 text-gray-500 line-through">{p.current[k] === null || p.current[k] === undefined ? "NULL" : String(p.current[k])}</td>
              <td className="text-emerald-800">→ {v === null ? "NULL" : String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {state ? (
        <div className="text-xs font-medium text-gray-700">{state}</div>
      ) : (
        <div className="flex gap-2">
          <button onClick={onApply} disabled={busy} className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50" data-testid="apply-proposal">Freigeben &amp; speichern</button>
          <button onClick={onDismiss} disabled={busy} className="rounded border border-gray-300 bg-white px-3 py-1 text-xs text-gray-600 hover:bg-gray-50">Verwerfen</button>
        </div>
      )}
    </div>
  );
}

function ReanalysisView({
  r, state, onApply, onDismiss, busy,
}: {
  r: AssistantReanalysis;
  state: string | undefined;
  onApply: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  return (
    <div className="rounded border border-violet-300 bg-violet-50 p-3 text-sm" data-testid="reanalysis">
      <div className="mb-1 font-medium text-violet-900">
        Vorschlag: {r.count.toLocaleString("de-DE")} Beleg(e) erneut zur KI-Analyse senden
      </div>
      {r.reason && <div className="mb-2 text-xs text-violet-800">{r.reason}</div>}
      {r.skip_type_detection && <div className="mb-2 text-xs text-violet-800">Nur neu auslesen: der Dokumententyp wird nicht erneut erkannt.</div>}
      <div className="mb-2 text-xs text-gray-700">
        Beleg-IDs: {r.sample.map((d) => `#${d.id}`).join(", ")}{r.count > r.sample.length ? ` … (+${r.count - r.sample.length})` : ""}
        {r.skipped > 0 && <> · {r.skipped} nicht geeignet (gelöscht / in Verarbeitung / unbekannt)</>}
        {r.truncated && <> · <b>auf die ersten {r.count.toLocaleString("de-DE")} begrenzt</b></>}
      </div>
      <div className="mb-2 text-xs text-gray-500">
        Die Belege werden in die Warteschlange eingereiht und von den Workern analysiert (KI-Konfiguration/Workflow wie üblich). Die bisherige
        Auslesung bleibt in der Analyse-Historie des Belegs erhalten.
      </div>
      {state ? (
        <div className="text-xs font-medium text-gray-700">{state}</div>
      ) : (
        <div className="flex gap-2">
          <button onClick={onApply} disabled={busy} data-testid="apply-reanalysis"
            className="rounded bg-violet-600 px-3 py-1 text-xs font-medium text-white hover:bg-violet-700 disabled:opacity-50">Freigeben &amp; einreihen</button>
          <button onClick={onDismiss} disabled={busy} className="rounded border border-gray-300 bg-white px-3 py-1 text-xs text-gray-600 hover:bg-gray-50">Verwerfen</button>
        </div>
      )}
    </div>
  );
}

export default function Assistant() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<{ id: number; name: string; model_name: string; active: boolean; default: boolean }[]>([]);
  const [modelId, setModelId] = useState<string>("");
  const [allowProposals, setAllowProposals] = useState(true);
  const [applying, setApplying] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dbAssistantApi.models().then((m) => { setModels(m); setModelId(m.find((x) => x.default)?.id.toString() ?? m[0]?.id.toString() ?? ""); }).catch(() => undefined);
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function send(text: string) {
    const t = text.trim();
    if (!t || busy) return;
    const next: Msg[] = [...messages, { role: "user", content: t }];
    setMessages(next);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const reply = await dbAssistantApi.chat(
        next.map((m) => ({ role: m.role, content: m.content })), modelId ? Number(modelId) : undefined, allowProposals);
      setMessages([...next, { role: "assistant", content: reply.answer, reply, applied: {}, dismissed: new Set() }]);
    } catch (e) {
      setError(extractApiError(e, "Die KI hat nicht geantwortet"));
    } finally {
      setBusy(false);
    }
  }

  async function apply(mi: number, pi: number) {
    const m = messages[mi];
    if (m.role !== "assistant" || !m.reply) return;
    const p = m.reply.proposals[pi];
    setApplying(true);
    try {
      const res = await dbAssistantApi.apply({ table: p.table, id: p.id, changes: p.changes, reason: p.reason });
      const text = `✓ Gespeichert (${Object.keys(res.changed).join(", ") || "keine Änderung"})`;
      setMessages((all) => all.map((x, i) => (i === mi && x.role === "assistant" ? { ...x, applied: { ...x.applied, [pi]: text } } : x)));
    } catch (e) {
      const text = `✗ ${extractApiError(e, "Speichern fehlgeschlagen")}`;
      setMessages((all) => all.map((x, i) => (i === mi && x.role === "assistant" ? { ...x, applied: { ...x.applied, [pi]: text } } : x)));
    } finally {
      setApplying(false);
    }
  }

  // Zustände der Wiederanalyse-Vorschläge liegen unter den Schlüsseln 1000 + Index (neben den Änderungsvorschlägen)
  async function applyReanalysis(mi: number, ri: number) {
    const m = messages[mi];
    if (m.role !== "assistant" || !m.reply?.reanalysis) return;
    const r = m.reply.reanalysis[ri];
    if (!confirm(`${r.count.toLocaleString("de-DE")} Beleg(e) jetzt zur KI-Analyse einreihen?\n\nDie bisherige Auslesung wird ersetzt (bleibt in der Analyse-Historie).`)) return;
    setApplying(true);
    let text: string;
    try {
      const res = await dbAssistantApi.applyReanalysis({ document_ids: r.document_ids, reason: r.reason, skip_type_detection: r.skip_type_detection });
      text = `✓ ${res.message}`;
    } catch (e) {
      text = `✗ ${extractApiError(e, "Einreihen fehlgeschlagen")}`;
    } finally {
      setApplying(false);
    }
    setMessages((all) => all.map((x, i) => (i === mi && x.role === "assistant" ? { ...x, applied: { ...x.applied, [1000 + ri]: text } } : x)));
  }

  function dismissReanalysis(mi: number, ri: number) {
    setMessages((all) => all.map((x, i) => (i === mi && x.role === "assistant" ? { ...x, applied: { ...x.applied, [1000 + ri]: "Verworfen" } } : x)));
  }

  function dismiss(mi: number, pi: number) {
    setMessages((all) => all.map((x, i) => (i === mi && x.role === "assistant" ? { ...x, applied: { ...x.applied, [pi]: "Verworfen" } } : x)));
  }

  return (
    <div className="flex h-[calc(100vh-14rem)] min-h-[28rem] flex-col rounded-lg border bg-white shadow-sm" data-testid="assistant">
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-2 text-sm">
        <label className="flex items-center gap-2 text-gray-600">
          KI:
          <select value={modelId} onChange={(e) => setModelId(e.target.value)} className="rounded border border-gray-300 px-2 py-1 text-sm">
            {models.length === 0 && <option value="">(keine Konfiguration)</option>}
            {models.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.model_name}{m.default ? " (Standard)" : m.active ? "" : " (inaktiv)"}</option>)}
          </select>
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-gray-600" title="Die KI schreibt nie selbst — sie kann Änderungen nur vorschlagen, die du einzeln freigibst">
          <input type="checkbox" checked={allowProposals} onChange={(e) => setAllowProposals(e.target.checked)} />
          Änderungsvorschläge erlauben (Freigabe immer von dir)
        </label>
        {messages.length > 0 && <button onClick={() => { setMessages([]); setError(null); }} className="ml-auto text-xs text-gray-500 hover:underline">Neue Unterhaltung</button>}
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {messages.length === 0 && (
          <div className="text-sm text-gray-500">
            <p className="mb-2">Frag die KI zu deinen Daten. Sie fragt die Datenbank selbstständig ab (nur lesend), wertet aus und nennt Beleg-/Positions-IDs.</p>
            <ul className="space-y-1">
              {EXAMPLES.map((e) => (
                <li key={e}><button onClick={() => send(e)} className="rounded border border-gray-200 px-2 py-1 text-left text-xs text-blue-700 hover:bg-blue-50">{e}</button></li>
              ))}
            </ul>
          </div>
        )}
        {messages.map((m, mi) => (
          <div key={mi} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div className={`max-w-[90%] space-y-2 rounded-lg px-3 py-2 text-sm ${m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"}`}>
              <div className="whitespace-pre-wrap">{m.content}</div>
              {m.role === "assistant" && m.reply && (
                <>
                  {m.reply.steps.map((s, si) => <StepView key={si} s={s} i={si} />)}
                  {m.reply.proposals.map((p, pi) => (
                    <ProposalView key={pi} p={p} state={m.applied?.[pi]} busy={applying}
                      onApply={() => apply(mi, pi)} onDismiss={() => dismiss(mi, pi)} />
                  ))}
                  {(m.reply.reanalysis ?? []).map((r, ri) => (
                    <ReanalysisView key={`r${ri}`} r={r} state={m.applied?.[1000 + ri]} busy={applying}
                      onApply={() => applyReanalysis(mi, ri)} onDismiss={() => dismissReanalysis(mi, ri)} />
                  ))}
                  <div className="text-[10px] text-gray-400">
                    {m.reply.usage.calls} KI-Aufruf(e) · {m.reply.usage.input_tokens.toLocaleString("de-DE")} Token ein / {m.reply.usage.output_tokens.toLocaleString("de-DE")} aus · {m.reply.usage.seconds} s
                  </div>
                </>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-gray-500" data-testid="assistant-busy">KI arbeitet… (bei lokalen Modellen können mehrere Minuten vergehen)</div>}
        {error && <div className="rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        <div ref={bottom} />
      </div>

      <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={2} placeholder="Frage stellen … (Enter = senden, Shift+Enter = Zeilenumbruch)"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
          className="flex-1 resize-none rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none" data-testid="assistant-input" />
        <button type="submit" disabled={busy || !input.trim()} className="self-end rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">Senden</button>
      </form>
    </div>
  );
}
