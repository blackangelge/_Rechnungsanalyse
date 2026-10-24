"""
Seitenerkennung: die Vision-KI ordnet JEDER Seite eines PDFs einen Typ zu (Rechnung, Rechnungs-Folgeseite, Lieferschein, …).

So lässt sich bei Sammel-PDFs (1 Rechnungsseite + 98 Seiten Lieferscheine u. a.) die Rechnung finden — auch bei Scans, ohne OCR.
Die Seiten gehen in niedriger Auflösung in Blöcken zur KI; jedes Bild ist mit „Seite N:“ beschriftet, die Antwort ist ein
strukturiertes JSON (Structured Outputs) mit der Seitennummer. Je Block kommt die Vorseite als Kontext mit (wird nicht bewertet),
damit Folgeseiten über Blockgrenzen hinweg erkannt werden.

Reine Logik + `classify()` (synchroner KI-Aufruf → über `asyncio.to_thread`). Die Ergebnisse speichert der Workflow-Knoten in `document_pages`.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.document_page import PAGE_KINDS
from app.services import structured_ai

INVOICE_KINDS = ("rechnung", "rechnung_folgeseite")
# Seitentyp → Dokumententyp-ID (siehe routers/settings.py); 15 = Mischbeleg
KIND_TO_DOCTYPE = {"lieferschein": 3, "bestellung": 4, "angebot": 5, "mahnung": 7, "kontoauszug": 8, "vertrag": 9, "sonstiges": 14}
DOCTYPE_INVOICE, DOCTYPE_MIXED, DOCTYPE_UNKNOWN = 1, 15, 0

SYSTEM_PROMPT = """Du sortierst die Seiten eines PDFs (oft ein gescanntes Sammeldokument). Jede Seite bekommt genau einen Typ:
- rechnung: erste Seite einer Rechnung (Briefkopf, Rechnungsnummer/-datum, Positionen oder Summen)
- rechnung_folgeseite: Fortsetzung DERSELBEN Rechnung mit weiteren Positionen, Zwischen-/Endsummen, Steuer- oder Zahlungsangaben
- lieferschein, bestellung (Bestellung/Auftragsbestätigung), angebot, mahnung, kontoauszug, vertrag
- sonstiges: alles andere (Anschreiben, AGB, Merkblätter, Fotos, Zahlscheine …)
- leer: leere oder fast leere Seite, Trennblatt, Rückseite ohne Inhalt

Weitere Angaben je Seite:
- beginnt_neues_dokument: true, wenn die Seite ein neues eigenständiges Dokument beginnt (neuer Briefkopf, „Seite 1 von …“, neue Belegnummer), sonst false
- rechnungsnummer: die Rechnungsnummer exakt wie gedruckt, wenn auf DIESER Seite sichtbar, sonst null
- absender: Firmenname des Absenders, wenn sichtbar, sonst null
- hinweis: höchstens ein kurzer Satz, wenn die Zuordnung unsicher ist, sonst null

