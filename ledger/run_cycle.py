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

# Every tracked market predicts crossing into ALL the others at once (see
# agents/scoring.py::find_multi_target_gaps) instead of a fixed list of
# asymmetric pairs - simpler, and a real prediction now has 3 chances to land
# instead of 1. Replaces the old 6-pair MARKET_PAIRS list (26-ago-2026).
GEOS = ["US", "MX", "ES", "GB"]

def main():
    logger.info("Ledger cycle starting at %s", datetime.now(timezone.utc).isoformat())

    resolution = predictor.resolve_due_predictions()
    logger.info(
        "Resolved %d due predictions (%d correct, %d sent to graded check, %d skipped - fetch failed)",
        resolution["checked"], resolution["resolved_correct"], resolution["sent_to_graded_check"],
        resolution["skipped_fetch_failed"],
    )

    total_new = 0
    for baseline_geo in GEOS:
        target_geos = [g for g in GEOS if g != baseline_geo]
        try:
            new_ids = predictor.make_predictions_for_baseline(baseline_geo, target_geos)
            logger.info("Recorded %d new predictions for %s -> %s", len(new_ids), baseline_geo, target_geos)
            total_new += len(new_ids)
        except Exception:
            logger.exception("Failed to record predictions for baseline %s", baseline_geo)

    stats = store.get_accuracy_stats()
    logger.info(
        "Cycle complete. New predictions: %d. Ledger totals: %d evaluated, %s%% accuracy, %d pending.",
        total_new, stats["evaluated"],
        stats["accuracy_pct"] if stats["accuracy_pct"] is not None else "n/a",
        stats["pending"],
    )


if __name__ == "__main__":
    main()
