"""
The falsifiability gate - the deterministic checkpoint every extracted promise
must pass before it can enter the ledger. Pure Python, no LLM: the extractor
agent PROPOSES that a statement is falsifiable; this code is what actually
decides, so "the ledger only holds checkable promises" is enforced, not
trusted.

A statement is admissible only if it has, verifiably in the extraction:
  - the extractor's own is_falsifiable flag set
  - a normalized deadline that parses as a real calendar date
  - a deadline that is not absurd (not before the announcement, not >5y out)
  - an observable_outcome with real content
  - >=2 usable, specific check_keywords (not one-word filler)
"""

from __future__ import annotations

import datetime as dt
import re

from agents.promise_schemas import GateResult, PromiseExtraction

MAX_DEADLINE_HORIZON_DAYS = 5 * 365
_GENERIC_KEYWORDS = {
    "api", "ai", "app", "beta", "cloud", "feature", "launch", "release",
    "update", "new", "available", "support", "model", "platform", "tool",
}


def _usable_keywords(keywords: list[str]) -> list[str]:
    out = []
    for k in keywords:
        k = k.strip()
        if len(k) < 3:
            continue
        if k.lower() in _GENERIC_KEYWORDS and " " not in k:
            continue  # single generic word matches everything -> useless as a check
        out.append(k)
    return out


def run_gate(extraction: PromiseExtraction, announced_date: str) -> GateResult:
    if not extraction.is_falsifiable:
        return GateResult(
            accepted=False,
            reason=f"extractor rejected: {extraction.rejection_reason or 'not falsifiable'}",
        )

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", extraction.deadline_date_iso or ""):
        return GateResult(accepted=False, reason=f"no normalized deadline (got {extraction.deadline_date_iso!r})")
    try:
        deadline = dt.date.fromisoformat(extraction.deadline_date_iso)
    except ValueError:
        return GateResult(accepted=False, reason=f"unparseable deadline {extraction.deadline_date_iso!r}")

    try:
        announced = dt.date.fromisoformat(announced_date)
    except ValueError:
        return GateResult(accepted=False, reason=f"unparseable announced_date {announced_date!r}")

    if deadline < announced:
        return GateResult(accepted=False, reason=f"deadline {deadline} is before the announcement {announced}")
    if (deadline - announced).days > MAX_DEADLINE_HORIZON_DAYS:
        return GateResult(accepted=False, reason=f"deadline {deadline} is more than 5 years past the announcement")

    if len(extraction.observable_outcome.split()) < 3:
        return GateResult(accepted=False, reason="observable_outcome too thin to check")

    usable = _usable_keywords(extraction.check_keywords)
    if len(usable) < 2:
        return GateResult(
            accepted=False,
            reason=f"need >=2 specific check_keywords, got {usable or 'none usable'}",
        )

    return GateResult(accepted=True, reason=f"accepted ({len(usable)} usable keywords, deadline {deadline})")
