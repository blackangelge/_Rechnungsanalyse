"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Pager from "@/components/common/Pager";
import {
  ImportBatch, PositionFilter, PositionHit, PositionSort, PositionSummary,
  documentsApi, extractApiError, importsApi, positionsApi,
} from "@/lib/api";

// ─── Formular ↔ Filter ↔ URL ──────────────────────────────────────────────────

/** Alle Eingabefelder als Text; Schlüssel = API-Parameter (und URL-Parameter) */
const EMPTY_FORM = {
  q: "", article_number: "", supplier_name: "", company: "", year: "", batch_id: "", unit: "",
  invoice_number: "", unit_price_min: "", unit_price_max: "", total_price_min: "", total_price_max: "",
  quantity_min: "", quantity_max: "", date_from: "", date_to: "", document_id: "",
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
  const [items, setItems] = useState<PositionHit[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<PositionSummary | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState<PositionSort | undefined>(undefined);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const [suppliers, setSuppliers] = useState<string[]>([]);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [years, setYears] = useState<number[]>([]);
  const [units, setUnits] = useState<{ unit: string; count: number }[]>([]);
  const [showMore, setShowMore] = useState(false);

  // Aktueller Zustand für Callbacks (wie bei den Belegen: Blättern/Sortieren setzt nichts anderes zurück)
  const state = useRef({ applied: EMPTY_FORM as Form, pageSize: 50, sort: undefined as PositionSort | undefined, order: "desc" as "asc" | "desc" });
  state.current = { applied, pageSize, sort, order };

  const load = useCallback(async (f: Form, p: number, size: number, s: PositionSort | undefined, o: "asc" | "desc", withSummary: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const filter = toFilter(f);
      const [res, sum] = await Promise.all([
        positionsApi.search(filter, p, size, s, o),
        withSummary ? positionsApi.summary(filter) : Promise.resolve(null),
      ]);
      setItems(res.items);
      setTotal(res.total);
      setPage(p);
      if (sum) setSummary(sum);
      setSearched(true);
    } catch (err) {
      setError(extractApiError(err, "Fehler bei der Artikelsuche"));
    } finally {
      setLoading(false);
    }
  }, []);

  // Start: Optionen laden; URL-Parameter (z.B. ?q=Kalk) übernehmen und sofort suchen
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial = { ...EMPTY_FORM };
    (Object.keys(initial) as (keyof Form)[]).forEach((k) => { const v = params.get(k); if (v) initial[k] = v; });
    setForm(initial);
    setApplied(initial);
    if (Object.values(initial).some(Boolean)) setShowMore(true);
    load(initial, 0, 50, undefined, "desc", true);
    Promise.all([
      importsApi.list().catch(() => [] as ImportBatch[]),
      documentsApi.filterOptions().catch(() => ({ years: [] as number[], companies: [] as string[], suppliers: [] as string[] })),
      positionsApi.filterOptions().catch(() => ({ units: [] as { unit: string; count: number }[] })),
    ]).then(([b, o, u]) => { setBatches(b); setSuppliers(o.suppliers ?? []); setYears(o.years); setUnits(u.units); });
  }, [load]);

  function apply(next: Form = form) {
    setApplied(next);
    syncUrl(next);
    load(next, 0, pageSize, sort, order, true);
  }
  function reset() {
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
    load(applied, 0, pageSize, key, nextOrder, false);
  }
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const onEnter = (e: React.KeyboardEvent) => { if (e.key === "Enter") apply(); };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeFrom = total === 0 ? 0 : page * pageSize + 1;
  const rangeTo = Math.min((page + 1) * pageSize, total);
  const pager = (testId: string, compact?: boolean) => (
    <Pager
      testId={testId} loading={loading} total={total} page={page} pageSize={pageSize} totalPages={totalPages}
      rangeFrom={rangeFrom} rangeTo={rangeTo} noun={["Position", "Positionen"]} compact={compact}
      onPage={(p) => load(applied, Math.min(Math.max(0, p), totalPages - 1), pageSize, sort, order, false)}
      onPageSize={(size) => { setPageSize(size); load(applied, 0, size, sort, order, false); }}
    />
  );

  return (
    <div className="relative left-1/2 w-screen -translate-x-1/2 px-6">
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Artikel</h1>
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
              <option value="">Alle Lieferanten</option>
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
          <button onClick={() => apply()} data-testid="apply"
            className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Suchen</button>
          <button onClick={reset} className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-600 hover:bg-gray-100">Zurücksetzen</button>
          <button onClick={() => setShowMore((v) => !v)} className="text-sm text-blue-600 hover:underline">
            {showMore ? "Weniger Filter" : "Mehr Filter"}
          </button>
        </div>

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
                {batches.map((b) => <option key={b.id} value={b.id}>#{b.id} {b.company_name} {b.year}</option>)}
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
          </div>
        )}
      </div>

      {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {/* ── Kennzahlen ─────────────────────────────────────────────── */}
      {summary && searched && (
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
            <div className="text-xs text-gray-500">Nach Einheit</div>
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
      <div className="overflow-x-auto rounded-lg border bg-white shadow-sm">
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
              <tr><td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-gray-400">Wird geladen...</td></tr>
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
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{num(p.quantity)} {p.unit ?? ""}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{eur(p.unit_price, 4)}</td>
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
