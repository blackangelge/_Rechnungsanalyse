"use client";

/**
 * Papierkorb: Belege, die aussortiert wurden (z. B. Lieferscheine, Angebote — alles, was keine Eingangsrechnung ist).
 * Hier wird geprüft (PDF öffnen), zurückgeholt oder ENDGÜLTIG gelöscht (inkl. PDF-Dateien, nicht umkehrbar).
 */

import { useCallback, useEffect, useState } from "react";
import Pager from "@/components/common/Pager";
import { Alert, Card, EmptyState, Icon, PageHeader, Spinner } from "@/components/ui";
import { DOCUMENT_TYPES, DocumentItem, documentsApi, extractApiError } from "@/lib/api";

const typeName = (id: number) => DOCUMENT_TYPES.find((t) => t.id === id)?.name ?? (id ? `Typ ${id}` : "nicht erkannt");
const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");

export default function TrashPage() {
  const [items, setItems] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [selectingAll, setSelectingAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async (p: number, size: number) => {
    setLoading(true);
    try {
      const r = await documentsApi.listPage({ only_deleted: true }, p, size);
      setItems(r.items);
      setTotal(r.total);
      setError(null);
    } catch (e) {
      setError(extractApiError(e, "Papierkorb konnte nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(page, pageSize); }, [load, page, pageSize]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const toggle = (id: number) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allOnPage = items.length > 0 && items.every((d) => selected.has(d.id));

  async function selectAllHits() {
    setSelectingAll(true);
    try { setSelected(new Set(await documentsApi.ids({ only_deleted: true }))); }
    catch (e) { setError(extractApiError(e, "Auswahl fehlgeschlagen")); }
    finally { setSelectingAll(false); }
  }

  async function act(kind: "restore" | "purge") {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (kind === "purge" && !confirm(
      `${ids.length.toLocaleString("de-DE")} Beleg(e) ENDGÜLTIG löschen?\n\nBelegdaten, Positionen und die PDF-Dateien werden entfernt. Das lässt sich nicht rückgängig machen.`)) return;
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = kind === "restore" ? await documentsApi.bulkRestore(ids) : await documentsApi.purge(ids);
      setInfo(kind === "restore" ? `${r.changed} Beleg(e) zurückgeholt.` : `${r.changed} Beleg(e) endgültig gelöscht (${r.files_removed} Datei(en)).${r.skipped ? ` ${r.skipped} übersprungen (z. B. in Verarbeitung).` : ""}`);
      setSelected(new Set());
      const newTotalPages = Math.max(1, Math.ceil((total - r.changed) / pageSize));
      const nextPage = Math.min(page, newTotalPages - 1);
      if (nextPage !== page) setPage(nextPage); else await load(page, pageSize);
    } catch (e) {
      setError(extractApiError(e, "Aktion fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Papierkorb"
        subtitle="Aussortierte Belege — vor allem alles, was keine Eingangsrechnung ist. Hier prüfen (PDF öffnen), zurückholen oder endgültig löschen. Endgültiges Löschen entfernt auch die PDF-Dateien und ist nicht umkehrbar."
        actions={<button className="btn btn-secondary" onClick={() => load(page, pageSize)} disabled={loading}><Icon name="refresh" /> Aktualisieren</button>} />
      {error && <div className="mb-4"><Alert tone="error" onClose={() => setError(null)}>{error}</Alert></div>}
      {info && <div className="mb-4"><Alert tone="success" onClose={() => setInfo(null)}>{info}</Alert></div>}

      <div className={`mb-4 flex flex-wrap items-center gap-3 rounded-lg border p-3 ${selected.size > 0 ? "border-blue-200 bg-blue-50" : "border-gray-200 bg-gray-50"}`}>
        <span className="text-sm font-medium text-gray-700">{selected.size > 0 ? `${selected.size.toLocaleString("de-DE")} ausgewählt` : "Belege auswählen"}</span>
        <div className="ml-auto flex gap-2">
          <button className="btn btn-secondary btn-sm" disabled={busy || selected.size === 0} onClick={() => act("restore")} data-testid="restore-selection">Zurückholen</button>
          <button className="btn btn-danger btn-sm" disabled={busy || selected.size === 0} onClick={() => act("purge")} data-testid="purge-selection"><Icon name="trash" /> Endgültig löschen</button>
        </div>
      </div>

      {loading && items.length === 0 ? <Spinner /> : total === 0 ? (
        <EmptyState title="Der Papierkorb ist leer" text="Aussortierte Belege erscheinen hier — auf der Belege-Seite „Nur Nicht-Eingangsrechnungen“ filtern und in den Papierkorb legen, oder ein Workflow mit dem Knoten „Beleg verwerfen“ nutzen." />
      ) : (
        <>
          <Pager testId="trash-pager" loading={loading} total={total} page={page} pageSize={pageSize} totalPages={totalPages}
            rangeFrom={page * pageSize + 1} rangeTo={Math.min(total, (page + 1) * pageSize)}
            selectedCount={selected.size} selectingAll={selectingAll} onSelectAll={selectAllHits} onClearSelection={() => setSelected(new Set())}
            onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(0); }} />
          <Card pad={false}>
            <div className="overflow-x-auto">
              <table className="table-ra min-w-full">
                <thead>
                  <tr>
                    <th className="w-8"><input type="checkbox" aria-label="Alle auf dieser Seite" checked={allOnPage}
                      onChange={() => setSelected((s) => { const n = new Set(s); items.forEach((d) => (allOnPage ? n.delete(d.id) : n.add(d.id))); return n; })} /></th>
                    <th>ID</th><th>Datei</th><th>Import</th><th>Typ</th><th>Seiten</th><th>Grund</th><th>Im Papierkorb seit</th><th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((d) => (
                    <tr key={d.id} className={selected.has(d.id) ? "bg-blue-50/60" : ""}>
                      <td><input type="checkbox" aria-label={`Beleg ${d.id} auswählen`} checked={selected.has(d.id)} onChange={() => toggle(d.id)} /></td>
                      <td className="font-mono text-xs">{d.id}</td>
                      <td className="max-w-[18rem] truncate" title={d.original_filename}>{d.original_filename}</td>
                      <td className="whitespace-nowrap">{d.company ?? "–"} {d.year ?? ""}</td>
                      <td className="whitespace-nowrap">{typeName(d.document_type)}</td>
                      <td>{d.page_count || "–"}</td>
                      <td className="max-w-[16rem] truncate text-gray-600" title={d.deleted_reason ?? ""}>{d.deleted_reason ?? "–"}</td>
                      <td className="whitespace-nowrap text-gray-500">{fmtDate(d.deleted_at)}</td>
                      <td className="whitespace-nowrap text-right">
                        <a className="btn btn-ghost btn-sm" href={documentsApi.previewUrl(d.id)} target="_blank" rel="noopener noreferrer">PDF ansehen</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
