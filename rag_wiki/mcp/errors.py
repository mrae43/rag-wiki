from __future__ import annotations

import httpx


def backend_error_message(exc: httpx.HTTPError, api_url: str) -> str:
    if isinstance(exc, httpx.ConnectError):
        return f"Could not connect to backend at {api_url}"
    if isinstance(exc, httpx.TimeoutException):
        return f"Request to backend at {api_url} timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Backend returned {exc.response.status_code} for {api_url}"
    return f"Request to backend at {api_url} failed: {exc}"
