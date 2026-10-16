/**
 * Lädt alle Treffer einer serverseitig paginierten Liste in Blöcken in den Browser
 * (für „Alle laden“: danach blättern, filtern und sortieren lokal).
 */
export async function fetchAllPages<T>(
  fetchPage: (page: number, size: number) => Promise<{ items: T[]; total: number }>,
  onProgress?: (loaded: number, total: number) => void,
  isCancelled?: () => boolean,
  chunk = 2000,
): Promise<T[]> {
  const first = await fetchPage(0, chunk);
  const all: T[] = [...first.items];
  onProgress?.(all.length, first.total);
  for (let p = 1; all.length < first.total; p++) {
    if (isCancelled?.()) return all;
    const next = await fetchPage(p, chunk);
    if (next.items.length === 0) break;
    all.push(...next.items);
    onProgress?.(all.length, first.total);
  }
  return all;
}

/** Alle Wörter müssen in mindestens einem der Felder vorkommen (Groß/Klein egal). */
export function matchesWords(query: string, values: unknown[]): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = values.map((v) => (v === null || v === undefined ? "" : String(v))).join(" \n ").toLowerCase();
  return words.every((w) => hay.includes(w));
}
