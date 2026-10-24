"use client";

/**
 * Editor für die Normalisierer-Kette eines Feldes.
 *
 * - Bausteine aus der Registry (GET /api/schema/normalizers) hinzufügen
 * - Reihenfolge per Drag & Drop (HTML5) ODER per Pfeiltasten-Buttons (barrierefrei) ändern
 * - Parameter je Baustein aus dem JSON-Schema des Backends
 * - Live-Vorschau: Beispielwert durch die Kette (POST /api/schema/normalizers/preview)
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { NormalizerStep, RegistryItem, schemaApi } from "@/lib/api";
import { Icon } from "@/components/ui";

type ParamSpec = NonNullable<NonNullable<RegistryItem["params_schema"]>["properties"]>[string];

function ParamInput({ spec, value, disabled, onChange }: { spec: ParamSpec; value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
  const v = value ?? spec.default ?? "";
  if (spec.type === "boolean") {
    return <input type="checkbox" disabled={disabled} checked={Boolean(v)} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (spec.enum) {
    return (
      <select className="input py-1" disabled={disabled} value={String(v)} onChange={(e) => onChange(e.target.value)}>
        {spec.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    );
  }
  const numeric = spec.type === "integer" || spec.type === "number";
  return (
    <input className="input py-1" disabled={disabled} type={numeric ? "number" : "text"} value={String(v)}
      onChange={(e) => onChange(numeric ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)} />
  );
}

export default function NormalizerChain({
  chain, onChange, readOnly = false, sampleHint,
}: {
  chain: NormalizerStep[]; onChange: (c: NormalizerStep[]) => void; readOnly?: boolean; sampleHint?: string;
}) {
  const [registry, setRegistry] = useState<RegistryItem[]>([]);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [sample, setSample] = useState(sampleHint ?? "");
  const [preview, setPreview] = useState<{ ok: boolean; value: unknown; errors: string[] } | null>(null);
  const reqId = useRef(0);

  useEffect(() => { schemaApi.normalizers().then(setRegistry).catch(() => setRegistry([])); }, []);
  const byName = useMemo(() => Object.fromEntries(registry.map((r) => [r.name, r])), [registry]);

  // Vorschau entprellt
  useEffect(() => {
    if (sample === "") { setPreview(null); return; }
    const id = ++reqId.current;
    const t = setTimeout(() => {
      schemaApi.preview(sample, chain).then((r) => { if (id === reqId.current) setPreview(r); }).catch(() => { /* Vorschau ist optional */ });
    }, 300);
    return () => clearTimeout(t);
  }, [sample, chain]);

  function move(from: number, to: number) {
    if (from === to || to < 0 || to >= chain.length) return;
    const next = [...chain];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  }

  function setParam(i: number, key: string, val: unknown) {
    onChange(chain.map((s, idx) => (idx === i ? { ...s, params: { ...s.params, [key]: val } } : s)));
  }

  return (
    <div className="space-y-3">
      {chain.length === 0 && <p className="rounded-lg border border-dashed border-gray-300 px-3 py-4 text-center text-sm text-gray-500">Keine Normalisierer — der Wert wird so übernommen, wie ihn die KI liefert.</p>}
      <ol className="space-y-2">
        {chain.map((step, i) => {
          const meta = byName[step.name];
          const props = meta?.params_schema?.properties ?? {};
          return (
            <li key={`${step.name}-${i}`}
              draggable={!readOnly}
              onDragStart={() => setDragFrom(i)}
              onDragOver={(e) => { e.preventDefault(); setDragOver(i); }}
              onDragEnd={() => { setDragFrom(null); setDragOver(null); }}
              onDrop={() => { if (dragFrom !== null) move(dragFrom, i); setDragFrom(null); setDragOver(null); }}
              className={`rounded-lg border bg-white p-3 ${dragOver === i && dragFrom !== null ? "border-blue-500 ring-2 ring-blue-100" : "border-gray-200"}`}>
              <div className="flex items-center gap-2">
                {!readOnly && <span className="cursor-grab text-gray-400" title="Ziehen zum Sortieren"><Icon name="grip" /></span>}
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900">{meta?.label ?? step.name}</p>
                  {meta?.description && <p className="truncate text-xs text-gray-500" title={meta.description}>{meta.description}</p>}
                </div>
                {!readOnly && (
                  <div className="flex items-center gap-0.5">
                    <button type="button" className="btn btn-ghost btn-sm" aria-label="Nach oben" disabled={i === 0} onClick={() => move(i, i - 1)}>↑</button>
                    <button type="button" className="btn btn-ghost btn-sm" aria-label="Nach unten" disabled={i === chain.length - 1} onClick={() => move(i, i + 1)}>↓</button>
                    <button type="button" className="btn btn-ghost btn-sm text-red-600" aria-label="Baustein entfernen" onClick={() => onChange(chain.filter((_, idx) => idx !== i))}><Icon name="trash" /></button>
                  </div>
                )}
              </div>
              {Object.keys(props).length > 0 && (
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {Object.entries(props).map(([key, spec]) => (
                    <label key={key} className="block text-xs text-gray-600">
                      {spec.title ?? key}
                      <ParamInput spec={spec} value={step.params?.[key]} disabled={readOnly} onChange={(v) => setParam(i, key, v)} />
                    </label>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {!readOnly && (
        <select className="input" value="" aria-label="Normalisierer hinzufügen"
          onChange={(e) => { if (e.target.value) onChange([...chain, { name: e.target.value, params: {} }]); }}>
          <option value="">+ Normalisierer hinzufügen …</option>
          {registry.map((r) => <option key={r.name} value={r.name}>{r.label ?? r.name}</option>)}
        </select>
      )}

      <div className="rounded-lg bg-gray-50 p-3">
        <label className="label" htmlFor="chain-sample">Vorschau mit Beispielwert</label>
        <input id="chain-sample" className="input" value={sample} onChange={(e) => setSample(e.target.value)} placeholder="z. B.  1.234,56 €" />
        {preview && (
          <p className={`mt-2 text-sm ${preview.ok ? "text-gray-800" : "text-red-700"}`}>
            {preview.errors.length ? preview.errors.join("; ") : <>Ergebnis: <code className="rounded bg-white px-1.5 py-0.5 font-mono text-xs">{JSON.stringify(preview.value)}</code>{!preview.ok && " (Fehler in der Kette)"}</>}
          </p>
        )}
      </div>
    </div>
  );
}
