"""
Führt einen Analyse-Workflow für ein Dokument aus.

Die Knoten (Handler) nutzen dieselben Bausteine wie der klassische Ablauf in
routers/documents.py (DB-Hilfen, KI-Aufrufe, Plausibilitätsprüfung); der Workflow
bestimmt nur, WELCHE Schritte in WELCHER Reihenfolge mit WELCHEN Prompts laufen.

Wie im klassischen Ablauf gilt: blockierende Arbeit (DB, KI-HTTP, PDF) läuft in Threads,
Dokumente werden nacheinander verarbeitet.

KI-Verbindungsfehler (Timeout, 429, Netzwerk) lassen den Ablauf abbrechen und liefern "ai_unavailable":
der Worker reiht den Task dann ohne Strafversuch neu ein — wie im klassischen Ablauf wird ein Beleg nie
wegen einer nicht erreichbaren KI auf "error" gesetzt.

Abschluss nach dem Ablauf (wie im klassischen Ablauf):
  • Extraktion lief   → Ergebnis + Plausibilität speichern (_db_analyze_write)
  • nur Typ erkannt   → Typ speichern, fertig ohne Extraktion (_db_type_only_finish)
  • sonst             → Status "done"
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import crud
from app.database import SessionLocal
from app.models.document import Document
from app.services import ai_service, pdf_edit_service, pdf_service, workflow_service
from app.services.ai_url import build_ai_url
from app.services.workflow_service import NodeResult, WorkflowError

logger = logging.getLogger(__name__)


def _docs():
    """Lazy-Import: routers.documents importiert dieses Modul erst zur Laufzeit."""
    from app.routers import documents
    return documents


class AIUnavailable(Exception):
    """KI-Endpunkt nicht erreichbar — der Task soll später erneut versucht werden."""


@dataclass
class RunContext:
    doc_id: int
    original_filename: str
    batch_id: int | None
    pdf_path: Path
    img_dpi: int
    img_format: str
    img_quality: int
    ai_proxy: Any                         # Standard-KI-Konfiguration der Analyse
    ai_model_name: str
    default_prompt_text: str | None       # in der Analyse gewählter / Standard-Extraktionsprompt
    doc_type_prompt_text: str | None      # Dokumententyp-Prompt aus den Einstellungen
    document_types: list[dict]
    known_supplier_name: str | None = None

    # Laufzeitzustand
    images: list | None = None
    page_count: int = 0
    type_detected: bool = False
    document_type_id: int | None = None
    document_type_name: str | None = None
    extracted: bool = False
    fields: dict = field(default_factory=dict)
    positions: list = field(default_factory=list)
    raw: str = ""
    ki_stats: dict = field(default_factory=dict)
    validation: dict | None = None
    extract_proxy: Any = None
    used_extract_prompt: str | None = None

    async def ensure_images(self) -> list:
        """Rendert die PDF beim ersten Bedarf (und nach einer Änderung der PDF erneut)."""
        if self.images is None:
            docs = _docs()
            self.images = await docs._run_ki_io(
                pdf_service.pdf_to_base64_images, self.pdf_path,
                self.img_dpi, self.img_format, self.img_quality,
            )
            if not self.images:
                raise RuntimeError("PDF konnte nicht gerendert werden")
            self.page_count = len(self.images)
        return self.images

    def add_stats(self, stats: dict | None) -> None:
        self.ki_stats = _docs()._merge_ki_stats(self.ki_stats, stats or {}) if (stats or self.ki_stats) else {}


# ─── DB-Helfer (sync, für asyncio.to_thread) ──────────────────────────────────

def _db_prompt_text(prompt_id: int) -> str:
    db = SessionLocal()
    try:
        prompt = crud.system_prompt.get_by_id(db, prompt_id)
        if prompt is None:
            raise RuntimeError(f"Systemprompt #{prompt_id} existiert nicht mehr")
        return prompt.content
    finally:
        db.close()


def _db_ai_proxy(config_id: int):
    db = SessionLocal()
    try:
        cfg = crud.ai_config.get_by_id(db, config_id)
        if cfg is None:
            raise RuntimeError(f"KI-Konfiguration #{config_id} existiert nicht mehr")
        return _make_proxy(
            api_url=build_ai_url(cfg.ip_address, cfg.port), api_key=cfg.api_key, model_name=cfg.model_name,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature,
            reasoning=getattr(cfg, "reasoning", "off") or "off",
            endpoint_type=getattr(cfg, "endpoint_type", "openai") or "openai",
        )
    finally:
        db.close()


def _make_proxy(**kw):
    class _Proxy:
        pass
    p = _Proxy()
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _db_trim(doc_id: int, max_pages: int) -> tuple[str | None, int]:
    """Kürzt die PDF (neue Version). Gibt (Beschreibung|None, aktuelle Seitenzahl) zurück."""
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        version = pdf_edit_service.auto_trim(db, doc, max_pages)
        if version is not None:
            doc.analyzed_pdf_version = doc.pdf_version
            db.commit()
        pages = pdf_service.get_page_count(pdf_edit_service.current_path(doc))
        return (version.description if version else None), pages
    finally:
        db.close()


def _db_save_prompts(doc_id: int, system_prompt: str | None = None, doc_type_prompt: str | None = None) -> None:
    db = SessionLocal()
    try:
        crud.document.set_used_prompt(db, doc_id, system_prompt, doc_type_prompt)
    except Exception as exc:
        db.rollback()
        logger.warning("Prompt-Snapshot für #%d nicht gespeichert: %s", doc_id, exc)
    finally:
        db.close()


def _db_save_run(doc_id: int, wf: dict, trace: list[dict]) -> None:
    db = SessionLocal()
    try:
        crud.document.save_workflow_run(db, doc_id, wf["id"], wf["name"], wf["graph"], trace)
    except Exception as exc:
        db.rollback()
        logger.warning("Workflow-Protokoll für #%d nicht gespeichert: %s", doc_id, exc)
    finally:
        db.close()


def _db_finish_empty(doc_id: int, page_count: int, batch_id: int | None, filename: str) -> None:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is not None:
            if page_count > 0:
                doc.page_count = page_count
            doc.status = "done"
            db.commit()
        logger.info("Workflow abgeschlossen ohne Extraktion: %s", filename)
    finally:
        db.close()


# ─── Knoten ───────────────────────────────────────────────────────────────────

async def _h_start(cfg, ctx: RunContext):
    return None


async def _h_end(cfg, ctx: RunContext):
    return None


async def _h_trim(cfg, ctx: RunContext):
    desc, pages = await asyncio.to_thread(_db_trim, ctx.doc_id, cfg.max_pages)
    if desc:
        ctx.images = None             # PDF hat sich geändert → neu rendern
        ctx.page_count = pages
        return NodeResult(detail=desc)
    ctx.page_count = pages
    return NodeResult(detail=f"nichts zu kürzen ({pages} Seite(n) ≤ {cfg.max_pages})")


async def _h_detect(cfg, ctx: RunContext):
    if not ctx.document_types:
        raise RuntimeError("Es sind keine Dokumententypen vorhanden")
    if cfg.system_prompt_id:
        prompt = await asyncio.to_thread(_db_prompt_text, cfg.system_prompt_id)
    else:
        prompt = ctx.doc_type_prompt_text
    if not prompt:
        raise RuntimeError(
            "Kein Dokumententyp-Prompt konfiguriert — bitte unter Einstellungen → Systemprompts "
            "einen Prompt vom Typ 'Dokumententyp-Erkennung' anlegen oder im Knoten einen auswählen."
        )
    images = await ctx.ensure_images()
    type_id, type_name, _raw, stats = await asyncio.to_thread(
        ai_service.detect_document_type, images, ctx.ai_proxy, ctx.document_types, prompt,
    )
    if _docs()._is_ai_conn_error(_raw):
        raise AIUnavailable(f"KI nicht erreichbar bei der Typ-Erkennung: {_raw[:120]}")
    ctx.type_detected = True
    ctx.document_type_id, ctx.document_type_name = type_id, type_name
    ctx.add_stats(stats)
    docs = _docs()
    await asyncio.to_thread(docs._db_save_document_type, ctx.doc_id, type_id)
    await asyncio.to_thread(_db_save_prompts, ctx.doc_id, None, prompt)
    return NodeResult(detail=f"Typ: {type_name or 'unbekannt'}")


async def _h_branch(cfg, ctx: RunContext):
    facts = {
        "document_type_id": ctx.document_type_id,
        "document_type_name": ctx.document_type_name,
        "supplier_name": (ctx.fields.get("supplier_name") if ctx.fields else None) or ctx.known_supplier_name,
        "page_count": ctx.page_count or None,
        "validation_status": (ctx.validation or {}).get("status"),
    }
    result = workflow_service.evaluate_condition(cfg, facts)
    shown = facts.get(
        {"document_type": "document_type_name", "supplier_name": "supplier_name",
         "page_count": "page_count", "validation_status": "validation_status"}[cfg.field]
    )
    return NodeResult("true" if result else "false", f"{cfg.field} = {shown!r} → {'Ja' if result else 'Nein'}")


async def _h_extract(cfg, ctx: RunContext):
    prompt = (
        await asyncio.to_thread(_db_prompt_text, cfg.system_prompt_id)
        if cfg.system_prompt_id else ctx.default_prompt_text
    )
    proxy = (
        await asyncio.to_thread(_db_ai_proxy, cfg.ai_config_id) if cfg.ai_config_id else ctx.ai_proxy
    )
    images = await ctx.ensure_images()
    fields, positions, raw, stats = await asyncio.to_thread(
        ai_service.extract_invoice_data, images, proxy, prompt,
    )
    if _docs()._is_ai_conn_error(raw):
        raise AIUnavailable(f"KI nicht erreichbar bei der Extraktion: {raw[:120]}")
    ctx.fields, ctx.positions, ctx.raw = fields, positions, raw
    ctx.extracted, ctx.extract_proxy = True, proxy
    ctx.used_extract_prompt = prompt
    ctx.validation = None
    ctx.add_stats(stats)
    await asyncio.to_thread(_db_save_prompts, ctx.doc_id, ctx.used_extract_prompt, None)
    return NodeResult(detail=f"{len(positions)} Position(en), Modell {getattr(proxy, 'model_name', '?')}")


async def _h_validate(cfg, ctx: RunContext):
    if not ctx.extracted:
        return NodeResult(detail="übersprungen — keine Extraktion vorhanden")
    docs = _docs()
    images = await ctx.ensure_images() if cfg.ai_recheck else []
    fields, positions, raw, stats, validation = await docs._plausibility_pass(
        ctx.doc_id, images, ctx.extract_proxy or ctx.ai_proxy, ctx.used_extract_prompt,
        ctx.fields, ctx.positions, ctx.raw, ctx.ki_stats,
        auto_correct=cfg.auto_correct, ai_recheck=cfg.ai_recheck,
    )
    ctx.fields, ctx.positions, ctx.raw, ctx.ki_stats, ctx.validation = fields, positions, raw, stats, validation
    return NodeResult(detail=f"Status: {(validation or {}).get('status', 'nicht prüfbar')}")


async def _h_normalize(cfg, ctx: RunContext):
    if not ctx.extracted:
        return NodeResult(detail="übersprungen — keine Extraktion vorhanden")
    from app.services import plausibility_service as ps
    raw = docs_parse_raw(ctx.raw)
    result = ps.ValidationResult(status=ps.STATUS_UNCHECKED)
    ps._normalize_mass_to_tonnes(result, ctx.positions, ps._raw_positions(raw), cfg.target)
    if ctx.validation is not None and result.normalizations:
        ctx.validation["normalizations"] = (ctx.validation.get("normalizations") or []) + result.normalizations
    n = len(result.normalizations)
    return NodeResult(detail=f"{n} Position(en) auf {cfg.target} umgerechnet" if n else f"nichts umzurechnen (Zieleinheit {cfg.target})")


def docs_parse_raw(raw: str) -> dict | None:
    return _docs()._parse_raw(raw)


async def _h_refine(cfg, ctx: RunContext):
    if not cfg.system_prompt_id:
        raise RuntimeError("Kein Systemprompt gewählt — bitte im Knoten „KI-Nachbearbeitung“ einen Prompt auswählen")
    prompt = await asyncio.to_thread(_db_prompt_text, cfg.system_prompt_id)
    proxy = await asyncio.to_thread(_db_ai_proxy, cfg.ai_config_id) if cfg.ai_config_id else (ctx.extract_proxy or ctx.ai_proxy)
    if ctx.extracted and ctx.raw:
        prompt = (
            f"{prompt}\n\nBISHERIGE AUSLESUNG (JSON aus dem vorherigen Schritt — bearbeite sie gemäß der Anweisung "
            f"und antworte vollständig im selben JSON-Format):\n{ctx.raw[:20000]}"
        )
    images = await ctx.ensure_images() if cfg.include_images else []
    fields, positions, raw, stats = await asyncio.to_thread(ai_service.extract_invoice_data, images, proxy, prompt)
    if _docs()._is_ai_conn_error(raw):
        raise AIUnavailable(f"KI nicht erreichbar bei der Nachbearbeitung: {raw[:120]}")
    ctx.add_stats(stats)
    if not (fields or positions):
        return NodeResult(detail="KI lieferte kein verwertbares Ergebnis — Daten unverändert")
    ctx.fields, ctx.positions, ctx.raw = fields, positions, raw
    ctx.extracted, ctx.extract_proxy = True, proxy
    ctx.validation = None             # muss erneut geprüft werden
    return NodeResult(detail=f"{len(positions)} Position(en), Modell {getattr(proxy, 'model_name', '?')}")


HANDLERS = {
    "start": _h_start, "end": _h_end, "trim_pages": _h_trim, "detect_type": _h_detect,
    "branch": _h_branch, "extract": _h_extract, "validate": _h_validate,
    "normalize_units": _h_normalize, "ai_refine": _h_refine,
}


# ─── Einstieg ─────────────────────────────────────────────────────────────────

async def analyze_with_workflow(
    doc_id: int, ai_config_id: int, system_prompt_id: int | None, workflow: dict,
) -> str:
    """
    Analysiert ein Dokument gemäß `workflow` ({id, name, graph, source}).

    Rückgabe wie `_analyze_single`: "ok" oder "ai_unavailable" (Task wird neu eingereiht).
    """
    docs = _docs()
    data = await asyncio.to_thread(docs._db_analyze_read, doc_id, ai_config_id, system_prompt_id)
    if data is None:
        return "ok"
    system_prompt_text = data["system_prompt_text"]

    # Prompt-Snapshot zurücksetzen (setzt auch frühere Workflow-Angaben zurück);
    # die Knoten tragen die tatsächlich verwendeten Prompts nach.
    await asyncio.to_thread(docs._db_save_used_prompts, doc_id, None, None)
    # Kürzen übernimmt der Workflow (Knoten) — die globale Auto-Kürzen-Einstellung gilt hier nicht.
    await asyncio.to_thread(docs._db_prepare_pdf, doc_id, data["original_filename"], data["batch_id"], False)

    proxy = _make_proxy(
        api_url=data["ai_api_url"], api_key=data["ai_api_key"], model_name=data["ai_model_name"],
        max_tokens=data["ai_max_tokens"], temperature=data["ai_temperature"],
        reasoning=data["ai_reasoning"], endpoint_type=data["ai_endpoint_type"],
    )
    ctx = RunContext(
        doc_id=doc_id, original_filename=data["original_filename"], batch_id=data["batch_id"],
        pdf_path=data["pdf_path"], img_dpi=data["img_dpi"], img_format=data["img_format"],
        img_quality=data["img_quality"], ai_proxy=proxy, ai_model_name=data["ai_model_name"],
        default_prompt_text=system_prompt_text, doc_type_prompt_text=data["doc_type_prompt_text"],
        document_types=data["document_types"],
        known_supplier_name=await asyncio.to_thread(_db_known_supplier, doc_id),
    )
    try:
        ctx.page_count = await asyncio.to_thread(pdf_service.get_page_count, ctx.pdf_path)
    except Exception as exc:
        logger.warning("Seitenzahl von #%d nicht lesbar: %s", doc_id, exc)

    await asyncio.to_thread(
        _db_log, doc_id, ctx.batch_id,
        f"Workflow '{workflow['name']}' gestartet ({workflow['source']}): {ctx.original_filename}",
    )
    trace: list[dict] = []
    try:
        trace = await workflow_service.run_graph(workflow["graph"], HANDLERS, ctx)
    except WorkflowError as exc:
        ctx.images = None
        if isinstance(exc.__cause__, AIUnavailable):
            logger.warning("Workflow '%s' #%d: %s — wird erneut versucht", workflow["name"], doc_id, exc.__cause__)
            return "ai_unavailable"
        await asyncio.to_thread(_db_save_run, doc_id, workflow, exc.trace)
        await asyncio.to_thread(
            docs._set_error, doc_id, f"Workflow '{workflow['name']}' — Knoten '{exc.node_label}': {exc}",
        )
        return "ok"
    except AIUnavailable as exc:
        ctx.images = None
        logger.warning("Workflow '%s' #%d: %s — wird erneut versucht", workflow["name"], doc_id, exc)
        return "ai_unavailable"
    except Exception as exc:  # unerwartet (z.B. defekte Konfiguration)
        logger.exception("Workflow-Fehler bei #%d", doc_id)
        ctx.images = None
        await asyncio.to_thread(docs._set_error, doc_id, f"Workflow-Fehler: {exc}")
        return "ok"

    if ctx.images:
        ctx.page_count = len(ctx.images)
    ctx.images = None

    if ctx.extracted:
        await asyncio.to_thread(
            docs._db_analyze_write, doc_id, ctx.original_filename, ctx.batch_id, ctx.ai_model_name,
            ctx.page_count, ctx.fields, ctx.positions, ctx.raw, ctx.ki_stats,
            ctx.document_type_id, ctx.validation,
        )
    elif ctx.type_detected:
        await asyncio.to_thread(
            docs._db_type_only_finish, doc_id, ctx.document_type_id, ctx.document_type_name,
            ctx.page_count, ctx.batch_id, ctx.original_filename, ctx.ki_stats,
        )
    else:
        await asyncio.to_thread(_db_finish_empty, doc_id, ctx.page_count, ctx.batch_id, ctx.original_filename)
    await asyncio.to_thread(_db_save_run, doc_id, workflow, trace)
    return "ok"


def _db_known_supplier(doc_id: int) -> str | None:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        return doc.supplier_name if doc is not None else None
    finally:
        db.close()


def _db_log(doc_id: int, batch_id: int | None, message: str) -> None:
    logger.info("#%d: %s", doc_id, message)
