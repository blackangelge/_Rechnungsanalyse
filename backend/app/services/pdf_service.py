"""
PDF-Service: Seitenanzahl auslesen und PDF-Seiten in Bilder umwandeln.

pypdf      → Seitenanzahl ohne Rendering (schnell, kein Systempaket nötig)
pypdfium2  → PDF-Seiten in Bilder rendern (kein Poppler nötig, bundled PDFium)
Pillow     → Bildgröße optimieren + Bildformat-Konvertierung + Base64-Kodierung

Die Konvertierungsparameter (DPI, Format, Qualität) werden von außen übergeben
und stammen aus der konfigurierbaren ImageSettings-Datenbanktabelle.
"""

import base64
import io
import logging
import threading
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Maximale Bildbreite in Pixel.
# Bilder breiter als dieser Wert werden proportional verkleinert,
# um den Token-Verbrauch bei der KI-API zu reduzieren.
MAX_IMAGE_WIDTH_PX = 1400

# PDFium (die von pypdfium2 gebundene C-Bibliothek) ist NICHT thread-sicher — laut
# eigener Doku darf zu keinem Zeitpunkt von mehr als einem Thread gleichzeitig auf
# PDFium zugegriffen werden, auch nicht auf unterschiedliche PdfDocument-Instanzen.
# Da pdf_to_base64_images() bei hoher Worker-Konkurrenz (z.B. 20+ parallele Belege)
# aus vielen Threads gleichzeitig aufgerufen wird (_run_ki_io → _KI_IO_EXECUTOR),
# führte das ohne Sperre zu internen PDFium-Zustandsfehlern ("Failed to load document
# (PDFium: Success)" — ein in sich widersprüchlicher Fehler, typisches Symptom von
# Thread-Korruption) bis hin zu einem Segfault, der den gesamten Worker-Prozess ohne
# Absturz-Log lautlos beendete (Symptom: Worker "hängt", keine Fehlermeldung, Prozess
# im Container wird zum Zombie). Diese Sperre serialisiert NUR den kurzen
# PDFium-Rendering-Schritt — der Rest der Pipeline (KI-HTTP-Anfragen, JSON-Encoding,
# DB-Zugriffe) bleibt vollständig parallel; das PDF-Rendering selbst dauert nur
# Millisekunden pro Seite und ist praktisch nie der Flaschenhals.
_PDFIUM_LOCK = threading.Lock()


def get_page_count(pdf_path: str | Path) -> int:
    """
    Liest die Seitenanzahl eines PDFs aus, ohne es zu rendern.

    Args:
        pdf_path: Absoluter Pfad zur PDF-Datei.

    Returns:
        Anzahl der Seiten (mindestens 0).
    """
    path = Path(pdf_path)
    logger.debug("Lese Seitenanzahl aus: %s", path.name)
    reader = PdfReader(str(path))
    count = len(reader.pages)
    logger.debug("Seitenanzahl: %d", count)
    return count