Regeln: Bewerte nur die Seiten, die als „Seite N:“ beschriftet sind; eine mit „Kontext“ beschriftete Seite dient nur der Orientierung und wird nicht bewertet.
Gib für jede zu bewertende Seite genau einen Eintrag mit ihrer Seitennummer zurück. Rate nicht — im Zweifel „sonstiges“ und ein Hinweis."""

USER_INTRO = "Bewerte die folgenden Seiten gemäß Schema."


class PageVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seite: int = Field(..., ge=1)
    typ: Literal[PAGE_KINDS]                                  # type: ignore[valid-type]
    beginnt_neues_dokument: bool
    rechnungsnummer: str | None
    absender: str | None
    hinweis: str | None


class PageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seiten: list[PageVerdict]


def response_format() -> dict:
    return {"type": "json_schema", "json_schema": {"name": "page_classification", "strict": True, "schema": PageBatch.model_json_schema()}}


@dataclass
class ClassifyOutcome:
    pages: list[PageVerdict] = field(default_factory=list)          # genau eine Zeile je Seite, nach Seitennummer
    stats: dict = field(default_factory=dict)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw_chunks: list[str] = field(default_factory=list)


def _merge_stats(a: dict, b: dict) -> dict:
    out = dict(a)
    for k in ("input_tokens", "output_tokens", "reasoning_tokens", "total_duration"):
        if b.get(k) is not None:
            out[k] = (out.get(k) or 0) + b[k]
    return out


def chunk_ranges(total: int, chunk_size: int) -> list[tuple[int, int]]:
    """[(1,10), (11,20), …] — 1-basiert, Enden inklusive."""
    return [(s, min(s + chunk_size - 1, total)) for s in range(1, total + 1, chunk_size)]


def build_user_content(images: list[str], start: int, end: int) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": USER_INTRO}]
    if start > 1:
        parts += [{"type": "text", "text": f"Kontext (nur zur Orientierung, NICHT bewerten) – Seite {start - 1}:"},
                  {"type": "image_url", "image_url": {"url": images[start - 2]}}]
    for n in range(start, end + 1):
        parts += [{"type": "text", "text": f"Seite {n}:"}, {"type": "image_url", "image_url": {"url": images[n - 1]}}]
    return parts


def classify(images: list[str], config, chunk_size: int = 10, extra_instruction: str | None = None, call=None) -> ClassifyOutcome:
    """
    Klassifiziert alle Seiten. Verbindungsfehler → `error` mit dem gewohnten Präfix („KI-Verbindungsfehler: …“), nichts wird geraten.
    Eine einzelne unbrauchbare Antwort macht nur ihren Block zu „sonstiges“ (mit Hinweis); sind ALLE Blöcke unbrauchbar, ist es ein Fehler.
    `call` ersetzt den KI-Aufruf (Tests).
    """
    call = call or structured_ai.call_chat
    system = SYSTEM_PROMPT + (f"\n\nZUSATZANWEISUNG:\n{extra_instruction.strip()}" if extra_instruction and extra_instruction.strip() else "")
    fmt = response_format()
    total = len(images)
    out = ClassifyOutcome()
    verdicts: dict[int, PageVerdict] = {}
    bad_chunks = 0
    for start, end in chunk_ranges(total, chunk_size):
        result = call(config, system, build_user_content(images, start, end), fmt)
        out.stats = _merge_stats(out.stats, result.ki_stats)
        out.raw_chunks.append(result.raw)
        if result.error and _is_conn_error(result.error):
            out.error = result.error
            return out
        try:
            if result.parsed is None:
                raise ValueError(result.error or "keine Antwort")
            batch = PageBatch.model_validate(result.parsed)
        except (ValidationError, ValueError) as exc:
            bad_chunks += 1
            out.warnings.append(f"Seite {start}–{end}: Antwort unbrauchbar ({str(exc)[:120]})")
            continue
        for v in batch.seiten:
            if start <= v.seite <= end and v.seite not in verdicts:
                verdicts[v.seite] = v
    if bad_chunks == len(chunk_ranges(total, chunk_size)) and total:
        out.error = "Die KI lieferte keine verwertbare Seitenklassifikation: " + "; ".join(out.warnings[:3])
        return out
    for n in range(1, total + 1):
        if n not in verdicts:
            verdicts[n] = PageVerdict(seite=n, typ="sonstiges", beginnt_neues_dokument=False, rechnungsnummer=None, absender=None,
                                      hinweis="nicht klassifiziert")
            out.warnings.append(f"Seite {n} nicht klassifiziert")
    out.pages = [verdicts[n] for n in range(1, total + 1)]
    return out


def _is_conn_error(text: str) -> bool:
    return text.startswith(("KI-Verbindungsfehler", "KI-Timeout", "KI überlastet", "Unerwarteter KI-Fehler"))


# ─── Auswertung ──────────────────────────────────────────────────────────────

@dataclass
class Segment:
    first: int
    last: int
    kind: str                       # häufigster inhaltlicher Typ im Abschnitt
    invoice_number: str | None
    sender: str | None


@dataclass
class Summary:
    total: int
    invoice_pages: list[int]
    other_pages: list[int]
    empty_pages: list[int]
    document_type_id: int
    mixed: bool
    segments: list[Segment]


def summarize(pages: list[PageVerdict]) -> Summary:
    invoice = [p.seite for p in pages if p.typ in INVOICE_KINDS]
    empty = [p.seite for p in pages if p.typ == "leer"]
    other = [p.seite for p in pages if p.typ not in INVOICE_KINDS and p.typ != "leer"]
    segments: list[Segment] = []
    cur: list[PageVerdict] = []

    def flush() -> None:
        if not cur:
            return
        kinds = Counter(p.typ for p in cur if p.typ != "leer") or Counter(["leer"])
        # Rechnungstyp zählt als „rechnung“, auch wenn die Mehrzahl Folgeseiten sind
        main = "rechnung" if any(p.typ in INVOICE_KINDS for p in cur) else kinds.most_common(1)[0][0]
        segments.append(Segment(
            cur[0].seite, cur[-1].seite, main,
            next((p.rechnungsnummer for p in cur if p.rechnungsnummer), None),
            next((p.absender for p in cur if p.absender), None),
        ))

    for p in pages:
        boundary = bool(cur) and (p.beginnt_neues_dokument or ((p.typ in INVOICE_KINDS) != (cur[-1].typ in INVOICE_KINDS) and p.typ != "leer" and cur[-1].typ != "leer"))
        if boundary:
            flush()
            cur = []
        cur.append(p)
    flush()

    if invoice and other:
        doc_type, mixed = DOCTYPE_MIXED, True
    elif invoice:
        doc_type, mixed = DOCTYPE_INVOICE, False
    elif other:
        top = Counter(next(p.typ for p in pages if p.seite == n) for n in other).most_common(1)[0][0]
        doc_type, mixed = KIND_TO_DOCTYPE.get(top, 14), False
    else:
        doc_type, mixed = DOCTYPE_UNKNOWN, False
    return Summary(len(pages), invoice, other, empty, doc_type, mixed, segments)


def ranges_text(pages: list[int]) -> str:
    """[1,2,3,7] → '1–3, 7'"""
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}–{pages[j]}")
        i = j + 1
    return ", ".join(out)


def describe(pages: list[PageVerdict]) -> list[str]:
    """Kompakte Zeilen fürs Ablaufprotokoll: aufeinanderfolgende Seiten gleichen Typs zusammengefasst."""
    lines, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1].typ == pages[i].typ and not pages[j + 1].beginnt_neues_dokument:
            j += 1
        nr = next((p.rechnungsnummer for p in pages[i:j + 1] if p.rechnungsnummer), None)
        lines.append(f"Seite {ranges_text(list(range(pages[i].seite, pages[j].seite + 1)))}: {pages[i].typ}" + (f" (Rechnungsnr. {nr})" if nr else ""))
        i = j + 1
    return lines
