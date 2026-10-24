"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Badge, Card, EmptyState, Icon, Modal, PageHeader, Spinner } from "@/components/ui";
import { ResultTable } from "@/components/belege/RuleChecks";
import {
  RuleMeta, RuleResult, ValidationRule, ValidationRuleInput, extractApiError, validationRulesApi,
} from "@/lib/api";

const EMPTY: ValidationRuleInput = {
  name: "", description: "", entity_name: "", left_expr: "", alt_left_exprs: [], right_expr: "", comparator: "=",
  tolerance_abs: 0.02, tolerance_pct: 0, severity: "error", enabled: true,
};

function RuleModal({ rule, meta, onClose, onSaved }: {
  rule: ValidationRule | null; meta: RuleMeta; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<ValidationRuleInput>(rule ? { ...rule } : { ...EMPTY, entity_name: meta.entities.find((e) => e.has_parent)?.name ?? meta.entities[0]?.name ?? "" });
  const [target, setTarget] = useState<"left_expr" | "right_expr" | number>("left_expr");      // Zahl = Variante (0-basiert)
  const [docId, setDocId] = useState("");
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<RuleResult[] | null>(null);
  const refs = { left_expr: useRef<HTMLInputElement>(null), right_expr: useRef<HTMLInputElement>(null) };
  const altRefs = useRef<(HTMLInputElement | null)[]>([]);

  const entity = meta.entities.find((e) => e.name === form.entity_name);
  const parent = meta.entities.find((e) => e.children.includes(form.entity_name));
  const set = <K extends keyof ValidationRuleInput>(k: K, v: ValidationRuleInput[K]) => setForm((f) => ({ ...f, [k]: v }));

  function insert(text: string) {
    const el = typeof target === "number" ? altRefs.current[target] : refs[target].current;
    const cur = typeof target === "number" ? form.alt_left_exprs[target] ?? "" : form[target];
    const start = el?.selectionStart ?? cur.length;
    const end = el?.selectionEnd ?? cur.length;
    const next = cur.slice(0, start) + text + cur.slice(end);
    if (typeof target === "number") set("alt_left_exprs", form.alt_left_exprs.map((a, i) => (i === target ? next : a)));
    else set(target, next);
    requestAnimationFrame(() => { el?.focus(); const pos = start + text.length; el?.setSelectionRange(pos, pos); });
  }

  async function test() {
    setTesting(true); setError(null); setResults(null);
    try {
      const r = await validationRulesApi.test(form, Number(docId));
      if (r.problems.length) setError(r.problems.join("; ")); else setResults(r.results);
    } catch (err) { setError(extractApiError(err, "Test fehlgeschlagen")); } finally { setTesting(false); }
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      if (rule) await validationRulesApi.update(rule.id, form); else await validationRulesApi.create(form);
      onSaved();
    } catch (err) { setError(extractApiError(err, "Speichern fehlgeschlagen")); } finally { setSaving(false); }
  }

  const exprInput = (key: "left_expr" | "right_expr", label: string) => (
    <div>
      <label className="label">{label}</label>
      <input ref={refs[key]} className="input font-mono" value={form[key]} onFocus={() => setTarget(key)}
        onChange={(e) => set(key, e.target.value)} placeholder="z. B. menge * einzelpreis" spellCheck={false} />
    </div>
  );

  return (
    <Modal title={rule ? "Prüfregel bearbeiten" : "Neue Prüfregel"} onClose={onClose} wide
      footer={<>
        <button className="btn btn-secondary" onClick={onClose}>Abbrechen</button>
        <button className="btn btn-primary" disabled={saving || !form.name || !form.left_expr || !form.right_expr || !form.entity_name} onClick={save}>
          {saving ? "Speichert…" : "Speichern"}
        </button>
      </>}>
      <div className="space-y-4">
        {error && <Alert tone="error">{error}</Alert>}
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className="label">Name</label><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></div>
          <div>
            <label className="label">Geprüft wird je …</label>
            <select className="input" value={form.entity_name} onChange={(e) => set("entity_name", e.target.value)}>
              {meta.entities.map((e) => <option key={e.name} value={e.name}>{e.display_name} ({e.name})</option>)}
            </select>
            <p className="hint mt-1">Die Regel läuft einmal für jeden Datensatz dieser Art (z. B. für jede Position).</p>
          </div>
        </div>

        <div className="rounded-lg border border-gray-200 p-3 space-y-3">
          <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr] items-end">
            {exprInput("left_expr", "Linke Seite")}
            <select className="input w-20 text-center" value={form.comparator} onChange={(e) => set("comparator", e.target.value)}
              title="= gleich · ≤ höchstens · ≥ mindestens">
              {meta.comparators.map((c) => <option key={c} value={c}>{c === "<=" ? "≤" : c === ">=" ? "≥" : "="}</option>)}
            </select>
            {exprInput("right_expr", "Rechte Seite")}
          </div>

          <div>
            <p className="label">Varianten der linken Seite (optional — die Regel gilt als erfüllt, sobald eine Variante passt)</p>
            {form.alt_left_exprs.map((a, i) => (
              <div key={i} className="mb-1.5 flex gap-2">
                <span className="w-20 shrink-0 pt-2 text-xs text-gray-500">Variante {i + 2}</span>
                <input ref={(el) => { altRefs.current[i] = el; }} className="input font-mono" value={a} spellCheck={false}
                  onFocus={() => setTarget(i)} onChange={(e) => set("alt_left_exprs", form.alt_left_exprs.map((x, j) => (j === i ? e.target.value : x)))} />
                <button type="button" className="btn btn-ghost" aria-label="Variante entfernen" onClick={() => set("alt_left_exprs", form.alt_left_exprs.filter((_, j) => j !== i))}><Icon name="trash" /></button>
              </div>
            ))}
            <button type="button" className="btn btn-secondary !py-0.5 !px-2 text-xs" onClick={() => set("alt_left_exprs", [...form.alt_left_exprs, ""])}>
              <Icon name="plus" /> Variante hinzufügen
            </button>
            <p className="hint mt-1">Beispiel: Einzelpreis je Einheit wie gedruckt, je Tonne (<code>menge.t</code>) oder je Verpackung (<code>menge / COALESCE(verpackungsinhalt; 1)</code>).</p>
          </div>

          <div>
            <p className="label">Felder einfügen (in das zuletzt angeklickte Feld)</p>
            <div className="flex flex-wrap gap-1.5">
              {entity?.fields.map((f) => (
                <button key={f.name} type="button" className="btn btn-secondary !py-0.5 !px-2 text-xs" title={f.display_name} onClick={() => insert(f.name)}>
                  {f.name}{f.data_type === "quantity" ? " (Menge)" : ""}
                </button>
              ))}
              {entity?.children.map((child) => {
                const ce = meta.entities.find((e) => e.name === child);
                return ce?.fields.map((f) => (
                  <button key={child + f.name} type="button" className="btn btn-ghost !py-0.5 !px-2 text-xs border border-dashed border-gray-300" onClick={() => insert(`SUM(${child}.${f.name})`)}>
                    Summe {child}.{f.name}
                  </button>
                ));
              })}
              {meta.price_unit_field && entity?.fields.some((f) => f.data_type === "quantity") && entity.fields.some((f) => f.name === "menge") && (
                <button type="button" className="btn btn-ghost !py-0.5 !px-2 text-xs border border-dashed border-gray-300"
                  title="Menge in der Einheit, auf die sich der Einzelpreis bezieht" onClick={() => insert("menge.@preiseinheit")}>
                  menge.@preiseinheit
                </button>
              )}
              {parent?.fields.map((f) => (
                <button key={"p" + f.name} type="button" className="btn btn-ghost !py-0.5 !px-2 text-xs border border-dashed border-gray-300" onClick={() => insert(`eltern.${f.name}`)}>
                  eltern.{f.name}
                </button>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {["+", "-", "*", "/", "(", ")"].map((o) => <button key={o} type="button" className="btn btn-secondary !py-0.5 !px-2 text-xs font-mono" onClick={() => insert(` ${o} `)}>{o}</button>)}
              <span className="hint ml-2">Funktionen:</span>
              {meta.functions.map((fn) => (
                <button key={fn.name} type="button" className="btn btn-ghost !py-0.5 !px-2 text-xs border border-gray-200" title={`${fn.text} — ${fn.example}`} onClick={() => insert(fn.example)}>
                  {fn.name}
                </button>
              ))}
            </div>
            <p className="hint mt-2">
              Mengenfelder zählen mit Wert und Einheit <b>wie gedruckt</b> (nichts wird umgestellt). Nur wenn du es hinschreibst, rechnet die Formel über die Einheiten-Gruppen um:
              {" "}<code>menge.t</code>, <code>menge.kg</code> (verfügbar: {meta.units.slice(0, 12).join(", ")}{meta.units.length > 12 ? " …" : ""}) oder <code>menge.basis</code>.
              {meta.price_unit_field && <> Mit <code>menge.@preiseinheit</code> wird die Menge in die Einheit umgerechnet, auf die sich der Einzelpreis laut Rechnung bezieht (Verpackungseinheit: Menge ÷ Inhalt je Verpackung).</>}
              Dezimalzahlen mit Punkt, Argumente mit Semikolon. Fehlt ein Wert, wird die Regel als „nicht prüfbar“ gemeldet — mit <code>COALESCE(feld; 1)</code> kannst du einen Ersatzwert festlegen.
            </p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-4">
          <div><label className="label">Toleranz (absolut)</label><input type="number" step="0.01" min="0" className="input" value={form.tolerance_abs} onChange={(e) => set("tolerance_abs", Number(e.target.value))} /></div>
          <div><label className="label">Toleranz (%)</label><input type="number" step="0.01" min="0" className="input" value={form.tolerance_pct} onChange={(e) => set("tolerance_pct", Number(e.target.value))} /></div>
          <div>
            <label className="label">Bei Abweichung</label>
            <select className="input" value={form.severity} onChange={(e) => set("severity", e.target.value as "warn" | "error")}>
              <option value="error">Beleg „Prüfen!“</option>
              <option value="warn">nur Hinweis</option>
            </select>
          </div>
          <label className="flex items-end gap-2 pb-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> aktiv</label>
        </div>
        <div><label className="label">Beschreibung (optional)</label><input className="input" value={form.description ?? ""} onChange={(e) => set("description", e.target.value)} /></div>

        <div className="rounded-lg bg-gray-50 p-3">
          <p className="label">Ausprobieren an einem Beleg</p>
          <div className="flex gap-2">
            <input className="input w-40" placeholder="Beleg-ID" value={docId} onChange={(e) => setDocId(e.target.value.replace(/\D/g, ""))} />
            <button className="btn btn-secondary" disabled={!docId || testing || !form.left_expr || !form.right_expr} onClick={test}>{testing ? "Rechnet…" : "Testen"}</button>
          </div>
          {results && <div className="mt-3"><ResultTable results={results} /></div>}
        </div>
      </div>
    </Modal>
  );
}

export default function RulesPage() {
  const [rules, setRules] = useState<ValidationRule[]>([]);
  const [meta, setMeta] = useState<RuleMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [edit, setEdit] = useState<ValidationRule | "new" | null>(null);
  const [running, setRunning] = useState(false);
  const [docId, setDocId] = useState("");
  const [priceUnitBusy, setPriceUnitBusy] = useState(false);
  const [docResults, setDocResults] = useState<{ status: string; results: RuleResult[] } | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [r, m] = await Promise.all([validationRulesApi.list(), validationRulesApi.meta()]);
      setRules(r); setMeta(m);
    } catch (err) { setError(extractApiError(err, "Prüfregeln konnten nicht geladen werden")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); await load(); } catch (err) { setError(extractApiError(err, "Aktion fehlgeschlagen")); }
  }

  async function runAll() {
    if (!confirm("Alle Belege mit den aktiven Regeln neu prüfen und das Ergebnis speichern?")) return;
    setRunning(true); setError(null); setInfo(null);
    try {
      const r = await validationRulesApi.run();
      setInfo(`${r.documents} Belege mit ${r.rules} Regeln geprüft: ${r.counts.ok} in Ordnung, ${r.counts.warn} mit Hinweis, ${r.counts.needs_review} „Prüfen!“, ${r.counts.unchecked} nicht prüfbar.` +
        (r.flagged_sample.length ? ` Auffällig (Beleg-IDs, Auszug): ${r.flagged_sample.join(", ")}` : ""));
    } catch (err) { setError(extractApiError(err, "Prüfung fehlgeschlagen")); } finally { setRunning(false); }
  }

  async function enablePriceUnit() {
    if (!confirm("Das aktive Schema bekommt ein neues Feld „Preiseinheit“ an der Position (neue Schema-Version wird aktiviert). Fortfahren?")) return;
    setPriceUnitBusy(true); setError(null); setInfo(null);
    try {
      const r = await validationRulesApi.enablePriceUnit();
      setInfo(r.changed ? `Feld „Preiseinheit“ ergänzt (Schema-Version ${r.schema_version})${r.rules_updated ? " — Standardregel „Positionspreis“ umgestellt" : ""}.` : "Das Feld gibt es schon.");
      await load();
    } catch (err) { setError(extractApiError(err, "Feld konnte nicht ergänzt werden")); } finally { setPriceUnitBusy(false); }
  }

  async function showDoc() {
    setError(null); setDocResults(null);
    try {
      const r = await validationRulesApi.document(Number(docId));
      setDocResults({ status: r.summary.status, results: r.results });
    } catch (err) { setError(extractApiError(err, "Beleg konnte nicht geprüft werden")); }
  }

  const entityLabel = useMemo(() => Object.fromEntries((meta?.entities ?? []).map((e) => [e.name, e.display_name])), [meta]);

  return (
    <div>
      <PageHeader title="Prüfregeln" subtitle="Rechenregeln, mit denen jeder gelesene Beleg gegengeprüft wird — z. B. Menge × Preis = Positionspreis, Summe der Positionen = Netto, Netto + Steuer = Brutto."
        actions={<>
          <button className="btn btn-secondary" disabled={running || !rules.length} onClick={runAll}>{running ? "Prüft…" : "Alle Belege neu prüfen"}</button>
          <button className="btn btn-primary" disabled={!meta?.entities.length} onClick={() => setEdit("new")}><Icon name="plus" /> Neue Regel</button>
        </>} />
      {error && <Alert tone="error">{error}</Alert>}
      {info && <Alert tone="success">{info}</Alert>}
      {meta && meta.entities.some((e) => e.name === "position") && !meta.price_unit_field && (
        <Alert tone="info">
          <div className="flex flex-wrap items-center gap-3">
            <span>
              Im Schema fehlt das Feld <b>Preiseinheit</b> (worauf sich der Einzelpreis bezieht: t, kg, VPE …). Mit dem Feld kann die Positionsregel genau rechnen,
              statt mehrere Varianten zu probieren. Neue Auslesungen fragen die KI dann danach; bestehende Belege bleiben unverändert.
            </span>
            <button className="btn btn-secondary" disabled={priceUnitBusy} onClick={enablePriceUnit}>
              {priceUnitBusy ? "Ergänzt…" : "Feld im Schema ergänzen"}
            </button>
          </div>
        </Alert>
      )}
      {meta && !meta.entities.length && <Alert tone="warn">Es gibt noch kein aktives Schema — Regeln beziehen sich auf dessen Felder (Seite „Schema“).</Alert>}

      <Card>
        {loading && !rules.length ? <Spinner /> : !rules.length ? (
          <EmptyState title="Noch keine Prüfregeln"
            text="Ohne Regel wird nichts gegengerechnet. Du kannst eigene anlegen oder die Standardregeln (Positionspreis, Summe, Brutto, Steuer) einspielen."
            action={<button className="btn btn-primary" onClick={() => act(async () => { const r = await validationRulesApi.seedDefaults(); setInfo(r.created ? `${r.created} Standardregeln angelegt` : "Die Standardregeln passen nicht zum aktuellen Schema."); })}>Standardregeln anlegen</button>} />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-ra">
              <thead><tr><th>Aktiv</th><th>Regel</th><th>Je</th><th>Formel</th><th>Toleranz</th><th>Bei Abweichung</th><th></th></tr></thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id} className={r.enabled ? "" : "opacity-60"}>
                    <td><input type="checkbox" checked={r.enabled} onChange={() => act(() => validationRulesApi.update(r.id, { ...r, enabled: !r.enabled }))} aria-label="aktiv" /></td>
                    <td>
                      <div className="font-medium">{r.name}</div>
                      {r.description && <div className="hint">{r.description}</div>}
                      {r.problems.map((p) => <div key={p} className="text-xs text-red-600">⚠ {p}</div>)}
                    </td>
                    <td>{entityLabel[r.entity_name] ?? r.entity_name}</td>
                    <td className="font-mono text-xs"><div>{r.left_expr}</div>{r.alt_left_exprs.map((a, i) => <div key={i} className="text-gray-600">oder {a}</div>)}<div className="text-gray-500">{r.comparator === "<=" ? "≤" : r.comparator === ">=" ? "≥" : "="} {r.right_expr}</div></td>
                    <td className="whitespace-nowrap">{r.tolerance_abs ? `${r.tolerance_abs} abs.` : ""}{r.tolerance_abs && r.tolerance_pct ? " / " : ""}{r.tolerance_pct ? `${r.tolerance_pct} %` : ""}{!r.tolerance_abs && !r.tolerance_pct ? "exakt" : ""}</td>
                    <td><Badge tone={r.severity === "error" ? "red" : "amber"}>{r.severity === "error" ? "Prüfen!" : "Hinweis"}</Badge></td>
                    <td className="whitespace-nowrap text-right">
                      <button className="btn btn-ghost" onClick={() => setEdit(r)} aria-label="Bearbeiten"><Icon name="edit" /></button>
                      <button className="btn btn-ghost" aria-label="Löschen" onClick={() => { if (confirm(`Regel „${r.name}“ löschen?`)) act(() => validationRulesApi.remove(r.id)); }}><Icon name="trash" /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="mt-4">
        <h2 className="mb-2 font-semibold">Beleg prüfen</h2>
        <div className="flex gap-2">
          <input className="input w-40" placeholder="Beleg-ID" value={docId} onChange={(e) => setDocId(e.target.value.replace(/\D/g, ""))} />
          <button className="btn btn-secondary" disabled={!docId} onClick={showDoc}>Alle aktiven Regeln auswerten</button>
        </div>
        {docResults && <div className="mt-3"><p className="mb-2 text-sm">Gesamtergebnis: <b>{{ ok: "in Ordnung", warn: "Hinweise", needs_review: "Prüfen!", unchecked: "nicht prüfbar" }[docResults.status]}</b></p><ResultTable results={docResults.results} /></div>}
      </Card>

      {edit && meta && <RuleModal rule={edit === "new" ? null : edit} meta={meta} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}