def pdf_to_base64_images(
    pdf_path: str | Path,
    dpi: int = 150,
    image_format: str = "PNG",
    jpeg_quality: int = 85,
) -> list[str]:
    """
    Rendert alle Seiten einer PDF-Datei als Bilder und gibt sie als
    Base64-kodierte Strings zurück.

    Die Bilder können direkt als data:image/...;base64,...-URLs in
    OpenAI-kompatiblen Vision-API-Anfragen verwendet werden.

    Args:
        pdf_path: Absoluter Pfad zur PDF-Datei.
        dpi: Renderauflösung in Pixel pro Zoll.
              Typische Werte: 72 (schnell), 150 (Standard), 300 (hochwertig).
        image_format: Ausgabeformat — "PNG" (verlustfrei) oder "JPEG" (komprimiert).
        jpeg_quality: JPEG-Kompressionsqualität 1–100 (nur bei image_format="JPEG").
                      85 ist ein gutes Gleichgewicht zwischen Qualität und Dateigröße.

    Returns:
        Liste von Base64-kodierten Bild-Strings (eine Eintrag pro Seite).
        Leere Liste bei Fehlern.
    """
    path = Path(pdf_path)
    fmt = image_format.upper()
    # MIME-Typ für die data:-URL bestimmen
    mime_type = "image/jpeg" if fmt == "JPEG" else "image/png"

    logger.info(
        "Rendere PDF '%s': DPI=%d, Format=%s%s",
        path.name,
        dpi,
        fmt,
        f", Qualität={jpeg_quality}" if fmt == "JPEG" else "",
    )

    images_b64: list[str] = []

    # Gesamter PDFium-Zugriff (Öffnen, Rendern, Schließen) unter EINER Sperre —
    # siehe Kommentar bei _PDFIUM_LOCK oben. Nur dieser Block ist serialisiert,
    # alles andere (Bildverkleinerung, JPEG/PNG-Encoding, Base64) läuft danach
    # außerhalb der Sperre und damit weiterhin voll parallel.
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            raw_bitmaps = []
            for page_idx in range(len(pdf)):
                logger.debug("  Rendere Seite %d/%d", page_idx + 1, len(pdf))
                page = pdf[page_idx]
                scale = dpi / 72.0
                bitmap = page.render(scale=scale, rotation=0)
                # .copy() ist Pflicht: laut pypdfium2-Doku teilt sich to_pil() für
                # RGBA/RGBX/L-Puffer den Speicher mit dem PDFium-Bitmap-Objekt statt
                # ihn zu kopieren. Ohne .copy() würde das PIL-Image nach pdf.close()
                # (unten, noch innerhalb der Sperre) auf bereits freigegebenen Speicher
                # zeigen — der eigentliche Grund, warum die Nachbearbeitung erst NACH
                # dem Freigeben von PDFium (außerhalb der Sperre) passieren darf.
                raw_bitmaps.append(bitmap.to_pil().copy())
        except Exception as exc:
            logger.error("Fehler beim Rendern von '%s': %s", path.name, exc)
            raw_bitmaps = []
        finally:
            pdf.close()

    # Nachbearbeitung (Skalierung, Format-Konvertierung, Encoding) bewusst AUSSERHALB
    # der PDFium-Sperre — betrifft nur Pillow/base64 (thread-sicher), blockiert also
    # keine anderen gleichzeitig wartenden PDF-Renderings mehr.
    for pil_image in raw_bitmaps:
        if pil_image.width > MAX_IMAGE_WIDTH_PX:
            ratio = MAX_IMAGE_WIDTH_PX / pil_image.width
            new_height = int(pil_image.height * ratio)
            pil_image = pil_image.resize(
                (MAX_IMAGE_WIDTH_PX, new_height), Image.LANCZOS
            )
            logger.debug(
                "  Bild skaliert auf %dx%d px", MAX_IMAGE_WIDTH_PX, new_height
            )

        # Bei JPEG: RGB-Modus erzwingen (PDFium kann RGBA liefern, JPEG hat kein Alpha)
        if fmt == "JPEG" and pil_image.mode in ("RGBA", "LA", "P"):
            pil_image = pil_image.convert("RGB")

        buffer = io.BytesIO()
        if fmt == "JPEG":
            pil_image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        else:
            pil_image.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)

        b64 = base64.b64encode(buffer.read()).decode("utf-8")
        images_b64.append(f"data:{mime_type};base64,{b64}")

    logger.info("PDF gerendert: %d Seiten als %s", len(images_b64), fmt)
    return images_b64


def render_page_thumbnail(pdf_path: str | Path, page_number: int, width_px: int = 240) -> bytes:
    """
    Rendert eine einzelne Seite (1-basiert) als kleines PNG für die Seitenauswahl.

    Der PDFium-Zugriff steht — wie überall — unter _PDFIUM_LOCK (PDFium ist nicht thread-sicher);
    Skalierung und PNG-Encoding laufen danach außerhalb der Sperre.

    Raises:
        IndexError: Seitenzahl außerhalb des Dokuments.
    """
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(Path(pdf_path)))
        try:
            if page_number < 1 or page_number > len(pdf):
                raise IndexError(f"Seite {page_number} existiert nicht (1–{len(pdf)})")
            page = pdf[page_number - 1]
            scale = width_px / max(page.get_width(), 1.0)
            image = page.render(scale=scale).to_pil().convert("RGB").copy()
        finally:
            pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
