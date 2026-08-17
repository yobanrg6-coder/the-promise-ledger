"""
One full ledger cycle: resolve any predictions whose window has elapsed, then
record fresh predictions for a fixed rotation of market pairs. Meant to be
invoked periodically by a scheduled task (see ledger/README.md) - no API key
required, no Gemini calls, safe to run unattended for days.

Usage: python -m ledger.run_cycle
"""

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import predictor
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("topicahead.ledger")

MARKET_PAIRS = [
    ("US", "MX"),
    ("US", "ES"),
    ("US", "GB"),
    ("MX", "US"),
    ("ES", "MX"),
    ("GB", "US"),
]

def main():
    logger.info("Ledger cycle starting at %s", datetime.now(timezone.utc).isoformat())

    resolution = predictor.resolve_due_predictions()
    logger.info(
        "Resolved %d due predictions (%d correct, %d incorrect, %d skipped - fetch failed)",
        resolution["checked"], resolution["resolved_correct"], resolution["resolved_incorrect"],
        resolution["skipped_fetch_failed"],
    )

    total_new = 0
    for baseline_geo, target_geo in MARKET_PAIRS:
        try:
            new_ids = predictor.make_predictions_for_market_pair(baseline_geo, target_geo)
            logger.info("Recorded %d new predictions for %s -> %s", len(new_ids), baseline_geo, target_geo)
            total_new += len(new_ids)
        except Exception:
            logger.exception("Failed to record predictions for %s -> %s", baseline_geo, target_geo)

    stats = store.get_accuracy_stats()
    logger.info(
        "Cycle complete. New predictions: %d. Ledger totals: %d evaluated, %s%% accuracy, %d pending.",
        total_new, stats["evaluated"],
        stats["accuracy_pct"] if stats["accuracy_pct"] is not None else "n/a",
        stats["pending"],
    )


if __name__ == "__main__":
    main()
