"""Helpers shared by the TWSE and TPEx JSON providers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stock_master.exceptions import StockDataValidationError

_WRAPPER_KEYS = ("data", "Data", "result", "results", "records")


def payload_records(payload: object, market: str) -> list[Mapping[str, Any]]:
    """Extract a list of object records from an official JSON payload."""

    candidate = payload
    if isinstance(payload, Mapping):
        for key in _WRAPPER_KEYS:
            if isinstance(payload.get(key), Sequence) and not isinstance(
                payload.get(key), (str, bytes, bytearray)
            ):
                candidate = payload[key]
                break

    if not isinstance(candidate, Sequence) or isinstance(
        candidate, (str, bytes, bytearray)
    ):
        raise StockDataValidationError(
            f"{market} response schema changed: expected a JSON array of records."
        )

    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(candidate):
        if not isinstance(item, Mapping):
            raise StockDataValidationError(
                f"{market} response schema changed: record {index} is not an object."
            )
        records.append(item)
    return records


def ensure_non_empty(records: list[Mapping[str, Any]], market: str) -> None:
    """Reject an empty response before it can be interpreted as a full feed."""

    if not records:
        raise StockDataValidationError(
            f"{market} returned an empty stock list; refusing to sync."
        )

