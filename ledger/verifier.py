"""
The zero-LLM verifier. Given a promise and the date it's being checked on,
fetch its evidence page and decide a PromiseStatus from keyword presence +
deadline arithmetic + a best-effort ship-date read. No model call anywhere in
this file - "we said X shipped and it did" must never be an inferred claim.

Decision table (deadline D, check date T):
  fetch failed / SPA shell .................... UNVERIFIABLE
  majority of check_keywords present:
      a dated ship signal <= D on the page .... FULFILLED
      a dated ship signal >  D ................ FULFILLED_LATE
      no dateable ship signal ................. FULFILLED       (reason notes date not established)
  some (>=1) but not a majority present:
      T <= D ................................. PENDING
      T >  D ................................. PARTIALLY_FULFILLED
  none present:
      T <= D ................................. PENDING
      D < T <= D + ABANDON_GRACE_DAYS ........ DELAYED
      T >  D + ABANDON_GRACE_DAYS ............ ABANDONED
"""

from __future__ import annotations

import datetime as dt
import re

from agents.promise_schemas import LedgerPromise, PromiseStatus, VerificationResult
from ledger.evidence import fetch_evidence, keyword_hits

ABANDON_GRACE_DAYS = 180

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
_MONTHS = {m.lower(): i for i, m in enumerate(_MONTH_NAMES, start=1)}
# Accept the standard 3-letter abbreviations too ("Oct", "sep", ...).
_MONTHS.update({m[:3].lower(): i for i, m in enumerate(_MONTH_NAMES, start=1)})

# re.IGNORECASE on the month patterns so "oct 28, 2024" / "OCTOBER 28, 2024"
# are read as well as Titlecase; the _MONTHS lookup still gates what counts.
_DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b"),
    re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", re.IGNORECASE),
]


def _majority(n_hits: int, n_total: int) -> bool:
    """A strict majority: more than half of the check_keywords, min 2.

    For an even keyword count this now requires > 50% (3 of 4), not exactly
    half. The min-2 floor keeps a lone repeated/whitelisted token from ever
    resolving a promise on its own.
    """
    return n_total > 0 and n_hits >= max(2, n_total // 2 + 1)


def _dates_near(text: str, anchor: str, radius: int = 400) -> list[dt.date]:
    """Pull candidate dates from the window around the first keyword hit."""
    low = text.lower()
    i = low.find(anchor.lower()) if anchor else -1
    window = text if i < 0 else text[max(0, i - radius): i + radius]
    out: list[dt.date] = []
    for rx in _DATE_PATTERNS:
        for m in rx.finditer(window):
            try:
                if rx in (_DATE_PATTERNS[0], _DATE_PATTERNS[1]):  # YYYY-MM-DD / YYYY/MM/DD
                    y, mo, d = int(m[1]), int(m[2]), int(m[3])
                elif rx is _DATE_PATTERNS[2]:  # Month D, YYYY
                    mo = _MONTHS.get(m[1].lower())
                    d, y = int(m[2]), int(m[3])
                else:  # D Month YYYY
                    d = int(m[1])
                    mo = _MONTHS.get(m[2].lower())
                    y = int(m[3])
                if mo and 1 <= mo <= 12 and 1 <= d <= 31 and 2015 <= y <= 2100:
                    out.append(dt.date(y, mo, d))
            except (ValueError, TypeError):
                continue
    return out


def verify_promise(promise: LedgerPromise, check_date: dt.date | None = None) -> VerificationResult:
    today = check_date or dt.datetime.now(dt.timezone.utc).date()
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    deadline = dt.date.fromisoformat(promise.deadline_date)

    ev = fetch_evidence(promise.evidence_url)
    if not ev.ok or ev.looks_like_spa_shell:
        why = ev.error or "page returned only a JS shell / navigation, no checkable content"
        return VerificationResult(
            status=PromiseStatus.UNVERIFIABLE,
            reason=f"could not verify against {promise.evidence_url or '(no url)'}: {why}",
            evidence_url=ev.url,
            checked_at=now_iso,
        )

    hits = keyword_hits(ev.text, promise.check_keywords)
    n, total = len(hits), len(promise.check_keywords)
    excerpt = ev.excerpt_around(hits[0]) if hits else ev.text[:300]

    if _majority(n, total):
        ship_dates = [d for d in _dates_near(ev.text, hits[0]) if d >= dt.date.fromisoformat(promise.announced_date)]
        earliest = min(ship_dates) if ship_dates else None
        if earliest and earliest <= deadline:
            status, reason = PromiseStatus.FULFILLED, (
                f"{n}/{total} keywords present on {ev.url}; dated {earliest.isoformat()}, on or before deadline {deadline.isoformat()}"
            )
        elif earliest and earliest > deadline:
            status, reason = PromiseStatus.FULFILLED_LATE, (
                f"{n}/{total} keywords present; shipped {earliest.isoformat()}, "
                f"{(earliest - deadline).days}d after deadline {deadline.isoformat()}"
            )
        else:
            status, reason = PromiseStatus.FULFILLED, (
                f"{n}/{total} keywords present on {ev.url}: {hits}; no explicit ship date found on page, "
                "on-time/late not established"
            )
    elif n >= 1:
        if today <= deadline:
            status, reason = PromiseStatus.PENDING, (
                f"only {n}/{total} keywords present and deadline {deadline.isoformat()} not yet reached"
            )
        else:
            status, reason = PromiseStatus.PARTIALLY_FULFILLED, (
                f"deadline {deadline.isoformat()} passed; partial evidence only ({n}/{total} keywords: {hits})"
            )
    else:
        if today <= deadline:
            status, reason = PromiseStatus.PENDING, (
                f"deadline {deadline.isoformat()} not yet reached; no delivery evidence on {ev.url}"
            )
        elif today <= deadline + dt.timedelta(days=ABANDON_GRACE_DAYS):
            status, reason = PromiseStatus.DELAYED, (
                f"deadline {deadline.isoformat()} passed {(today - deadline).days}d ago; "
                f"no delivery evidence on {ev.url} (0/{total} keywords)"
            )
        else:
            status, reason = PromiseStatus.ABANDONED, (
                f"deadline {deadline.isoformat()} passed {(today - deadline).days}d ago (> {ABANDON_GRACE_DAYS}d grace); "
                f"still no delivery evidence on {ev.url}"
            )

    return VerificationResult(
        status=status,
        reason=reason,
        evidence_url=ev.url,
        evidence_excerpt=excerpt,
        keyword_hits=hits,
        checked_at=now_iso,
    )
