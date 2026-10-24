"use client";

import { useEffect, useState } from "react";
import { Alert, Badge, Modal, Spinner } from "@/components/ui";
import { RuleCheckSummary, RuleResult, extractApiError, validationRulesApi } from "@/lib/api";

const RESULT_BADGE: Record<string, { tone: "green" | "red" | "gray"; label: string }> = {
  ok: { tone: "green", label: "stimmt" },
  failed: { tone: "red", label: "weicht ab" },
  skipped: { tone: "gray", label: "nicht prüfbar" },
};

export const RULE_STATUS: Record<string, { tone: "green" | "amber" | "red" | "gray"; label: string; hint: string }> = {
  ok: { tone: "green", label: "Regeln OK", hint: "Alle Prüfregeln stimmen" },
  warn: { tone: "amber", label: "Hinweis", hint: "Eine Prüfregel mit Schweregrad „Hinweis“ weicht ab" },
  needs_review: { tone: "red", label: "Prüfen!", hint: "Eine Prüfregel weicht ab — bitte prüfen" },
  unchecked: { tone: "gray", label: "nicht prüfbar", hint: "Keine Regel konnte ausgewertet werden (Werte fehlen oder keine Regel aktiv)" },
};

export function ResultTable({ results }: { results: RuleResult[] }) {
  if (!results.length) return <p className="hint">Keine Ergebnisse — es gibt keine aktive Regel für die Datensätze dieses Belegs.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="table-ra">
        <thead><tr><th>Regel</th><th>Datensatz</th><th>Links</th><th>Rechts</th><th>Abweichung</th><th>Ergebnis</th></tr></thead>
        <tbody>
          {results.map((r, i) => (
            <tr key={i}>
              <td>{r.name}</td>
              <td>{r.entity}{r.entity !== "beleg" ? ` ${r.position_index + 1}` : ""}</td>
              <td>{r.left ?? "–"}</td><td>{r.right ?? "–"}</td><td>{r.diff ?? "–"}</td>
              <td>
                <Badge tone={RESULT_BADGE[r.status].tone}>{RESULT_BADGE[r.status].label}</Badge>
                {r.message && r.status !== "ok" && <div className="hint mt-1">{r.message}</div>}
                {r.status === "ok" && r.variant && r.variant > 1 && <div className="hint mt-1">über Variante {r.variant}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Kleines Ergebnis-Etikett für die Belegliste; Klick öffnet die Einzelergebnisse. */
export function RuleBadge({ docId, status, failed, skipped }: {
  docId: number; status?: string | null; failed?: number | null; skipped?: number | null;
}) {
  const [open, setOpen] = useState(false);
  if (!status) return <span className="text-gray-400">–</span>;
  const s = RULE_STATUS[status];
  if (!s) return <span className="text-gray-400">{status}</span>;
  const detail = [failed ? `${failed} weicht ab` : null, skipped ? `${skipped} nicht prüfbar` : null].filter(Boolean).join(" · ");
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} title={`${s.hint}${detail ? " — " + detail : ""} · Klick für Details`}>
        <Badge tone={s.tone}>{s.label}{failed ? ` (${failed})` : ""}</Badge>
      </button>
      {open && <RuleChecksModal docId={docId} onClose={() => setOpen(false)} />}
    </>
  );
}

export function RuleChecksModal({ docId, onClose }: { docId: number; onClose: () => void }) {
  const [data, setData] = useState<{ summary: RuleCheckSummary; results: RuleResult[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    validationRulesApi.document(docId)
      .then((r) => { if (alive) setData(r); })
      .catch((err) => { if (alive) setError(extractApiError(err, "Prüfung konnte nicht geladen werden")); });
    return () => { alive = false; };
  }, [docId]);
  return (
    <Modal title={`Prüfregeln — Beleg ${docId}`} onClose={onClose} wide footer={<a className="btn btn-secondary" href="/pruefregeln">Regeln bearbeiten</a>}>
      {error && <Alert tone="error">{error}</Alert>}
      {!data && !error && <Spinner />}
      {data && <ResultTable results={data.results} />}
    </Modal>
  );
}

/** Infos-Ansicht eines Belegs: Gesamtergebnis der Prüfregeln + Einzelergebnisse (nur bei Belegen mit Regel-Ergebnissen). */
export function RuleChecksPanel({ docId }: { docId: number }) {
  const [data, setData] = useState<{ summary: RuleCheckSummary; results: RuleResult[] } | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    setData(null);
    validationRulesApi.document(docId).then((r) => { if (alive) setData(r); }).catch(() => { if (alive) setData(null); });
    return () => { alive = false; };
  }, [docId]);
  if (!data || !data.results.length) return null;
  const s = RULE_STATUS[data.summary.status] ?? RULE_STATUS.unchecked;
  const bad = data.results.filter((r) => r.status !== "ok");
  const box = data.summary.status === "needs_review" ? "border-red-200 bg-red-50"
    : data.summary.status === "warn" ? "border-amber-200 bg-amber-50"
    : data.summary.status === "ok" ? "border-green-200 bg-green-50" : "border-gray-200 bg-gray-50";
  return (
    <div className={`mb-6 rounded-lg border p-4 ${box}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-700">Prüfregeln</h3>
        <Badge tone={s.tone}>{s.label}</Badge>
        <span className="text-xs text-gray-500">
          {data.summary.ok} stimmen · {data.summary.failed} weichen ab · {data.summary.skipped} nicht prüfbar
        </span>
        <button type="button" className="btn btn-ghost ml-auto !py-0.5 text-xs" onClick={() => setOpen((o) => !o)}>
          {open ? "Nur Auffälliges" : "Alle Ergebnisse"}
        </button>
      </div>
      <div className="mt-3"><ResultTable results={open ? data.results : bad} /></div>
      {!open && !bad.length && <p className="hint">Alle Regeln stimmen.</p>}
    </div>
  );
}
