"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Pager from "@/components/common/Pager";
import { fetchAllPages, matchesWords } from "@/lib/loadAll";
import {
  ImportBatch, PositionFilter, PositionHit, PositionSort, PositionSummary, UNIT_TYPE_LABELS,
  documentsApi, extractApiError, importsApi, positionsApi,
} from "@/lib/api";

// ─── Formular ↔ Filter ↔ URL ──────────────────────────────────────────────────

/** Alle Eingabefelder als Text; Schlüssel = API-Parameter (und URL-Parameter) */
const EMPTY_FORM = {
  q: "", article_number: "", supplier_name: "", company: "", year: "", batch_id: "", unit: "", unit_group: "",
  invoice_number: "", unit_price_min: "", unit_price_max: "", total_price_min: "", total_price_max: "",
  quantity_min: "", quantity_max: "", date_from: "", date_to: "", document_id: "", include_hidden: "",
};
type Form = typeof EMPTY_FORM;

const NUMERIC: (keyof Form)[] = [
  "year", "unit_price_min", "unit_price_max", "total_price_min", "total_price_max",
  "quantity_min", "quantity_max", "document_id",
];

function toFilter(form: Form): PositionFilter {
  const f: Record<string, unknown> = {};
  (Object.keys(form) as (keyof Form)[]).forEach((k) => {
    const raw = form[k].trim();
    if (!raw) return;
    if (k === "batch_id") f.batch_ids = [Number(raw)];
    else if (k === "supplier_name") { f.supplier_name = raw; f.supplier_exact = true; }   // aus der Auswahlliste → exakt
    else if (k === "include_hidden") f.include_hidden = true;
    else if (NUMERIC.includes(k)) {
      const n = Number(raw.replace(",", "."));
      if (Number.isFinite(n)) f[k] = n;
    } else f[k] = raw;
  });
  return f as PositionFilter;
}

/** Hält die Adresszeile synchron (teilbar, Neuladen-sicher) */
function syncUrl(form: Form) {
  const url = new URL(window.location.href);
  (Object.keys(EMPTY_FORM) as (keyof Form)[]).forEach((k) => url.searchParams.delete(k));
  (Object.keys(form) as (keyof Form)[]).forEach((k) => { if (form[k].trim()) url.searchParams.set(k, form[k].trim()); });
  window.history.replaceState(null, "", url.pathname + url.search);
}

// ─── Hilfen ───────────────────────────────────────────────────────────────────

const eur = (v: number | string | null | undefined, max = 2) =>
  v == null || v === ""
    ? "–"
    : new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: max }).format(Number(v));
const num = (v: number | string | null | undefined) =>
  v == null || v === "" ? "–" : new Intl.NumberFormat("de-DE", { maximumFractionDigits: 4 }).format(Number(v));
const date = (v: string | null) => (v ? new Date(v + "T00:00:00").toLocaleDateString("de-DE") : "–");

/** Hebt die Suchwörter im Text hervor */
function Highlight({ text, query }: { text: string | null; query: string }) {
  if (!text) return <span className="text-gray-300">–</span>;
  const terms = query.split(/\s+/).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (terms.length === 0) return <>{text}</>;
  const parts = text.split(new RegExp(`(${terms.join("|")})`, "i"));
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? <mark key={i} className="rounded bg-yellow-200 px-0.5 text-gray-900">{p}</mark> : <React.Fragment key={i}>{p}</React.Fragment>,
      )}
    </>
  );
}

const COLUMNS: { key: PositionSort; label: string; align?: "right" }[] = [
  { key: "product_name", label: "Artikel" },
  { key: "article_number", label: "Art.-Nr." },
  { key: "quantity", label: "Menge", align: "right" },
  { key: "unit_price", label: "Einzelpreis", align: "right" },
  { key: "total_price", label: "Gesamtpreis", align: "right" },
  { key: "supplier_name", label: "Lieferant" },
  { key: "invoice_number", label: "Rechnungsnr." },
  { key: "invoice_date", label: "Datum" },
  { key: "document_id", label: "Beleg" },
];

const field = "rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none";

// ─── Seite ────────────────────────────────────────────────────────────────────

