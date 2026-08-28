"""
One verification cycle for The Promise Ledger.

For every admitted promise whose deadline has passed and that is not yet in a
final state, re-fetch its evidence page and re-decide its status with the
zero-LLM verifier (ledger/verifier.py). No API key, no Gemini call - safe to
run unattended on a schedule.

Usage: python -m ledger.run_cycle
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from agents.promise_schemas import LedgerPromise
from ledger import promises as ledger
from ledger.verifier import verify_promise

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("promise_ledger.cycle")


def run_cycle(check_date: dt.date | None = None, backend=None) -> dict:
    """Verify every due promise once. Returns a small summary dict."""
    today = check_date or dt.datetime.now(dt.timezone.utc).date()
    due = ledger.due_for_check(check_date=today, backend=backend)
    logger.info("Verification cycle starting %s - %d promise(s) due", today.isoformat(), len(due))

    changed: list[dict] = []
    errors = 0
    for row in due:
        try:
            before = row.get("status")
            result = verify_promise(LedgerPromise(**row), check_date=today)
            ledger.apply_verification(row["id"], result, backend=backend)
            if result.status.value != before:
                changed.append({"id": row["id"], "company": row.get("company"),
                                "from": before, "to": result.status.value})
                logger.info("  %s (%s): %s -> %s  [%s]", row["id"][:8], row.get("company"),
                            before, result.status.value, result.reason)
        except Exception:
            errors += 1
            logger.exception("  verification failed for %s", row.get("id"))

    card = ledger.get_scorecard(backend=backend)
    ov = card["overall"]
    logger.info(
        "Cycle complete. checked=%d changed=%d errors=%d | ledger: %d total, %d resolved, on-time rate %s",
        len(due), len(changed), errors, ov["total"], ov["resolved"],
        f'{ov["on_time_rate_pct"]}%' if ov["on_time_rate_pct"] is not None else "n/a",
    )
    return {"checked": len(due), "changed": changed, "errors": errors, "scorecard": card}


if __name__ == "__main__":
    run_cycle()
