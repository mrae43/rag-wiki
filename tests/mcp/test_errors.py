from __future__ import annotations

import httpx
import pytest

from rag_wiki.mcp.errors import backend_error_message


def _http_status_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://localhost:8000/api/query")
    response = httpx.Response(503, request=request)
    return httpx.HTTPStatusError("Server error", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("Connection refused"), "Could not connect"),
        (httpx.TimeoutException("Timed out"), "timed out"),
        pytest.param(_http_status_error(), "503", id="http_status_error"),
        (httpx.HTTPError("Something went wrong"), "failed"),
    ],
)
def test_backend_error_message(exc: httpx.HTTPError, expected: str) -> None:
    message = backend_error_message(exc, "http://localhost:8000")
    assert expected in message
    assert "http://localhost:8000" in message
