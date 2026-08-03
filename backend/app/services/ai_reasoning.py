"""
Gemeinsame Bildung des "reasoning"-Request-Parameters für OpenAI-kompatible
Cloud-Provider (z.B. OpenRouter, OpenAI).

Wird von app/services/ai_service.py (Produktions-Pipeline) UND
app/services/ai_test_service.py (KI-Test-Tool) verwendet — beide MÜSSEN
dieselbe Logik nutzen, sonst zeigt das Test-Tool ein anderes Verhalten als die
echte Analyse (siehe Bugfix: zwei unabhängige URL-Implementierungen führten
vorher zum selben Problem, siehe app/services/ai_url.py).

Vorherige Version sandte "reasoning": "off"/"low"/"medium"/"high" als reinen
STRING im Request-Body — das ist kein Teil des offiziellen OpenAI-Schemas.
Manche lokalen Server (LM Studio über den eigenen /api/v1/chat-Endpunkt)
akzeptieren das, strikt validierende Cloud-Provider wie OpenRouter lehnen es
mit HTTP 400 ab: "reasoning: Invalid input: expected object, received string".

Betrifft NUR den OpenAI-kompatiblen Pfad (POST /v1/chat/completions).
LM Studios eigener /api/v1/chat-Endpunkt (LMStudioClient) hat ein anderes,
eigenes Payload-Format und ist von diesem Bug nicht betroffen.
"""


def build_reasoning_param(reasoning: str | None) -> dict | None:
    """
    Baut den 'reasoning'-Parameter im OpenRouter/OpenAI-Objekt-Format
    ({"effort": "low"|"medium"|"high"}) oder gibt None zurück, wenn Reasoning
    aus ist. Bei None wird das Feld im Request-Body komplett weggelassen —
    die sicherste Variante, da nicht jeder Provider ein unbekanntes Feld
    stillschweigend ignoriert.
    """
    level = "high" if reasoning == "on" else (reasoning or "off")
    if level == "off":
        return None
    return {"effort": level}
