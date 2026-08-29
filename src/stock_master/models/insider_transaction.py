"""Normalized company-insider share-transfer disclosures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """One official insider share-transfer disclosure.

    The free TWSE/TPEx OpenAPI feeds are *pre-declarations*: they describe a
    planned transfer and a later non-transfer notice, not proof that a trade
    was executed.  MOPS monthly balance rows use ``after_report`` and retain
    both the previous and month-end holdings so the distinction remains
    explicit in the shared table.
    """

    report_date: str
    stock_code: str
    market: str
    report_type: str
    transaction_type: str
    insider_name: str
    insider_role: str
    shares_changed: int
    source: str
    source_record_key: str
    transfer_method: str | None = None
    transferee: str | None = None
    current_shares: int | None = None
    planned_shares: int | None = None
    after_shares: int | None = None
    effective_period: str | None = None
    reason: str | None = None
    raw_data: Mapping[str, Any] = field(default_factory=dict)
