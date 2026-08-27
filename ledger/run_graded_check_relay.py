"""
Second-pass resolver for predictions Cloud Run's ledger cycle could only
mark NEEDS_GRADED_CHECK (absent from every tracked target's own top-10, but
that's too high a bar to call INCORRECT outright - see
predictor.resolve_due_predictions).

Must run from a normal, non-datacenter IP: Google reliably rate-limits/blocks
pytrends calls from Cloud Run's own egress (verified live, 26-ago-2026 - a
real 45s cooldown retry still got HTTP 429), so this step cannot run there.
Meant to be triggered periodically from this machine (Windows Task
Scheduler, hourly) while Cloud Run's ledger-cycle job keeps doing the free
exact-match half every 6h - same Firestore, same source of truth, just two
different machines doing the two halves that need different network
reputations.

Usage: python -m ledger.run_graded_check_relay
"""

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import predictor
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("promise_ledger.ledger.relay")


def main():
    logger.info("Graded-check relay starting at %s", datetime.now(timezone.utc).isoformat())

    resolution = predictor.resolve_needs_graded_check()
    logger.info(
        "Resolved %d graded-check predictions (%d correct, %d incorrect, %d still unavailable)",
        resolution["checked"], resolution["resolved_correct"], resolution["resolved_incorrect"],
        resolution["still_unavailable"],
    )

    stats = store.get_accuracy_stats()
    logger.info(
        "Relay complete. Ledger totals: %d evaluated, %s%% accuracy, %d pending, %d awaiting graded check.",
        stats["evaluated"],
        stats["accuracy_pct"] if stats["accuracy_pct"] is not None else "n/a",
        stats["pending"], stats["needs_graded_check"],
    )


if __name__ == "__main__":
    main()
