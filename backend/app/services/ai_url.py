"""
Gemeinsame URL-Bildung für KI-Endpunkte (IP/Hostname/volle URL + Port → Basis-URL).

Wird sowohl von der Analyse-Pipeline (app/routers/documents.py, über den
_ConfigProxy in _analyze_single_inner) als auch vom Worker-Dispatcher
(app/worker/worker.py, AIServer.url — nur für /status) verwendet.

Beide MÜSSEN dieselbe Logik nutzen. Vorher gab es zwei unabhängige, leicht
unterschiedliche Implementierungen: documents.py baute IMMER "http://{ip}:{port}",
auch wenn ip_address bereits ein vollständiges "https://..." enthielt — dadurch
wurden Anfragen an reine HTTPS-Endpunkte (z.B. openrouter.ai) fälschlich per
Klartext-HTTP auf Port 443 gesendet, was dort zu HTTP 400 führte.
"""


def build_ai_url(ip_address: str, port: str | None) -> str:
    """
    Baut die Basis-URL aus ip_address (IP, Hostname ODER vollständige URL
    inkl. Protokoll) und port zusammen. port ist optional — leer/None bedeutet
    "kein :port in der URL" (z.B. für Cloud-Anbieter, deren Standardport 443
    nicht explizit in der Anfrage stehen soll).

    - Enthält ip_address bereits ein Protokoll (http:// oder https://), wird es
      unverändert übernommen; der Port wird nur angehängt, wenn er gesetzt ist
      UND nicht schon Teil der Adresse ist.
    - Sonst wird http:// vorangestellt (Standardannahme für lokale APIs wie
      LM Studio/Ollama im LAN, die üblicherweise kein TLS verwenden) — der Port
      wird nur angehängt, wenn gesetzt.

    Für reine HTTPS-Endpunkte (z.B. Cloud-Anbieter wie OpenRouter, OpenAI) MUSS
    ip_address explizit mit "https://" beginnen, sonst wird fälschlich Klartext-
    HTTP verwendet.
    """
    port = (port or "").strip() or None

    if ip_address.startswith(("http://", "https://")):
        base = ip_address.rstrip("/")
        if port and ":" not in base.split("//", 1)[1]:
            base = f"{base}:{port}"
        return base

    base = f"http://{ip_address}"
    return f"{base}:{port}" if port else base
