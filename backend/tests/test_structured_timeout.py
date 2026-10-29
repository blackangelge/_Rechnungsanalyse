"""Der Schema-Pfad (structured_ai) staffelt das KI-Zeitlimit nach Seitenzahl wie der alte Pfad."""

import httpx
import pytest

from app.services import structured_ai


class _Cfg:
    api_url = "http://ki.test"
    api_key = None
    model_name = "m"
    max_tokens = None
    temperature = None
    reasoning = "off"


@pytest.mark.parametrize("pages,expected", [(1, 900), (14, 900), (15, 1200), (20, 1800), (30, 2700), (52, 5400)])
def test_timeout_scales_with_page_count(monkeypatch, pages, expected):
    seen = {}

    class FakeClient:
        def __init__(self, timeout):
            seen["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise httpx.ReadTimeout("zu langsam")

    monkeypatch.setattr(structured_ai.httpx, "Client", FakeClient)
    res = structured_ai.call_structured(_Cfg(), "sys", "text", ["data:image/png;base64,AA"] * pages, {"type": "json_schema"})
    assert seen["timeout"] == expected
    assert f"KI-Timeout nach {expected}s" in (res.error or "")
