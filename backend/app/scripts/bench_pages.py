"""
Prüft die Seitenerkennung (Workflow-Knoten „Seiten erkennen“) auf echten PDFs — BEVOR sie automatisch Belege beschneidet/aufteilt.

    python -m app.scripts.bench_pages --pdf-dir /pfad/zu/pdfs --api-url http://KI-RECHNER:8000 --model nvidia/Qwen3.6-35B-A3B-NVFP4 \
        [--expected erwartet.csv] [--n 30] [--dpi 80] [--chunk-size 10]

Je PDF wird jede Seite klassifiziert (genau wie im Workflow) und ausgegeben: Seitentyp, Rechnungsnummer, Hinweise der KI,
die erkannten Rechnungen und ob die Erkennung „sicher“ genug zum automatischen Beschneiden wäre. Es wird NICHTS gespeichert.

Optional `--expected`: CSV (`datei;seite;typ`, ';' oder ',' als Trenner, Kopfzeile erlaubt) mit der richtigen Antwort für einzelne Seiten —
dann gibt es Treffer/Fehler je Seitentyp und eine Fehlerliste (rechnung/rechnung_folgeseite gelten dabei als eigene Typen).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from app.services import page_classifier as pc
from app.services import pdf_service


def _config(args) -> SimpleNamespace:
    return SimpleNamespace(api_url=args.api_url.rstrip("/"), api_key=args.api_key, model_name=args.model, max_tokens=args.max_tokens,
                           temperature=0, reasoning="off", endpoint_type="openai")


def load_expected(path: Path) -> dict[tuple[str, int], str]:
    """CSV → {(Dateiname, Seite): Typ}"""
    out: dict[tuple[str, int], str] = {}
    text = path.read_text(encoding="utf-8-sig")
    delim = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    for row in csv.reader(text.splitlines(), delimiter=delim):
        if len(row) < 3 or not row[1].strip().isdigit():
            continue
        out[(row[0].strip(), int(row[1]))] = row[2].strip()
    return out


def score(pages_by_file: dict[str, list[pc.PageVerdict]], expected: dict[tuple[str, int], str]) -> dict:
    """Treffer je erwartetem Typ + Fehlerliste [(Datei, Seite, erwartet, erkannt)]"""
    per_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])      # typ → [richtig, gesamt]
    wrong: list[tuple[str, int, str, str]] = []
    for (name, page), typ in expected.items():
        verdicts = pages_by_file.get(name)
        if not verdicts or page > len(verdicts):
            continue
        got = verdicts[page - 1].typ
        per_type[typ][1] += 1
        if got == typ:
            per_type[typ][0] += 1
        else:
            wrong.append((name, page, typ, got))
    return {"per_type": dict(per_type), "wrong": wrong}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--api-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--expected", default=None, help="CSV mit der richtigen Antwort je Seite: datei;seite;typ")
    ap.add_argument("--n", type=int, default=30, help="Anzahl PDFs")
    ap.add_argument("--dpi", type=int, default=80)
    ap.add_argument("--chunk-size", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=8192)
    args = ap.parse_args()

    pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))[: args.n]
    if not pdfs:
        raise SystemExit("Keine PDFs gefunden")
    config = _config(args)
    expected = load_expected(Path(args.expected)) if args.expected else {}
    print(f"{len(pdfs)} PDF(s), Modell {args.model}, {args.dpi} dpi, Blöcke à {args.chunk_size} Seiten\n")

    seconds: list[float] = []
    pages_by_file: dict[str, list[pc.PageVerdict]] = {}
    unsure_count = failed = 0
    totals = Counter()
    for pdf in pdfs:
        images = pdf_service.pdf_to_base64_images(pdf, args.dpi, "JPEG", 70)
        t0 = time.monotonic()
        out = pc.classify(images, config, args.chunk_size)
        seconds.append(time.monotonic() - t0)
        if out.error:
            failed += 1
            print(f"✕ {pdf.name}: {out.error[:150]}")
            continue
        summary = pc.summarize(out.pages)
        groups = pc.invoice_groups(out.pages, summary)
        reasons = pc.uncertainty(out.pages, out.warnings, summary)
        pages_by_file[pdf.name] = out.pages
        totals.update(p.typ for p in out.pages)
        unsure_count += bool(reasons)
        print(f"{'⚠' if reasons else '✓'} {pdf.name}: {len(out.pages)} Seite(n), {len(groups)} Rechnung(en), {seconds[-1]:.1f} s")
        for line in pc.describe(out.pages):
            print("    " + line)
        for g in groups:
            print(f"    → Rechnung {g.number or '?'} ({g.sender or 'Absender unbekannt'}): Seite {pc.ranges_text(g.pages)}")
        for r in reasons:
            print(f"    ! unsicher: {r}")

    print("\n── Zusammenfassung ──")
    ok = len(pdfs) - failed
    print(f"PDFs: {ok} ausgewertet, {failed} Fehler; davon {unsure_count} als unsicher markiert (würden zur Nachbearbeitung gehen)")
    if seconds:
        print(f"Dauer je PDF: Ø {statistics.mean(seconds):.1f} s, Median {statistics.median(seconds):.1f} s, Gesamt {sum(seconds):.0f} s")
    print("Seitentypen: " + ", ".join(f"{k} {v}" for k, v in totals.most_common()))
    if expected:
        res = score(pages_by_file, expected)
        print("\nTreffer je Seitentyp (laut --expected):")
        for typ, (right, total) in sorted(res["per_type"].items()):
            print(f"  {typ:22s} {right}/{total}  ({100 * right / total:.0f} %)")
        total_right = sum(r for r, _ in res["per_type"].values())
        total_all = sum(t for _, t in res["per_type"].values())
        if total_all:
            print(f"  {'gesamt':22s} {total_right}/{total_all}  ({100 * total_right / total_all:.0f} %)")
        for name, page, want, got in res["wrong"][:40]:
            print(f"  ✕ {name} Seite {page}: erwartet {want}, erkannt {got}")


if __name__ == "__main__":
    main()
