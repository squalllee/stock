"""Web-layer errors and the stable API error envelope."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebError(Exception):
    """An expected API error with a stable code and HTTP status."""

    code: str
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


def error_payload(error: WebError) -> dict[str, dict[str, str]]:
    """Return the documented JSON error envelope."""

    return {"error": {"code": error.code, "message": error.message}}

