from __future__ import annotations

from enum import StrEnum


class SourceState(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_FORMAT = "invalid_format"
    LISTED_BUT_NOT_PUBLISHED = "listed_but_not_published"
    NOT_FOUND = "not_found"
    NOT_QUERIED = "not_queried"
    RATE_LIMITED = "rate_limited"


class SourceError(Exception):
    """Controlled failure of one official dataset file. `detail` is developer-facing."""

    def __init__(self, state: SourceState, detail: str) -> None:
        super().__init__(f"{state}: {detail}")
        self.state = state
        self.detail = detail
