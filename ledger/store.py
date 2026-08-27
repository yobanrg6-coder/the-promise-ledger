"""
Firestore-backed store for the falsifiable prediction ledger.
Every prediction is logged with a real timestamp and checked later against a
real re-pull of Google Trends data - no LLM involved in either step, so
"we predicted X and it was correct" is never an invented claim.

Persistence backend is pluggable (see PredictionBackend below) so the exact
same query logic runs against real Firestore in production and against a
network-free in-memory fake in tests - Cloud Run's per-instance filesystem is
ephemeral, so a local SQLite file (the original implementation) would lose
every prediction whenever an instance restarted or scaled to zero.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

COLLECTION_NAME = "predictions"


class PredictionBackend(Protocol):
    """Minimal storage interface the ledger needs - implemented by both Firestore (production) and an in-memory fake (tests)."""

    def insert(self, doc: dict[str, Any]) -> None: ...
    def query_by_status(self, status: str) -> list[dict[str, Any]]: ...
    def query_pending_for_pair(self, baseline_geo: str, target_geo: str) -> list[dict[str, Any]]: ...
    def update(self, prediction_id: str, fields: dict[str, Any]) -> None: ...
    def count_by_status(self, status: str) -> int: ...
    def total_count(self) -> int: ...
    def most_recent_resolved(self, limit: int) -> list[dict[str, Any]]: ...


class FirestoreBackend:
    """Real backend - one 'predictions' collection in the project's default Firestore database."""

    def __init__(self, project: str | None = None):
        # Local import: keeps Firestore an optional dependency for pure in-memory usage (e.g. offline tests).
        from google.cloud import firestore

        self._client = firestore.Client(project=project) if project else firestore.Client()
        self._collection = self._client.collection(COLLECTION_NAME)

    def insert(self, doc: dict[str, Any]) -> None:
        self._collection.document(doc["id"]).set(doc)

    def query_by_status(self, status: str) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._collection.where("status", "==", status).stream()]

    def query_pending_for_pair(self, baseline_geo: str, target_geo: str) -> list[dict[str, Any]]:
        # Single-field equality filter (status) only - baseline/target_geo are
        # filtered client-side below to avoid requiring a Firestore composite
        # index (data volume here is a handful of predictions per 6h cycle,
        # so this is cheap).
        pending = self.query_by_status("PENDING")
        return [
            p for p in pending
            if p.get("baseline_geo") == baseline_geo and p.get("target_geo") == target_geo
        ]

    def update(self, prediction_id: str, fields: dict[str, Any]) -> None:
        self._collection.document(prediction_id).update(fields)

    def count_by_status(self, status: str) -> int:
        agg = self._collection.where("status", "==", status).count().get()
        return int(agg[0][0].value)

    def total_count(self) -> int:
        agg = self._collection.count().get()
        return int(agg[0][0].value)

    def most_recent_resolved(self, limit: int) -> list[dict[str, Any]]:
        # Two single-field equality queries + in-Python merge/sort, same
        # index-free reasoning as query_pending_for_pair above.
        resolved = self.query_by_status("CORRECT") + self.query_by_status("INCORRECT")
        resolved.sort(key=lambda p: p.get("resolved_at") or "", reverse=True)
        return resolved[:limit]


class InMemoryBackend:
    """Test-only fake: same query semantics as FirestoreBackend, zero network calls."""

    def __init__(self):
        self._docs: dict[str, dict[str, Any]] = {}

    def insert(self, doc: dict[str, Any]) -> None:
        self._docs[doc["id"]] = dict(doc)

    def query_by_status(self, status: str) -> list[dict[str, Any]]:
        return [dict(d) for d in self._docs.values() if d.get("status") == status]

    def query_pending_for_pair(self, baseline_geo: str, target_geo: str) -> list[dict[str, Any]]:
        return [
            dict(d) for d in self._docs.values()
            if d.get("status") == "PENDING" and d.get("baseline_geo") == baseline_geo and d.get("target_geo") == target_geo
        ]

    def update(self, prediction_id: str, fields: dict[str, Any]) -> None:
        self._docs[prediction_id].update(fields)

    def count_by_status(self, status: str) -> int:
        return sum(1 for d in self._docs.values() if d.get("status") == status)

    def total_count(self) -> int:
        return len(self._docs)

    def most_recent_resolved(self, limit: int) -> list[dict[str, Any]]:
        resolved = [d for d in self._docs.values() if d.get("status") != "PENDING"]
        resolved.sort(key=lambda p: p.get("resolved_at") or "", reverse=True)
        return [dict(d) for d in resolved[:limit]]


