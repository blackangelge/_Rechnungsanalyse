"use client";

import { useEffect, useRef, useState } from "react";
import {
  AiAnswer, AiQueryField, AiQueryHistoryItem, AiQuestionState,
  aiQuestionsApi, changeLogApi, dbAssistantApi, extractApiError,
} from "@/lib/api";

/**
 * Freie KI-Frage an einen oder mehrere Belege („Wie heißt die IBAN?“).
 * Die KI antwortet nur mit dem Wert; mit gewähltem Zielfeld wird der Beleg aktualisiert (im Änderungsprotokoll, rückgängig machbar).
 * Jede Frage landet im Verlauf („Letzte Abfragen“) und lässt sich später wieder auswählen.
 */
export default function AskModal({
  documentIds,
  onClose,
  onChanged,
}: {
  documentIds: number[];
  onClose: () => void;
  onChanged: () => void;          // nach Änderungen an Belegen (Liste/Infos neu laden)
}) {
  const single = documentIds.length === 1;
  const [question, setQuestion] = useState("");
  const [fieldKey, setFieldKey] = useState<string>("");
  const [apply, setApply] = useState(true);
  const [modelId, setModelId] = useState("");
  const [models, setModels] = useState<{ id: number; name: string; model_name: string; default: boolean }[]>([]);
  const [fields, setFields] = useState<AiQueryField[]>([]);
  const [history, setHistory] = useState<AiQueryHistoryItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AiAnswer | null>(null);
  const [answers, setAnswers] = useState<AiAnswer[]>([]);
  const [job, setJob] = useState<AiQuestionState | null>(null);
  const [undoMsg, setUndoMsg] = useState<string | null>(null);
  const changed = useRef(false);

  const loadHistory = () => aiQuestionsApi.history().then(setHistory).catch(() => undefined);
  const loadAnswers = () => { if (single) aiQuestionsApi.answers(documentIds[0]).then(setAnswers).catch(() => undefined); };

  useEffect(() => {
    aiQuestionsApi.fields().then(setFields).catch(() => undefined);
    dbAssistantApi.models().then((m) => { setModels(m); setModelId(m.find((x) => x.default)?.id.toString() ?? ""); }).catch(() => undefined);
    loadHistory();
    loadAnswers();
    aiQuestionsApi.state().then((s) => { if (s.running) setJob(s); }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!job?.running) return;
    const t = setInterval(async () => {
      try {
        const s = await aiQuestionsApi.state();
        setJob(s);
        if (!s.running) { changed.current = true; loadHistory(); }
      } catch { /* nächster Takt */ }
    }, 2000);
    return () => clearInterval(t);
  }, [job?.running]);

  function pickHistory(id: string) {
    const h = history.find((x) => String(x.id) === id);
    if (!h) return;
    setQuestion(h.question);
    setFieldKey(h.field_key ?? "");
    setApply(!!h.field_key);
  }

  async function send() {
    if (question.trim().length < 3) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setUndoMsg(null);
    const base = { question: question.trim(), field_key: fieldKey || null, ai_config_id: modelId ? Number(modelId) : null, apply: apply && !!fieldKey };
    try {
      if (single) {
        const a = await aiQuestionsApi.ask({ document_id: documentIds[0], ...base });
        setResult(a);
        if (a.applied) changed.current = true;
        loadAnswers();
      } else {
        if (!confirm(`Diese Frage an ${documentIds.length} Beleg(e) stellen${base.apply ? " und die Belege mit den Antworten aktualisieren" : ""}?\n\nDie Belege werden nacheinander von der KI gelesen (bei lokalen Modellen kann das dauern).`)) { setBusy(false); return; }
        setJob(await aiQuestionsApi.run({ document_ids: documentIds, ...base }));
      }
      loadHistory();
    } catch (e) {
      setError(extractApiError(e, "Die KI hat nicht geantwortet"));
    } finally {
      setBusy(false);
    }
  }

  async function undo(runId: number) {
    setUndoMsg("Mache rückgängig …");
    try {
      await changeLogApi.undo(runId);
      for (let i = 0; i < 40; i++) {
        const st = await changeLogApi.undoState();
        if (!st.running) { setUndoMsg(`Rückgängig gemacht (${st.reverted ?? 0} Feld(er) zurückgesetzt).`); changed.current = true; loadAnswers(); return; }
        await new Promise((r) => setTimeout(r, 500));
      }
      setUndoMsg("Rückgängig läuft noch — Ergebnis im Änderungsprotokoll.");
    } catch (e) {
      setUndoMsg(extractApiError(e, "Rückgängig fehlgeschlagen"));
    }
  }

  function close() { if (changed.current) onChanged(); onClose(); }

  const fieldLabel = (k: string | null) => fields.find((f) => f.key === k)?.label ?? k ?? "";
  const pct = job?.total ? Math.round(((job.processed ?? 0) / job.total) * 100) : 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="ask-modal">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl">
        <div className="border-b px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">KI-Frage an {single ? "den Beleg" : `${documentIds.length} Belege`}</h2>
          <p className="mt-1 text-sm text-gray-500">
            Die KI antwortet nur mit dem gesuchten Wert (z. B. „Wie heißt die IBAN?“ → nur die IBAN). Mit Zielfeld wird der Beleg damit aktualisiert.
          </p>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4 text-sm">
          {history.length > 0 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">Letzte Abfragen</label>
              <select className="w-full rounded border border-gray-300 bg-white px-2 py-1.5" value="" onChange={(e) => pickHistory(e.target.value)} data-testid="ask-history">
                <option value="">— frühere Frage wieder verwenden —</option>
                {history.map((h) => (
                  <option key={h.id} value={h.id}>{h.question}{h.field_key ? ` → ${fieldLabel(h.field_key)}` : ""} ({h.use_count}×)</option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Frage</label>
            <textarea rows={2} className="w-full rounded border border-gray-300 px-2 py-1.5" value={question} onChange={(e) => setQuestion(e.target.value)}
              placeholder="z. B. Wie heißt die IBAN des Lieferanten?" data-testid="ask-question" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">Beleg aktualisieren: Feld</label>
              <select className="w-full rounded border border-gray-300 bg-white px-2 py-1.5" value={fieldKey}
                onChange={(e) => { setFieldKey(e.target.value); setApply(!!e.target.value); }} data-testid="ask-field">
                <option value="">Kein Feld — nur Antwort speichern</option>
                {fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">KI-Konfiguration</label>
              <select className="w-full rounded border border-gray-300 bg-white px-2 py-1.5" value={modelId} onChange={(e) => setModelId(e.target.value)}>
                {models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.model_name})</option>)}
              </select>
            </div>
          </div>
          {fieldKey && (
            <label className="flex items-center gap-2 text-gray-700">
              <input type="checkbox" checked={apply} onChange={(e) => setApply(e.target.checked)} />
              Beleg mit der Antwort aktualisieren <span className="text-xs text-gray-400">(im Änderungsprotokoll, rückgängig machbar)</span>
            </label>
          )}
          {error && <div className="rounded border border-red-200 bg-red-50 p-2 text-red-700">{error}</div>}

          {result && (
            <div className={`rounded border p-3 ${result.valid ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="ask-result">
              <div className="text-xs text-gray-500">Antwort der KI</div>
              <div className="font-mono text-base text-gray-900">{result.answer ?? "—"}</div>
              {result.note && <div className="mt-1 text-xs text-amber-800">{result.note}</div>}
              {result.applied && (
                <div className="mt-1 text-xs text-emerald-800">
                  ✓ Beleg aktualisiert{result.field_key ? ` (${fieldLabel(result.field_key)})` : ""}
                  {result.old_value ? <> · vorher: <span className="font-mono">{result.old_value}</span></> : null}
                  {result.change_run_id && (
                    <button className="ml-3 rounded border border-gray-300 bg-white px-2 py-0.5 text-gray-700 hover:bg-gray-50" onClick={() => undo(result.change_run_id!)}>Rückgängig</button>
                  )}
                </div>
              )}
              {undoMsg && <div className="mt-1 text-xs text-gray-600">{undoMsg}</div>}
            </div>
          )}

          {job && (
            <div className="rounded border border-gray-200 bg-gray-50 p-3" data-testid="ask-job">
              <div className="mb-1 flex justify-between text-xs text-gray-600">
                <span>{job.running ? "Läuft …" : job.cancelled ? "Abgebrochen" : "Fertig"} — „{job.question}“</span>
                <span>{(job.processed ?? 0)} / {job.total ?? 0} ({pct} %)</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-gray-200"><div className="h-2 rounded-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} /></div>
              <div className="mt-2 text-xs text-gray-700">
                gefunden {job.found ?? 0} · nicht gefunden {job.not_found ?? 0} · am Beleg übernommen {job.applied ?? 0} · Fehler {job.errors ?? 0}
              </div>
              {job.running && <button className="mt-2 rounded border border-red-300 px-3 py-1 text-xs text-red-700 hover:bg-red-50" onClick={() => aiQuestionsApi.cancel()}>Abbrechen</button>}
              {!job.running && (job.results?.length ?? 0) > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-gray-600">Einzelergebnisse</summary>
                  <ul className="mt-1 max-h-40 overflow-y-auto">
                    {job.results!.map((r, i) => (
                      <li key={i} className="flex gap-2 border-t border-gray-100 py-0.5">
                        <a className="text-blue-600 hover:underline" href={`/belege?doc_id=${r.document_id}`} target="_blank" rel="noreferrer">#{r.document_id}</a>
                        <span className="font-mono">{r.answer ?? "—"}</span>
                        <span className="text-gray-400">{r.applied ? "✓ übernommen" : r.note ?? ""}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <p className="mt-1 text-[11px] text-gray-400">Rückgängig machen: Einstellungen → Automatisierung → Änderungsprotokoll.</p>
            </div>
          )}

          {single && answers.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer font-medium text-gray-600">Bisherige Antworten zu diesem Beleg ({answers.length})</summary>
              <ul className="mt-1 space-y-1">
                {answers.map((a) => (
                  <li key={a.id} className="rounded border border-gray-100 p-1.5">
                    <div className="text-gray-500">{a.question}{a.field_key ? ` → ${fieldLabel(a.field_key)}` : ""} · {a.created_at ? new Date(a.created_at).toLocaleString("de-DE") : ""}</div>
                    <div className="font-mono text-gray-900">{a.answer ?? "—"} {a.applied && <span className="font-sans text-emerald-700">✓ übernommen</span>}</div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t px-6 py-3">
          <button onClick={close} className="rounded border border-gray-300 px-4 py-2 text-gray-700 hover:bg-gray-50">Schließen</button>
          <button onClick={send} disabled={busy || job?.running || question.trim().length < 3} data-testid="ask-send"
            className="rounded bg-violet-600 px-4 py-2 font-medium text-white hover:bg-violet-700 disabled:opacity-50">
            {busy ? "KI liest …" : "Frage stellen"}
          </button>
        </div>
      </div>
    </div>
  );
}
