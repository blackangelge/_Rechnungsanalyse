"use client";

/**
 * Blätterleiste für serverseitig paginierte Listen (Belege, Artikel).
 * Zeigt "x–y von N", Seitengröße, « ‹ Zurück | Seite p / P | Weiter › », optional
 * "Alle N Treffer auswählen" und "Auswahl aufheben".
 */
export default function Pager({
  testId,
  loading,
  total,
  page,
  pageSize,
  totalPages,
  rangeFrom,
  rangeTo,
  noun = ["Beleg", "Belege"],
  selectedCount = 0,
  selectingAll = false,
  onSelectAll,
  onClearSelection,
  onPage,
  onPageSize,
  pageSizes = [25, 50, 100, 200],
  allSize,
  compact,
}: {
  testId: string;
  loading: boolean;
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  rangeFrom: number;
  rangeTo: number;
  /** [Einzahl, Mehrzahl] — z.B. ["Beleg", "Belege"] */
  noun?: [string, string];
  selectedCount?: number;
  selectingAll?: boolean;
  /** Ohne diese Callbacks gibt es keine Auswahl-Links */
  onSelectAll?: () => void;
  onClearSelection?: () => void;
  onPage: (p: number) => void;
  onPageSize: (size: number) => void;
  pageSizes?: number[];
  /** Maximale Seitengröße für „Alle laden“ (ohne Angabe kein Button) */
  allSize?: number;
  compact?: boolean;
}) {
  const btn = "rounded border border-gray-300 bg-white px-2.5 py-1 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40";
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-gray-500" data-testid={testId}>
      <span data-testid={`${testId}-count`}>
        {loading && total === 0
          ? "Lade..."
          : total === 0
            ? `Keine ${noun[1]} gefunden`
            : `${rangeFrom.toLocaleString("de-DE")}–${rangeTo.toLocaleString("de-DE")} von ${total.toLocaleString("de-DE")} ${total === 1 ? noun[0] : noun[1]}`}
      </span>
      {onSelectAll && total > pageSize && selectedCount < total && (
        <button onClick={onSelectAll} disabled={selectingAll} className="text-blue-600 hover:underline disabled:opacity-50">
          {selectingAll ? "Wähle aus…" : `Alle ${total.toLocaleString("de-DE")} Treffer auswählen`}
        </button>
      )}
      {onClearSelection && selectedCount > 0 && (
        <button onClick={onClearSelection} className="text-gray-500 hover:underline">
          Auswahl aufheben ({selectedCount.toLocaleString("de-DE")})
        </button>
      )}
      {allSize && total > pageSize && pageSize < allSize && (
        <button
          onClick={() => {
            if (total > 5000 && !window.confirm(`${Math.min(total, allSize).toLocaleString("de-DE")} Einträge auf einmal laden? Das kann einige Sekunden dauern und den Browser verlangsamen.`)) return;
            onPageSize(allSize);
          }}
          disabled={loading}
          className="rounded border border-blue-300 bg-blue-50 px-2.5 py-1 text-blue-700 hover:bg-blue-100 disabled:opacity-50"
          data-testid={`${testId}-load-all`}
        >
          Alle laden{total > allSize ? ` (max. ${allSize.toLocaleString("de-DE")})` : ""}
        </button>
      )}
      <div className="ml-auto flex items-center gap-2">
        {!compact && (
          <label className="flex items-center gap-1.5 text-xs text-gray-500">
            pro Seite
            <select value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))}
              className="rounded border border-gray-300 bg-white px-1.5 py-1 text-sm">
              {pageSizes.map((n) => <option key={n} value={n}>{n}</option>)}
              {allSize && !pageSizes.includes(allSize) && <option value={allSize}>Alle</option>}
            </select>
          </label>
        )}
        <button className={btn} onClick={() => onPage(0)} disabled={page === 0 || loading}>«</button>
        <button className={btn} onClick={() => onPage(page - 1)} disabled={page === 0 || loading}>‹ Zurück</button>
        <span className="px-1 tabular-nums" data-testid={`${testId}-page`}>Seite {page + 1} / {totalPages}</span>
        <button className={btn} onClick={() => onPage(page + 1)} disabled={page >= totalPages - 1 || loading}>Weiter ›</button>
        <button className={btn} onClick={() => onPage(totalPages - 1)} disabled={page >= totalPages - 1 || loading}>»</button>
      </div>
    </div>
  );
}
