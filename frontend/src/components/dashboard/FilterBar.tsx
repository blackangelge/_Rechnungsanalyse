/**
 * Filterleiste für das Dashboard.
 *
 * Ermöglicht das Filtern der Import-Batch-Tabelle nach:
 * - Firmenname (Texteingabe, Teilstring-Suche)
 * - Jahr (Dropdown mit allen vorhandenen Jahren)
 * - Mehrere Jahre gleichzeitig (Multi-Select)
 */

"use client";

interface Filters {
  company: string;
  years: number[];
}

interface Props {
  /** Alle verfügbaren Jahre aus den vorhandenen Batches */
  availableYears: number[];
  /** Aktuelle Filter-Werte */
  filters: Filters;
  /** Callback bei Änderung */
  onChange: (filters: Filters) => void;
}

export default function FilterBar({ availableYears, filters, onChange }: Props) {
  /** Jahr zur Auswahl hinzufügen oder entfernen */
  function toggleYear(year: number) {
    const isSelected = filters.years.includes(year);
    const newYears = isSelected
      ? filters.years.filter((y) => y !== year)
      : [...filters.years, year];
    onChange({ ...filters, years: newYears });
  }

  return (
    <div className="flex flex-wrap items-center gap-4 rounded-lg border bg-white p-4 shadow-sm">
      {/* Firmenname-Suche */}
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium text-gray-600">Firma:</label>
        <input
          className="rounded border px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="Teilstring..."
          value={filters.company}
          onChange={(e) => onChange({ ...filters, company: e.target.value })}
        />
      </div>

      {/* Jahres-Filter (Multi-Toggle-Buttons) */}
      {availableYears.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-600">Jahr:</span>
          <div className="flex flex-wrap gap-1">
            {availableYears.map((year) => {
              const isSelected = filters.years.includes(year);
              return (
                <button
                  key={year}
                  onClick={() => toggleYear(year)}
                  className={[
                    "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                    isSelected
                      ? "bg-blue-600 text-white"
                      : "border text-gray-600 hover:bg-gray-50",
                  ].join(" ")}
                >
                  {year}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Filter zurücksetzen */}
      {(filters.company || filters.years.length > 0) && (
        <button
          onClick={() => onChange({ company: "", years: [] })}
          className="text-xs text-gray-400 hover:text-gray-700"
        >
          Filter zurücksetzen
        </button>
      )}
    </div>
  );
}