_default_backend: PredictionBackend | None = None


def _get_backend(backend: PredictionBackend | None) -> PredictionBackend:
    """Every public function accepts an optional backend override (tests pass
    InMemoryBackend()); production code leaves it unset and gets a lazily
    constructed, process-wide Firestore client."""
    if backend is not None:
        return backend
    global _default_backend
    if _default_backend is None:
        # Explicit project, not ambient gcloud config: this ledger only ever
        # belongs to one project, but a developer machine running this
        # locally (see run_graded_check_relay.py) may have a different
        # project active for other work - relying on ambient config caused a
        # real PERMISSION_DENIED against the wrong project (26-ago-2026).
        # Cloud Run's own metadata-server project matches anyway, so this is
        # a no-op there.
        _default_backend = FirestoreBackend(project="topicahead-hackathon")
    return _default_backend


def utcnow_iso() -> str:
    # timespec="microseconds" forces a fixed-width string (isoformat() alone
    # drops the fractional part when microsecond==0). All ordering/comparison
    # of these timestamps happens as plain string comparison, so a
    # variable-width format could sort incorrectly against a fixed-width one
    # at the exact instant a timestamp lands on a whole second.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def record_prediction(
    topic: str,
    baseline_geo: str,
    target_geo: str,
    baseline_rank: int,
    baseline_search_volume: str,
    baseline_velocity_score: int,
    baseline_saturation_level: str,
    baseline_lifecycle_stage: str,
    evaluation_window_hours: float,
    backend: PredictionBackend | None = None,
) -> str:
    store = _get_backend(backend)
    prediction_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    evaluate_after = created_at.timestamp() + evaluation_window_hours * 3600
    evaluate_after_iso = datetime.fromtimestamp(evaluate_after, tz=timezone.utc).isoformat(timespec="microseconds")

    store.insert({
        "id": prediction_id,
        "created_at": created_at.isoformat(timespec="microseconds"),
        "topic": topic,
        "baseline_geo": baseline_geo,
        "target_geo": target_geo,
        "baseline_rank": baseline_rank,
        "baseline_search_volume": baseline_search_volume,
        "baseline_velocity_score": baseline_velocity_score,
        "baseline_saturation_level": baseline_saturation_level,
        "baseline_lifecycle_stage": baseline_lifecycle_stage,
        "evaluation_window_hours": evaluation_window_hours,
        "evaluate_after": evaluate_after_iso,
        "predicted_outcome": "WILL_APPEAR_IN_TARGET",
        "status": "PENDING",
        "resolved_at": None,
        "actual_outcome": None,
    })
    return prediction_id


def record_multi_target_prediction(
    topic: str,
    baseline_geo: str,
    target_geos: list[str],
    baseline_rank: int,
    baseline_search_volume: str,
    baseline_velocity_score: int,
    baseline_saturation_level: str,
    baseline_lifecycle_stage: str,
    evaluation_window_hours: float,
    backend: PredictionBackend | None = None,
) -> str:
    """
    Same falsifiable-prediction shape as record_prediction, but the claim is
    "will appear in AT LEAST ONE of target_geos" instead of one fixed pair -
    see agents/scoring.py::find_multi_target_gaps and
    ledger/predictor.py::make_predictions_for_baseline for why: a single
    country's own top-10 is too high a bar for most genuine cross-market
    signal to clear in 1-24h, so this widens the real, falsifiable surface
    area instead of inventing a softer success criterion.
    """
    store = _get_backend(backend)
    prediction_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    evaluate_after = created_at.timestamp() + evaluation_window_hours * 3600
    evaluate_after_iso = datetime.fromtimestamp(evaluate_after, tz=timezone.utc).isoformat(timespec="microseconds")

    store.insert({
        "id": prediction_id,
        "created_at": created_at.isoformat(timespec="microseconds"),
        "topic": topic,
        "baseline_geo": baseline_geo,
        "target_geo": None,
        "target_geos": list(target_geos),
        "baseline_rank": baseline_rank,
        "baseline_search_volume": baseline_search_volume,
        "baseline_velocity_score": baseline_velocity_score,
        "baseline_saturation_level": baseline_saturation_level,
        "baseline_lifecycle_stage": baseline_lifecycle_stage,
        "evaluation_window_hours": evaluation_window_hours,
        "evaluate_after": evaluate_after_iso,
        "predicted_outcome": "WILL_APPEAR_IN_AT_LEAST_ONE_TARGET",
        "status": "PENDING",
        "resolved_at": None,
        "actual_outcome": None,
    })
    return prediction_id


