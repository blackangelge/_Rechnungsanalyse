"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Pager from "@/components/common/Pager";
import { DbColumn, DbRow, DbTable, dbApi, extractApiError } from "@/lib/api";

const field = "rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-500 focus:outline-none";

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ─── Zeile ansehen / bearbeiten / anlegen ─────────────────────────────────────

function RowEditor({
  table, pk, writable, onClose, onSaved,
}: {
  table: DbTable;
  /** null = neue Zeile */
  pk: unknown | null;
  writable: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = pk === null;
  const [original, setOriginal] = useState<DbRow | null>(isNew ? {} : null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [nulls, setNulls] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (isNew) return;
    dbApi.row(table.name, pk).then((row) => {
      setOriginal(row);
      const v: Record<string, string> = {};
      const n = new Set<string>();
      table.columns.forEach((c) => {
        const x = row[c.name];
        if (x === null || x === undefined) n.add(c.name);
        v[c.name] = cellText(x);
      });
      setValues(v);
      setNulls(n);
    }).catch((e) => setError(extractApiError(e, "Zeile konnte nicht geladen werden")));
  }, [table, pk, isNew]);

  function changes(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    table.columns.forEach((c) => {
      if (c.primary_key || c.secret) return;
      const before = original?.[c.name];
      const wasNull = before === null || before === undefined;
      if (nulls.has(c.name)) { if (!wasNull) out[c.name] = null; return; }
      const now = values[c.name] ?? "";
      if (wasNull || cellText(before) !== now) out[c.name] = now;
    });
    return out;
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (isNew) {
        const vals: Record<string, unknown> = {};
        table.columns.forEach((c) => {
          if (c.primary_key || c.secret || nulls.has(c.name)) return;
          if ((values[c.name] ?? "") !== "") vals[c.name] = values[c.name];
        });
        await dbApi.insert(table.name, vals);
      } else {
        const ch = changes();
        if (Object.keys(ch).length === 0) { onClose(); return; }
        await dbApi.update(table.name, pk, ch);
      }
      onSaved();
    } catch (e) {
      setError(extractApiError(e, "Speichern fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(`${table.name} #${String(pk)} wirklich LÖSCHEN? Das lässt sich nicht rückgängig machen.\n\nHinweis: Belege besser über „Belege“ löschen; hier werden Dateien nicht mitgelöscht.`)) return;
    setBusy(true);
    try {
      await dbApi.remove(table.name, pk);
      onSaved();
    } catch (e) {
      setError(extractApiError(e, "Löschen fehlgeschlagen"));
      setBusy(false);
    }
  }

  const input = (c: DbColumn) => {
    const disabled = !writable || c.primary_key || c.secret || nulls.has(c.name);
    const v = values[c.name] ?? "";
    const set = (x: string) => setValues((s) => ({ ...s, [c.name]: x }));
    if (c.type === "bool") {
      return (
        <select value={v} disabled={disabled} onChange={(e) => set(e.target.value)} className={`${field} w-32`}>
          <option value="">–</option><option value="true">ja</option><option value="false">nein</option>
        </select>
      );
    }
    const long = c.type === "json" || v.length > 80 || v.includes("\n");
    return long ? (
      <textarea value={v} disabled={disabled} onChange={(e) => set(e.target.value)} rows={Math.min(14, Math.max(3, Math.ceil(v.length / 90)))}
        className={`${field} w-full font-mono text-xs`} />
    ) : (
      <input value={v} disabled={disabled} onChange={(e) => set(e.target.value)} className={`${field} w-full`} />
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6" data-testid="row-editor">
      <div className="w-full max-w-3xl rounded-lg bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">
            {table.name} {isNew ? "– neue Zeile" : `#${String(pk)}`}
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">✕</button>
        </div>
        {error && <div className="mb-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        {original === null ? <p className="text-sm text-gray-500">Lade…</p> : (
          <div className="space-y-2">
            {table.columns.map((c) => (
              <div key={c.name} className="grid grid-cols-[11rem_1fr_auto] items-start gap-2">
                <div className="pt-1 text-xs text-gray-600">
                  <span className="font-medium">{c.name}</span>
                  <div className="text-[10px] text-gray-400">{c.type}{c.primary_key ? " · PK" : ""}{c.references ? ` → ${c.references}` : ""}</div>
                </div>
                {input(c)}
                {writable && c.nullable && !c.primary_key && !c.secret ? (
                  <label className="flex items-center gap-1 pt-1 text-[10px] text-gray-500" title="Wert auf NULL (leer) setzen">
                    <input type="checkbox" checked={nulls.has(c.name)}
                      onChange={(e) => setNulls((s) => { const n = new Set(s); if (e.target.checked) n.add(c.name); else n.delete(c.name); return n; })} />
                    NULL
                  </label>
                ) : <span />}
              </div>
            ))}
          </div>
        )}
        <div className="mt-4 flex items-center gap-3">
          {writable && original !== null && (
            <button onClick={save} disabled={busy} className="btn btn-primary">
              {busy ? "Speichere…" : "Speichern"}
            </button>
          )}
          {writable && !isNew && (
            <button onClick={remove} disabled={busy} className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50">Löschen</button>
          )}
          <button onClick={onClose} className="ml-auto btn btn-secondary btn-sm">Schließen</button>
        </div>
        {!writable && <p className="mt-2 text-xs text-gray-400">Nur Ansicht — zum Ändern oben „Bearbeiten erlauben“ einschalten.</p>}
      </div>
    </div>
  );
}

// ─── Tabellenansicht ──────────────────────────────────────────────────────────

export default function TableBrowser() {
  const [tables, setTables] = useState<DbTable[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const [current, setCurrent] = useState<string>("");
  const [rows, setRows] = useState<DbRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [q, setQ] = useState("");
  const [appliedQ, setAppliedQ] = useState("");
  const [filterCol, setFilterCol] = useState("");
  const [filterVal, setFilterVal] = useState("");
  const [appliedFilter, setAppliedFilter] = useState<{ col: string; val: string } | null>(null);
  const [sort, setSort] = useState<string | undefined>(undefined);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [writable, setWritable] = useState(false);
  const [editing, setEditing] = useState<{ pk: unknown | null } | null>(null);
  const seq = useRef(0);

  const table = tables.find((t) => t.name === current);

  const loadTables = useCallback(async () => {
    setTablesLoading(true);
    setTablesError(null);
    try {
      const t = await dbApi.tables();
      setTables(t);
      setCurrent((c) => c || (t.find((x) => x.name === "order_positions") ? "order_positions" : t[0]?.name ?? ""));
    } catch (e) {
      setTablesError(extractApiError(e, "Tabellen konnten nicht geladen werden"));
    } finally {
      setTablesLoading(false);
    }
  }, []);
  useEffect(() => { loadTables(); }, [loadTables]);

  const load = useCallback(async (p: number, size: number, s = sort, o = order, qq = appliedQ, f = appliedFilter) => {
    if (!current) return;
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await dbApi.rows(current, {
        limit: size, offset: p * size, q: qq, sort: s, order: o,
        filters: f && f.col ? { [f.col]: f.val } : undefined,
      });
      if (my !== seq.current) return;
      setRows(res.items);
      setTotal(res.total);
      setPage(p);
    } catch (e) {
      if (my === seq.current) setError(extractApiError(e, "Fehler beim Laden"));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [current, sort, order, appliedQ, appliedFilter]);

  // Tabellenwechsel: Suche zurücksetzen und neu laden
  useEffect(() => {
    if (!current) return;
    setQ(""); setAppliedQ(""); setFilterCol(""); setFilterVal(""); setAppliedFilter(null); setSort(undefined); setOrder("desc");
    load(0, pageSize, undefined, "desc", "", null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  function apply() {
    const f = filterCol ? { col: filterCol, val: filterVal } : null;
    setAppliedQ(q);
    setAppliedFilter(f);
    load(0, pageSize, sort, order, q, f);
  }
  function sortBy(col: string) {
    const o = sort === col ? (order === "asc" ? "desc" : "asc") : "asc";
    setSort(col); setOrder(o);
    load(0, pageSize, col, o);
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeFrom = total === 0 ? 0 : page * pageSize + 1;
  const rangeTo = Math.min((page + 1) * pageSize, total);
  const pk = table?.primary_key ?? null;

  return (
    <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
      {/* Tabellenliste */}
      <aside className="card p-2" data-testid="table-list">
        <div className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-gray-400">Tabellen</div>
        {tablesLoading && tables.length === 0 && <p className="px-2 py-1 text-xs text-gray-400">Lade Tabellen…</p>}
        {tablesError && (
          <div className="m-1 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700" data-testid="tables-error">
            {tablesError}
            <button onClick={loadTables} className="ml-2 font-medium underline">Erneut versuchen</button>
          </div>
        )}
        {!tablesLoading && !tablesError && tables.length === 0 && (
          <p className="px-2 py-1 text-xs text-gray-500">Keine Tabellen gefunden.</p>
        )}
        <ul>
          {tables.map((t) => (
            <li key={t.name}>
              <button onClick={() => setCurrent(t.name)}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${t.name === current ? "bg-blue-50 font-medium text-blue-800" : "text-gray-700 hover:bg-gray-50"}`}>
                <span className="truncate">{t.name}</span>
                <span className="ml-2 text-xs tabular-nums text-gray-400">{t.count?.toLocaleString("de-DE") ?? ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="min-w-0">
        <div className="mb-3 flex flex-wrap items-end gap-3 card p-3">
          <div className="flex min-w-[16rem] flex-1 flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Suche in allen Textspalten (mehrere Wörter = UND; Zahl = auch ID)</label>
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && apply()} className={`${field} w-full`} data-testid="db-q" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Spalte = Wert (leer = NULL)</label>
            <div className="flex gap-1">
              <select value={filterCol} onChange={(e) => setFilterCol(e.target.value)} className={`${field} w-40`}>
                <option value="">(kein Filter)</option>
                {table?.columns.filter((c) => !c.secret).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
              <input value={filterVal} disabled={!filterCol} onChange={(e) => setFilterVal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && apply()} className={`${field} w-40`} />
            </div>
          </div>
          <button onClick={apply} className="btn btn-primary">Suchen</button>
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm text-gray-700" title="Ohne diesen Schalter sind alle Daten schreibgeschützt">
            <input type="checkbox" checked={writable} onChange={(e) => setWritable(e.target.checked)} data-testid="write-toggle" />
            Bearbeiten erlauben
          </label>
          {writable && pk && (
            <button onClick={() => setEditing({ pk: null })} className="rounded border border-emerald-600 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-100">+ Neue Zeile</button>
          )}
        </div>
        {writable && (
          <div className="mb-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
            Bearbeiten ist eingeschaltet: Änderungen wirken sofort auf die echten Daten (Backend-Log hält sie fest). Es laufen keine Plausibilitätsprüfungen — bei Positionen/Beträgen bitte selbst auf Konsistenz achten.
          </div>
        )}
        {error && <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <Pager
          testId="db-pager" loading={loading} total={total} page={page} pageSize={pageSize} totalPages={totalPages}
          rangeFrom={rangeFrom} rangeTo={rangeTo} noun={["Zeile", "Zeilen"]}
          onPage={(p) => load(Math.min(Math.max(0, p), totalPages - 1), pageSize)}
          onPageSize={(s) => { setPageSize(s); load(0, s); }}
        />
        <div className="relative overflow-x-auto card">
          <table className="min-w-full text-xs" data-testid="db-grid">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="w-10 px-2 py-2" />
                {table?.columns.map((c) => (
                  <th key={c.name} className="whitespace-nowrap px-2 py-2 text-left font-medium">
                    <button onClick={() => sortBy(c.name)} className="inline-flex items-center gap-1 hover:text-blue-700">
                      {c.name}{sort === c.name ? (order === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {!loading && rows.length === 0 && (
                <tr><td colSpan={(table?.columns.length ?? 0) + 1} className="px-3 py-6 text-center text-gray-400">Keine Zeilen</td></tr>
              )}
              {rows.map((r, i) => (
                <tr key={pk ? String(r[pk]) : i} className="hover:bg-gray-50">
                  <td className="px-2 py-1.5">
                    {pk && <button onClick={() => setEditing({ pk: r[pk] })} className="rounded px-1.5 py-0.5 text-blue-600 hover:bg-blue-50" title="Zeile öffnen">{writable ? "✎" : "🔍"}</button>}
                  </td>
                  {table?.columns.map((c) => (
                    <td key={c.name} className="max-w-[22rem] truncate px-2 py-1.5 text-gray-800" title={cellText(r[c.name])}>
                      {r[c.name] === null || r[c.name] === undefined ? <span className="text-gray-300">NULL</span> : cellText(r[c.name])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!pk && table && <p className="mt-2 text-xs text-gray-400">Diese Tabelle hat keinen einfachen Primärschlüssel — nur Ansicht.</p>}
      </section>

      {editing && table && (
        <RowEditor
          table={table} pk={editing.pk} writable={writable}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(page, pageSize); loadTables(); }}
        />
      )}
    </div>
  );
}
