"""
Vergleich Altpfad (Freitext-JSON) und Schemapfad (Structured Outputs) auf echten Belegen.

    python -m app.scripts.bench_extraction --pdf-dir /pfad/zu/pdfs --api-url http://KI-RECHNER:8000 --model nvidia/Qwen3.6-35B-A3B-NVFP4 \
        --legacy-prompt-file prompt.txt [--n 20] [--rounds 2] [--max-tokens 8192]

Je Beleg und Runde werden beide Pfade nacheinander aufgerufen; gemessen werden Dauer, Eingabe-/Ausgabe-Token und ob die Antwort
verwertbar war (Altpfad: JSON parsbar und Positionen erkannt, Schemapfad: Antwort besteht die Schema-Validierung). Die erste Runde
füllt das Prefix-Caching, ab der zweiten zeigt sich dessen Wirkung — deshalb standardmäßig 2 Runden. Es wird NICHTS gespeichert.
Benötigt eine Datenbank mit angelegtem Schema (DATABASE_URL) und eine erreichbare OpenAI-kompatible API (vLLM).
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

from app.database import SessionLocal
from app.services import ai_service, pdf_service, schema_extraction, structured_ai
from app.services.schema_pipeline import SchemaValidationFailed, validate_output


def _config(args) -> SimpleNamespace:
    return SimpleNamespace(
        api_url=args.api_url.rstrip("/"), api_key=args.api_key, model_name=args.model, max_tokens=args.max_tokens, temperature=0,
        reasoning="off", endpoint_type="openai",
    )


def _summary(name: str, rows: list[dict]) -> str:
    if not rows:
        return f"{name}: keine Messwerte"
    secs = [r["seconds"] for r in rows]
    ok = sum(1 for r in rows if r["ok"])
    tin = [r["in"] for r in rows if r["in"]]
    tout = [r["out"] for r in rows if r["out"]]
    return (
        f"{name}: {len(rows)} Aufrufe, verwertbar {ok}/{len(rows)}, Ø {statistics.mean(secs):.1f} s, Median {statistics.median(secs):.1f} s, "
        f"Gesamt {sum(secs):.0f} s, Ø Eingabe-Token {statistics.mean(tin) if tin else 0:.0f}, Ø Ausgabe-Token {statistics.mean(tout) if tout else 0:.0f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--api-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--legacy-prompt-file", required=True, help="Extraktionsprompt des Altpfads (Textdatei)")
    ap.add_argument("--n", type=int, default=10, help="Anzahl Belege")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))[: args.n]
    if not pdfs:
        raise SystemExit("Keine PDFs gefunden")
    config = _config(args)
    legacy_prompt = Path(args.legacy_prompt_file).read_text(encoding="utf-8")
    db = SessionLocal()
    try:
        prepared = schema_extraction.prepare(db)
    finally:
        db.close()
    print(f"{len(pdfs)} Belege, {args.rounds} Runde(n), Schema v{prepared.version_number}, Modell {args.model}")

    legacy_rows: list[dict] = []
    schema_rows: list[dict] = []
    for rnd in range(1, args.rounds + 1):
        for pdf in pdfs:
            images = pdf_service.pdf_to_base64_images(pdf, args.dpi, "png", 85)
            t0 = time.monotonic()
            fields, positions, raw, stats = ai_service.extract_invoice_data(images, config, legacy_prompt)
            legacy_rows.append({"round": rnd, "seconds": time.monotonic() - t0, "ok": bool(positions or fields),
                                "in": (stats or {}).get("input_tokens"), "out": (stats or {}).get("output_tokens")})
            t0 = time.monotonic()
            res = structured_ai.call_structured(config, prepared.system_prompt, prepared.user_text, images, prepared.response_format)
            valid = False
            if res.parsed is not None:
                try:
                    validate_output(prepared.built, res.parsed)
                    valid = True
                except SchemaValidationFailed:
                    pass
            schema_rows.append({"round": rnd, "seconds": time.monotonic() - t0, "ok": valid,
                                "in": res.ki_stats.get("input_tokens"), "out": res.ki_stats.get("output_tokens")})
            print(f"  Runde {rnd} {pdf.name}: alt {legacy_rows[-1]['seconds']:.1f} s / Schema {schema_rows[-1]['seconds']:.1f} s"
                  f"{'' if valid else '  (Schema-Antwort nicht verwertbar: ' + str(res.error or 'Validierung') + ')'}")
    print()
    for rnd in range(1, args.rounds + 1):
        print(f"Runde {rnd}")
        print("  " + _summary("Altpfad ", [r for r in legacy_rows if r["round"] == rnd]))
        print("  " + _summary("Schema  ", [r for r in schema_rows if r["round"] == rnd]))


if __name__ == "__main__":
    main()