def get_due_predictions(backend: PredictionBackend | None = None, now: str | None = None) -> list[dict[str, Any]]:
    now = now or utcnow_iso()
    store = _get_backend(backend)
    pending = store.query_by_status("PENDING")
    due = [p for p in pending if p["evaluate_after"] <= now]
    due.sort(key=lambda p: p["evaluate_after"])
    return due


def get_pending_for_pair(baseline_geo: str, target_geo: str, backend: PredictionBackend | None = None) -> list[dict[str, Any]]:
    return _get_backend(backend).query_pending_for_pair(baseline_geo, target_geo)


def get_pending_for_baseline(baseline_geo: str, backend: PredictionBackend | None = None) -> list[dict[str, Any]]:
    """
    Same client-side-filter reasoning as query_pending_for_pair (avoids
    needing a Firestore composite index for a handful of docs per cycle) -
    used by make_predictions_for_baseline's per-horizon dedup, where the
    target isn't a single fixed geo anymore.
    """
    pending = _get_backend(backend).query_by_status("PENDING")
    return [p for p in pending if p.get("baseline_geo") == baseline_geo]


def resolve_prediction(prediction_id: str, status: str, actual_outcome: str, backend: PredictionBackend | None = None) -> None:
    if status not in ("CORRECT", "INCORRECT"):
        raise ValueError(f"status must be CORRECT or INCORRECT, got {status!r}")
    _get_backend(backend).update(prediction_id, {
        "status": status,
        "resolved_at": utcnow_iso(),
        "actual_outcome": actual_outcome,
    })


def mark_needs_graded_check(prediction_id: str, note: str, backend: PredictionBackend | None = None) -> None:
    """
    Intermediate, non-final status: the free exact top-10 check missed in
    every tracked target, but that's a very high bar - see
    fetch_relative_search_ratio's docstring for why a real graded check needs
    pytrends, which Google blocks from Cloud Run's egress (verified live,
    26-ago-2026). This hands the prediction off to a separate local relay
    (ledger/run_graded_check_relay.py, run from a non-datacenter IP) instead
    of resolving it INCORRECT from data we know is incomplete. Deliberately
    does NOT set resolved_at/actual_outcome - it is not evaluated yet, so it
    must not count toward accuracy_pct until the relay finalizes it.
    """
    _get_backend(backend).update(prediction_id, {
        "status": "NEEDS_GRADED_CHECK",
        "graded_check_note": note,
    })


def get_needs_graded_check(backend: PredictionBackend | None = None) -> list[dict[str, Any]]:
    return _get_backend(backend).query_by_status("NEEDS_GRADED_CHECK")


def get_accuracy_stats(backend: PredictionBackend | None = None) -> dict[str, Any]:
    store = _get_backend(backend)
    total = store.total_count()
    pending = store.count_by_status("PENDING")
    needs_graded_check = store.count_by_status("NEEDS_GRADED_CHECK")
    correct = store.count_by_status("CORRECT")
    incorrect = store.count_by_status("INCORRECT")

    evaluated = correct + incorrect
    accuracy_pct = round((correct / evaluated) * 100, 1) if evaluated > 0 else None

    return {
        "total_predictions": total,
        "pending": pending,
        "needs_graded_check": needs_graded_check,
        "evaluated": evaluated,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy_pct": accuracy_pct,
    }


def list_recent_resolved(backend: PredictionBackend | None = None, limit: int = 10) -> list[dict[str, Any]]:
    return _get_backend(backend).most_recent_resolved(limit)
