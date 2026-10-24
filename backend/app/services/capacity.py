"""KI-Kapazität in Seiten (gemeinsam für Worker und Workflow-Knoten)."""

# Grobe Schätzung: so viele Kontext-Token braucht eine Seite bei der üblichen Bildauflösung
TOKENS_PER_PAGE_ESTIMATE = 4500


def max_pages(max_context_tokens: int | None) -> int | None:
    """Wie viele Seiten in eine Anfrage passen (None = unbeschränkt)."""
    if not max_context_tokens:
        return None
    return max(1, max_context_tokens // TOKENS_PER_PAGE_ESTIMATE)
