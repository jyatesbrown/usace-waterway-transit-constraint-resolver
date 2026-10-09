"""Deterministic extraction of river-mile references from official notice text.

Used only to describe notices that have no published geometry. A parsed mile is never treated as a
spatial match to the supplied route.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MILE = re.compile(
    r"\b(?:R\.?\s*M\.?|RIVER\s+MILE|MILE|MI\.?|RM)\s*(?:MARKER\s*)?"
    r"(\d{1,4}(?:\.\d{1,2})?)(?:\s*(?:-|TO|THROUGH|AND)\s*(?:R\.?\s*M\.?|MILE|RM)?\s*(\d{1,4}(?:\.\d{1,2})?))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MileReference:
    text: str
    mile_from: float
    mile_to: float


def river_mile_references(text: str) -> list[MileReference]:
    out: list[MileReference] = []
    seen: set[str] = set()
    for m in _MILE.finditer(text or ""):
        raw = " ".join(m.group(0).split())
        if raw.upper() in seen:
            continue
        seen.add(raw.upper())
        a = float(m.group(1))
        b = float(m.group(2)) if m.group(2) else a
        out.append(MileReference(raw, min(a, b), max(a, b)))
    return out