export default function ArtikelPage() {
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [applied, setApplied] = useState<Form>(EMPTY_FORM);
  const [serverItems, setItems] = useState<PositionHit[]>([]);
  const [serverTotal, setTotal] = useState(0);
  // „Alle laden“: alle Treffer im Browser, Blättern/Filtern/Sortieren lokal
  const [allItems, setAllItems] = useState<PositionHit[] | null>(null);
  const [allProgress, setAllProgress] = useState<{ loaded: number; total: number } | null>(null);
  const [localQ, setLocalQ] = useState("");
  const [summary, setSummary] = useState<PositionSummary | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState<PositionSort | undefined>(undefined);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [capped, setCapped] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryDeferred, setSummaryDeferred] = useState(false);   // sehr breite Suche: Kennzahlen erst auf Klick (sonst Zeitüberschreitung)
  const [hiddenCount, setHiddenCount] = useState(0);               // Anzahl ausgeblendeter Imports (werden standardmäßig nicht durchsucht)
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const searchSeq = useRef(0);       // verwirft Antworten veralteter Suchen
  const summarySeq = useRef(0);

  const [suppliers, setSuppliers] = useState<string[]>([]);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [years, setYears] = useState<number[]>([]);
  const [units, setUnits] = useState<{ unit: string; count: number }[]>([]);
  const [showMore, setShowMore] = useState(false);
  const [showUnits, setShowUnits] = useState(false);
  const [showBase, setShowBase] = useState(false);   // umgerechnete Basiswerte (t/l/m) nur auf Wunsch — Standard: Originalwerte wie auf der Rechnung
  const [unitTable, setUnitTable] = useState<{ group: string; label: string; base_unit: string; units: { unit: string; factor: number }[] }[]>([]);

  // Aktueller Zustand für Callbacks (wie bei den Belegen: Blättern/Sortieren setzt nichts anderes zurück)
  const state = useRef({ applied: EMPTY_FORM as Form, pageSize: 50, sort: undefined as PositionSort | undefined, order: "desc" as "asc" | "desc" });
  state.current = { applied, pageSize, sort, order };

  /** Kennzahlen separat laden — sie dürfen die Tabelle nie blockieren (bei sehr vielen Positionen dauern sie länger). */
  const loadSummary = useCallback(async (f: Form) => {
    const seq = ++summarySeq.current;
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const sum = await positionsApi.summary(toFilter(f));
      if (seq === summarySeq.current) setSummary(sum);
    } catch (err) {
      if (seq === summarySeq.current) {
        setSummary(null);
        setSummaryError(extractApiError(err, "Kennzahlen konnten nicht berechnet werden"));
      }
    } finally {
      if (seq === summarySeq.current) setSummaryLoading(false);
    }
  }, []);

  const load = useCallback(async (f: Form, p: number, size: number, s: PositionSort | undefined, o: "asc" | "desc", withSummary: boolean) => {
    const seq = ++searchSeq.current;
    setLoading(true);
    setError(null);
    if (withSummary) { setSummaryDeferred(false); setSummary(null); setSummaryError(null); }
    try {
      const res = await positionsApi.search(toFilter(f), p, size, s, o);
      if (seq !== searchSeq.current) return;
      setItems(res.items);
      setTotal(res.total);
      setCapped(res.capped);
      if (withSummary) {
        if (res.capped) setSummaryDeferred(true); else loadSummary(f);
      }
      setPage(p);
      setSearched(true);
    } catch (err) {
      if (seq !== searchSeq.current) return;
      setError(extractApiError(err, "Fehler bei der Artikelsuche"));
      setSearched(true);
    } finally {
      if (seq === searchSeq.current) setLoading(false);
    }
  }, [loadSummary]);

  /** Auswahllisten (Import, Lieferant, Jahr, Einheit) — unabhängig von der Suche; ausgeblendete Imports nur auf Wunsch. */
  const loadOptions = useCallback(async (includeHidden: boolean) => {
    setOptionsLoading(true);
    const [b, o, u] = await Promise.all([
      importsApi.list({ include_hidden: includeHidden }).catch(() => [] as ImportBatch[]),
      documentsApi.filterOptions(includeHidden).catch(() => ({ years: [] as number[], companies: [] as string[], suppliers: [] as string[] })),
      positionsApi.filterOptions().catch(() => ({ units: [] as { unit: string; count: number }[] })),
    ]);
    setBatches(b); setSuppliers(o.suppliers ?? []); setYears(o.years); setUnits(u.units);
    setOptionsLoading(false);
  }, []);

  // Start: URL-Parameter (z.B. ?q=Kalk) übernehmen und sofort suchen; die Auswahllisten laden parallel im Hintergrund
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial = { ...EMPTY_FORM };
    (Object.keys(initial) as (keyof Form)[]).forEach((k) => { const v = params.get(k); if (v) initial[k] = v; });
    setForm(initial);
    setApplied(initial);
    if (Object.values(initial).some(Boolean)) setShowMore(true);
    load(initial, 0, 50, undefined, "desc", true);
  }, [load]);

  // Wie viele Imports sind ausgeblendet? (nur für den Hinweis „werden nicht durchsucht“)
  useEffect(() => {
    importsApi.list({ include_hidden: true }).then((all) => setHiddenCount(all.filter((b) => b.hidden_at).length)).catch(() => undefined);
  }, []);

  // Auswahllisten neu laden, wenn „Ausgeblendete Imports einbeziehen" umgeschaltet wird
  const includeHiddenOptions = form.include_hidden === "1";
  useEffect(() => { loadOptions(includeHiddenOptions); }, [loadOptions, includeHiddenOptions]);

  function apply(next: Form = form) {
    setAllItems(null); setLocalQ("");
    setApplied(next);
    syncUrl(next);
    load(next, 0, pageSize, sort, order, true);
  }
  function reset() {
    setAllItems(null); setLocalQ("");
    setForm(EMPTY_FORM);
    setSort(undefined);
    setOrder("desc");
    setApplied(EMPTY_FORM);
    syncUrl(EMPTY_FORM);
    load(EMPTY_FORM, 0, pageSize, undefined, "desc", true);
  }
  function sortBy(key: PositionSort) {
    const nextOrder = sort === key ? (order === "asc" ? "desc" : "asc") : (key === "document_id" || key === "invoice_date" || key === "total_price" || key === "unit_price" ? "desc" : "asc");
    setSort(key);
    setOrder(nextOrder);
    if (allItems) { setPage(0); return; }          // lokal sortiert
    load(applied, 0, pageSize, key, nextOrder, false);
  }
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const onEnter = (e: React.KeyboardEvent) => { if (e.key === "Enter") apply(); };

  const localView = useMemo(() => {
    if (!allItems) return null;
    const list = localQ.trim()
      ? allItems.filter((p) => matchesWords(localQ, [p.product_name, p.product_description, p.article_number, p.supplier_name, p.invoice_number, p.unit, p.company, p.year, p.document_id]))
      : allItems;
    if (!sort) return list;
    const dir = order === "asc" ? 1 : -1;
    const val = (p: PositionHit) => (p as unknown as Record<string, unknown>)[sort];
    return [...list].sort((a, b) => {
      const x = val(a), y = val(b);
      if (x === null || x === undefined || x === "") return y === null || y === undefined || y === "" ? 0 : 1;   // leer immer am Ende
      if (y === null || y === undefined || y === "") return -1;
      const nx = Number(x), ny = Number(y);
      const c = Number.isFinite(nx) && Number.isFinite(ny) ? nx - ny : String(x).localeCompare(String(y), "de");
      return c * dir;
    });
  }, [allItems, localQ, sort, order]);
  const total = localView ? localView.length : serverTotal;
  const items = localView ? localView.slice(page * pageSize, (page + 1) * pageSize) : serverItems;

  async function loadAll() {
    const seq = ++searchSeq.current;
    setError(null);
    setAllProgress({ loaded: 0, total: serverTotal });
    try {
      const list = await fetchAllPages<PositionHit>(
        (p, size) => positionsApi.search(toFilter(applied), p, size, sort, order),
        (loaded, tot) => { if (seq === searchSeq.current) setAllProgress({ loaded, total: tot }); },
        () => seq !== searchSeq.current,
      );
      if (seq !== searchSeq.current) return;
      setLocalQ("");
      setAllItems(list);
      setPage(0);
    } catch (err) {
      if (seq === searchSeq.current) setError(extractApiError(err, "Alle Treffer konnten nicht geladen werden"));
    } finally {
      if (seq === searchSeq.current) setAllProgress(null);
    }
  }
  function exitAll() { setAllItems(null); setLocalQ(""); load(applied, 0, pageSize, sort, order, false); }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeFrom = total === 0 ? 0 : page * pageSize + 1;
  const rangeTo = Math.min((page + 1) * pageSize, total);
  const pager = (testId: string, compact?: boolean) => (
    <Pager
      testId={testId} loading={loading} total={total} page={page} pageSize={pageSize} totalPages={totalPages}
      rangeFrom={rangeFrom} rangeTo={rangeTo} noun={["Position", "Positionen"]} compact={compact}
      onLoadAll={loadAll} allActive={!!allItems} allLoading={allProgress} onExitAll={exitAll}
      localFilter={localQ} onLocalFilter={(v) => { setLocalQ(v); setPage(0); }}
      onPage={(p) => {
        const target = Math.min(Math.max(0, p), totalPages - 1);
        if (allItems) setPage(target); else load(applied, target, pageSize, sort, order, false);
      }}
      onPageSize={(size) => {
        setPageSize(size);
        if (allItems) setPage(0); else load(applied, 0, size, sort, order, false);
      }}
    />
  );

  return (
    <div className="w-full">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight text-gray-900">Artikel</h1>
      <p className="mb-4 text-sm text-gray-500">
        Alle Positionen aus allen Belegen durchsuchen — z.B. um Preise eines Artikels bei verschiedenen Lieferanten zu vergleichen.
      </p>

      {/* ── Filter ─────────────────────────────────────────────────── */}
      <div className="mb-4 rounded-lg border bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex min-w-[20rem] flex-1 flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Suche (Bezeichnung oder Artikelnummer, mehrere Wörter = UND)</label>
            <input value={form.q} onChange={set("q")} onKeyDown={onEnter} autoFocus data-testid="q"
              placeholder="z.B. Kalkammonsalpeter oder KAS 27" className={`${field} w-full`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Lieferant</label>
            <select value={form.supplier_name} onChange={set("supplier_name")} className={`${field} w-52`} data-testid="supplier">
              <option value="">{optionsLoading && suppliers.length === 0 ? "Wird geladen…" : "Alle Lieferanten"}</option>
              {suppliers.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Jahr</label>
            <select value={form.year} onChange={set("year")} className={`${field} w-28`}>
              <option value="">Alle</option>
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Einheit</label>
            <select value={form.unit} onChange={set("unit")} className={`${field} w-32`}>
              <option value="">Alle</option>
              {units.map((u) => <option key={u.unit} value={u.unit}>{u.unit} ({u.count.toLocaleString("de-DE")})</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Einheitengruppe</label>
            <select value={form.unit_group} onChange={set("unit_group")} className={`${field} w-44`} data-testid="unit-group">
              <option value="">Alle</option>
              <option value="mass">Gewicht (t, kg, g …)</option>
              <option value="volume">Volumen (hl, l, ml …)</option>
              <option value="length">Länge (km, m, cm …)</option>
              <option value="area">Fläche (m²)</option>
            </select>
          </div>
          <button onClick={() => apply()} data-testid="apply"
            className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Suchen</button>
          <button onClick={reset} className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-600 hover:bg-gray-100">Zurücksetzen</button>
          <button onClick={() => {
            setShowUnits((v) => !v);
            if (unitTable.length === 0) positionsApi.unitTable().then(setUnitTable).catch(() => undefined);
          }} data-testid="units-toggle" className="text-sm text-blue-600 hover:underline">
            {showUnits ? "Einheiten ausblenden" : "ⓘ Einheiten-Umrechnung"}
          </button>
          <label className="flex cursor-pointer items-center gap-1.5 text-sm text-gray-600" title="Zeigt zusätzlich umgerechnete Werte (z. B. = 25,5 t) unter Menge und Preis. Die gespeicherten Originalwerte bleiben unverändert.">
            <input type="checkbox" checked={showBase} onChange={(e) => setShowBase(e.target.checked)} data-testid="show-base" />
            Umgerechnete Werte zeigen
          </label>
          <button onClick={() => setShowMore((v) => !v)} className="text-sm text-blue-600 hover:underline">
            {showMore ? "Weniger Filter" : "Mehr Filter"}
          </button>
        </div>

        {showUnits && (
          <div className="mt-3 rounded border border-blue-100 bg-blue-50/50 p-3 text-sm text-gray-700" data-testid="units-info">
            <p className="mb-2 text-xs text-gray-500">
              Mengen werden für Auswertungen in die Basiseinheit umgerechnet (in der Tabelle als „= …“ unter der Menge, Preis je
              Basiseinheit = Positionspreis ÷ Menge). Die Rechnungswerte bleiben unverändert gespeichert. Stück, Paletten, Säcke u. Ä.
              werden nicht umgerechnet.
            </p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {unitTable.map((g) => (
                <div key={g.group}>
                  <div className="font-medium">{g.label} <span className="text-xs font-normal text-gray-500">(Basis: {g.base_unit})</span></div>
                  <ul className="text-xs text-gray-600">
                    {g.units.map((u) => (
                      <li key={u.unit}>1 {u.unit} = {u.factor.toLocaleString("de-DE", { maximumFractionDigits: 6 })} {g.base_unit}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        )}

        {showMore && (
          <div className="mt-3 flex flex-wrap items-end gap-3 border-t pt-3" data-testid="more-filters">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Artikelnummer</label>
              <input value={form.article_number} onChange={set("article_number")} onKeyDown={onEnter} className={`${field} w-36`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Rechnungsnr.</label>
              <input value={form.invoice_number} onChange={set("invoice_number")} onKeyDown={onEnter} className={`${field} w-36`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Firma</label>
              <input value={form.company} onChange={set("company")} onKeyDown={onEnter} placeholder="z.B. Müller" className={`${field} w-36`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Import</label>
              <select value={form.batch_id} onChange={set("batch_id")} className={`${field} w-48`}>
                <option value="">Alle Imports</option>
                {batches.map((b) => <option key={b.id} value={b.id}>#{b.id} {b.company_name} {b.year}{b.hidden_at ? " (ausgeblendet)" : ""}</option>)}
              </select>
            </div>
            {([
              ["unit_price_min", "Einzelpreis von (€)"], ["unit_price_max", "Einzelpreis bis (€)"],
              ["total_price_min", "Gesamtpreis von (€)"], ["total_price_max", "Gesamtpreis bis (€)"],
              ["quantity_min", "Menge von"], ["quantity_max", "Menge bis"],
            ] as [keyof Form, string][]).map(([k, label]) => (
              <div key={k} className="flex flex-col gap-1">
                <label className="text-xs font-medium text-gray-600">{label}</label>
                <input type="number" step="any" value={form[k]} onChange={set(k)} onKeyDown={onEnter} className={`${field} w-32`} />
              </div>
            ))}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Rechnungsdatum von</label>
              <input type="date" value={form.date_from} onChange={set("date_from")} className={`${field} w-40`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Rechnungsdatum bis</label>
              <input type="date" value={form.date_to} onChange={set("date_to")} className={`${field} w-40`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Beleg-ID</label>
              <input type="number" value={form.document_id} onChange={set("document_id")} onKeyDown={onEnter} className={`${field} w-28`} />
            </div>
            <label className="mb-1.5 flex cursor-pointer items-center gap-2 text-sm text-gray-600">
              <input type="checkbox" checked={form.include_hidden === "1"} className="rounded border-gray-300 text-blue-600"
                onChange={(e) => setForm((f) => ({ ...f, include_hidden: e.target.checked ? "1" : "" }))} />
              Ausgeblendete Imports einbeziehen
            </label>
          </div>
        )}
      </div>

      {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700" data-testid="error">{error}</div>}
      {capped && !error && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800" data-testid="capped">
          Mehr als {total.toLocaleString("de-DE")} Treffer — angezeigt werden die ersten {total.toLocaleString("de-DE")}.
          Mit Lieferant, Jahr oder einem genaueren Suchwort lässt sich das Ergebnis eingrenzen.
        </div>
      )}

      {hiddenCount > 0 && applied.include_hidden !== "1" && !applied.batch_id && !applied.document_id && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800" data-testid="hidden-notice">
          {hiddenCount} ausgeblendete{hiddenCount === 1 ? "r Import wird" : " Imports werden"} nicht durchsucht — deshalb fehlen ggf. Treffer.
          <button onClick={() => { const next = { ...form, include_hidden: "1" }; setForm(next); apply(next); }} className="font-medium underline">Einbeziehen</button>
        </div>
      )}
      {summary && (summary.without_total_price ?? 0) > 0 && (applied.total_price_min || applied.total_price_max) && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800" data-testid="no-total-notice">
          {(summary.without_total_price ?? 0).toLocaleString("de-DE")} Position(en) in dieser Auswahl haben keinen gespeicherten Gesamtpreis und können bei einem Preisfilter nicht gefunden werden.
          Abhilfe: <Link href="/settings/automation" className="font-medium underline">Einstellungen → Automatisierung → „Prüfung für den Altbestand nachrüsten“</Link> (ohne KI).
        </div>
      )}
      {summaryDeferred && !summary && !summaryLoading && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600" data-testid="summary-deferred">
          Sehr viele Treffer — die Kennzahlen werden nicht automatisch berechnet (das kann lange dauern).
          <button onClick={() => loadSummary(applied)} className="font-medium text-blue-700 underline">Kennzahlen berechnen</button>
        </div>
      )}

      {/* ── Kennzahlen (laden getrennt von der Tabelle) ────────────── */}
      {summaryLoading && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="summary-loading">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="rounded-lg border bg-white p-3 shadow-sm">
              <div className="mb-2 h-3 w-24 animate-pulse rounded bg-gray-200" />
              <div className="mb-1.5 h-6 w-36 animate-pulse rounded bg-gray-200" />
              <div className="h-3 w-28 animate-pulse rounded bg-gray-100" />
            </div>
          ))}
        </div>
      )}
      {!summaryLoading && summaryError && !error && (
        <div className="mb-4 rounded-md border border-gray-200 bg-gray-50 p-3 text-xs text-gray-500" data-testid="summary-error">
          Kennzahlen: {summaryError}
        </div>
      )}
      {!summaryLoading && summary && searched && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="summary">
          <div className="rounded-lg border bg-white p-3 shadow-sm">
            <div className="text-xs text-gray-500">Treffer</div>
            <div className="text-xl font-semibold text-gray-900">{summary.count.toLocaleString("de-DE")} Positionen</div>
            <div className="text-xs text-gray-400">in {summary.documents.toLocaleString("de-DE")} Beleg(en)</div>
          </div>
          <div className="rounded-lg border bg-white p-3 shadow-sm">
            <div className="text-xs text-gray-500">Summe Gesamtpreis</div>
            <div className="text-xl font-semibold text-gray-900">{eur(summary.total_price_sum)}</div>
          </div>
          <div className="rounded-lg border bg-white p-3 shadow-sm">
            <div className="text-xs text-gray-500">Einzelpreis (Ø · min · max)</div>
            <div className="text-xl font-semibold text-gray-900">{eur(summary.unit_price_avg, 4)}</div>
            <div className="text-xs text-gray-400">{eur(summary.unit_price_min, 4)} – {eur(summary.unit_price_max, 4)}</div>
          </div>
          <div className="rounded-lg border bg-white p-3 shadow-sm">
            <div className="text-xs text-gray-500">Nach Einheit <span className="text-gray-400">(t/kg/g → kg, hl/l/ml → l)</span></div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {summary.by_unit.length === 0 && <span className="text-sm text-gray-300">–</span>}
              {summary.by_unit.slice(0, 5).map((u) => (
                <span key={u.unit ?? "?"} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                  title={`${u.count.toLocaleString("de-DE")} Positionen, ${eur(u.total_price)}`}>
                  {u.unit ?? "ohne"}: {num(u.quantity)}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Tabelle ────────────────────────────────────────────────── */}
      {pager("pager-top")}
      <div className="relative overflow-x-auto rounded-lg border bg-white shadow-sm">
        {loading && (
          <div className="absolute inset-x-0 top-0 z-10 h-1 overflow-hidden bg-blue-100" data-testid="loading-bar">
            <div className="h-1 w-1/3 animate-[slide_1.1s_ease-in-out_infinite] rounded bg-blue-500" />
          </div>
        )}
        {loading && items.length > 0 && (
          <div className="absolute inset-0 z-[5] flex items-start justify-center bg-white/60 pt-16" data-testid="loading-overlay">
            <div className="flex items-center gap-2 rounded-md border bg-white px-4 py-2 text-sm text-gray-600 shadow">
              <span className="inline-block animate-spin">⟳</span> Suche läuft …
            </div>
          </div>
        )}
        <table className="min-w-full divide-y divide-gray-200 text-sm" data-testid="results">
          <thead className="bg-gray-50">
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={`px-3 py-3 font-medium text-gray-600 ${c.align === "right" ? "text-right" : "text-left"}`}>
                  <button onClick={() => sortBy(c.key)} className="inline-flex items-center gap-1 hover:text-blue-700" data-testid={`sort-${c.key}`}>
                    {c.label}
                    <span className={sort === c.key ? "text-blue-600" : "text-gray-300"}>{sort === c.key ? (order === "asc" ? "▲" : "▼") : "↕"}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && items.length === 0 && (
              <>
                <tr data-testid="loading-text"><td colSpan={COLUMNS.length} className="px-4 pt-6 pb-2 text-center text-sm text-gray-500">
                  <span className="mr-2 inline-block animate-spin">⟳</span>
                  Artikel werden geladen … bei sehr vielen Positionen kann das einen Moment dauern.
                </td></tr>
                {Array.from({ length: 8 }).map((_, i) => (
                  <tr key={`sk${i}`} data-testid="skeleton-row">
                    {COLUMNS.map((c) => (
                      <td key={c.key} className="px-3 py-3"><div className="h-3.5 animate-pulse rounded bg-gray-100" style={{ width: `${55 + ((i * 7 + c.key.length * 5) % 40)}%` }} /></td>
                    ))}
                  </tr>
                ))}
              </>
            )}
            {!loading && searched && items.length === 0 && (
              <tr><td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-gray-400">Keine Artikel gefunden</td></tr>
            )}
            {items.map((p) => (
              <tr key={p.id} className="hover:bg-gray-50" data-testid="row">
                <td className="max-w-md px-3 py-2 text-gray-900">
                  <Highlight text={p.product_name ?? p.product_description} query={applied.q} />
                  {p.discount && <span className="ml-2 rounded bg-amber-50 px-1.5 text-xs text-amber-700" title="Nachlass">{p.discount}</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-gray-600">
                  <Highlight text={p.article_number} query={applied.q} />
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{num(p.quantity)} {p.unit ?? ""}
                  {p.pack_size != null && Number(p.pack_size) > 0 && (
                    <div className="text-xs text-violet-600" title="Inhalt je Verpackungseinheit">
                      à {num(p.pack_size)} {p.pack_unit ?? ""}
                      {p.content_quantity != null && <> · = {num(p.content_quantity)} {p.content_unit ?? ""}</>}
                    </div>
                  )}
                  {p.unit_type && <div className="text-[10px] uppercase tracking-wide text-gray-300">{UNIT_TYPE_LABELS[p.unit_type] ?? p.unit_type}</div>}
                  {showBase && p.base_unit && (p.unit ?? "").trim().toLowerCase() !== p.base_unit && (
                    <div className="text-xs text-gray-400" title="Umgerechnet in die Basiseinheit">
                      = {num(p.base_quantity ?? null)} {p.base_unit}
                    </div>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{eur(p.unit_price, 4)}
                  {p.price_quantity != null && Number(p.price_quantity) > 1 && (
                    <div className="text-xs text-violet-600" title="Der Einzelpreis gilt für mehrere Einheiten">je {num(p.price_quantity)} {p.unit ?? ""}</div>
                  )}
                  {p.content_unit_price != null && (
                    <div className="text-xs text-gray-400" title="Positionspreis ÷ Inhaltsmenge">
                      = {eur(p.content_unit_price, 4)} / {p.content_unit ?? "Einheit"}
                    </div>
                  )}
                  {showBase && p.base_unit && p.base_unit_price != null && (
                    <div className="text-xs text-gray-400" title="Positionspreis ÷ umgerechnete Menge">
                      = {eur(p.base_unit_price, 4)} / {p.base_unit}
                    </div>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-medium tabular-nums text-gray-900">{eur(p.total_price)}</td>
                <td className="px-3 py-2 text-gray-700">
                  {p.supplier_name ?? "–"}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-700">{p.invoice_number ?? "–"}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{date(p.invoice_date)}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <Link href={`/belege?doc_id=${p.document_id}`} className="text-blue-600 hover:underline"
                    title={`${p.original_filename} · ${p.company} ${p.year}`}>
                    #{p.document_id}
                  </Link>
                  {p.validation_status === "needs_review" && (
                    <span className="ml-1.5 rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700" title="Beleg: Plausibilitätsprüfung auffällig">Prüfen!</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2">{pager("pager-bottom", true)}</div>
    </div>
  );
}
