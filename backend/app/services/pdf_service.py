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
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Maximale Bildbreite in Pixel.
# Bilder breiter als dieser Wert werden proportional verkleinert,
# um den Token-Verbrauch bei der KI-API zu reduzieren.
MAX_IMAGE_WIDTH_PX = 1400


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
    pdf = pdfium.PdfDocument(str(path))

    try:
        for page_idx in range(len(pdf)):
            logger.debug("  Rendere Seite %d/%d", page_idx + 1, len(pdf))

            # PDF-Seite rendern: scale = DPI / 72 (PDF-Standard ist 72 DPI)
            page = pdf[page_idx]
            scale = dpi / 72.0
            bitmap = page.render(scale=scale, rotation=0)

            # PDFium-Bitmap → PIL-Image
            pil_image = bitmap.to_pil()

            # Bild verkleinern, wenn es breiter als MAX_IMAGE_WIDTH_PX ist
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

            # Bild in Byte-Buffer schreiben
            buffer = io.BytesIO()
            if fmt == "JPEG":
                pil_image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
            else:
                pil_image.save(buffer, format="PNG", optimize=True)
            buffer.seek(0)

            # Als data:-URL kodieren (kompatibel mit OpenAI Vision API)
            b64 = base64.b64encode(buffer.read()).decode("utf-8")
            images_b64.append(f"data:{mime_type};base64,{b64}")

    except Exception as exc:
        logger.error("Fehler beim Rendern von '%s': %s", path.name, exc)
    finally:
        pdf.close()

    logger.info("PDF gerendert: %d Seiten als %s", len(images_b64), fmt)
    return images_b64
