"""
Firestore-backed store for The Promise Ledger.

Every promise is persisted with its verbatim source quote and source URL the
moment it's admitted, and its status is only ever changed by the zero-LLM
verifier (ledger/verifier.py). Same pluggable-backend design as the original
trend ledger: real Firestore in production, a network-free in-memory fake in
tests - Cloud Run's per-instance disk is ephemeral, so a local file would
lose the ledger on every scale-to-zero.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Protocol

from agents.promise_schemas import (
    KEPT_ON_TIME_STATUSES,
    RESOLVED_STATUSES,
    LedgerPromise,
    PromiseStatus,
)

COLLECTION_NAME = "promises"
GCP_PROJECT = "topicahead-hackathon"  # immutable GCP project id (pre-rebrand); display name is "The Promise Ledger"


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


class PromiseBackend(Protocol):
    def insert(self, doc: dict[str, Any]) -> None: ...
    def get(self, promise_id: str) -> dict[str, Any] | None: ...
    def update(self, promise_id: str, fields: dict[str, Any]) -> None: ...
    def all(self) -> list[dict[str, Any]]: ...
    def by_status(self, status: str) -> list[dict[str, Any]]: ...


class FirestoreBackend:
    def __init__(self, project: str | None = None):
        from google.cloud import firestore

        self._client = firestore.Client(project=project or GCP_PROJECT)
        self._col = self._client.collection(COLLECTION_NAME)

    def insert(self, doc: dict[str, Any]) -> None:
        self._col.document(doc["id"]).set(doc)

    def get(self, promise_id: str) -> dict[str, Any] | None:
        snap = self._col.document(promise_id).get()
        return snap.to_dict() if snap.exists else None

    def update(self, promise_id: str, fields: dict[str, Any]) -> None:
        self._col.document(promise_id).update(fields)

    def all(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._col.stream()]

    def by_status(self, status: str) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._col.where("status", "==", status).stream()]


class InMemoryBackend:
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}

    def insert(self, doc: dict[str, Any]) -> None:
        self._docs[doc["id"]] = dict(doc)

    def get(self, promise_id: str) -> dict[str, Any] | None:
        d = self._docs.get(promise_id)
        return dict(d) if d else None

    def update(self, promise_id: str, fields: dict[str, Any]) -> None:
        self._docs[promise_id].update(fields)

    def all(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self._docs.values()]

    def by_status(self, status: str) -> list[dict[str, Any]]:
        return [dict(d) for d in self._docs.values() if d.get("status") == status]


_default_backend: PromiseBackend | None = None


def _backend(backend: PromiseBackend | None) -> PromiseBackend:
    if backend is not None:
        return backend
    global _default_backend
    if _default_backend is None:
        _default_backend = FirestoreBackend(project=GCP_PROJECT)
    return _default_backend


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def admit_promise(
    *,
    company: str,
    promise_text: str,
    source_quote: str,
    source_url: str,
    announced_date: str,
    deadline_raw: str,
    deadline_date: str,
    observable_outcome: str,
    check_keywords: list[str],
    evidence_url: str = "",
    extractor_model: str = "",
    auditor_agreed: bool | None = None,
    backend: PromiseBackend | None = None,
) -> str:
    promise = LedgerPromise(
        id=str(uuid.uuid4()),
        company=company,
        promise_text=promise_text,
        source_quote=source_quote,
        source_url=source_url,
        announced_date=announced_date,
        deadline_raw=deadline_raw,
        deadline_date=deadline_date,
        observable_outcome=observable_outcome,
        check_keywords=check_keywords,
        evidence_url=evidence_url,
        status=PromiseStatus.PENDING,
        created_at=utcnow_iso(),
        extractor_model=extractor_model,
        auditor_agreed=auditor_agreed,
    )
    _backend(backend).insert(promise.model_dump(mode="json"))
    return promise.id


def apply_verification(promise_id: str, result, backend: PromiseBackend | None = None) -> None:
    """Persist a VerificationResult onto a promise."""
    fields: dict[str, Any] = {
        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
        "status_reason": result.reason,
        "evidence_url": result.evidence_url or "",
        "evidence_excerpt": result.evidence_excerpt or "",
        "last_checked_at": result.checked_at or utcnow_iso(),
    }
    if fields["status"] in {s.value for s in RESOLVED_STATUSES}:
        fields["resolved_at"] = utcnow_iso()
    _backend(backend).update(promise_id, fields)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def get_promise(promise_id: str, backend: PromiseBackend | None = None) -> dict[str, Any] | None:
    return _backend(backend).get(promise_id)


def list_promises(backend: PromiseBackend | None = None) -> list[dict[str, Any]]:
    return _backend(backend).all()


def due_for_check(check_date: dt.date | None = None, backend: PromiseBackend | None = None) -> list[dict[str, Any]]:
    """PENDING or still-tracking promises whose deadline has passed."""
    today = check_date or dt.datetime.now(dt.timezone.utc).date()
    out = []
    for d in _backend(backend).all():
        status = d.get("status")
        if status in {PromiseStatus.PENDING.value, PromiseStatus.DELAYED.value, PromiseStatus.UNVERIFIABLE.value}:
            try:
                if dt.date.fromisoformat(d["deadline_date"]) <= today:
                    out.append(d)
            except (KeyError, ValueError):
                continue
    return out


# --------------------------------------------------------------------------- #
# Scorecard
# --------------------------------------------------------------------------- #
def get_scorecard(backend: PromiseBackend | None = None) -> dict[str, Any]:
    docs = _backend(backend).all()
    resolved_vals = {s.value for s in RESOLVED_STATUSES}
    kept_on_time = {s.value for s in KEPT_ON_TIME_STATUSES}
    kept_any = kept_on_time | {PromiseStatus.FULFILLED_LATE.value, PromiseStatus.PARTIALLY_FULFILLED.value}

    by_company: dict[str, dict[str, int]] = {}
    overall = {"total": len(docs), "resolved": 0, "kept_on_time": 0, "kept_late_or_partial": 0,
               "delayed": 0, "abandoned": 0, "pending": 0, "unverifiable": 0}

    for d in docs:
        s = d.get("status", PromiseStatus.PENDING.value)
        c = d.get("company") or "(unknown)"
        cc = by_company.setdefault(c, {"resolved": 0, "kept_on_time": 0, "kept_late_or_partial": 0,
                                       "delayed": 0, "abandoned": 0, "pending": 0, "unverifiable": 0})
        if s in resolved_vals:
            overall["resolved"] += 1
            cc["resolved"] += 1
        if s in kept_on_time:
            overall["kept_on_time"] += 1
            cc["kept_on_time"] += 1
        elif s in kept_any:
            overall["kept_late_or_partial"] += 1
            cc["kept_late_or_partial"] += 1
        elif s == PromiseStatus.DELAYED.value:
            overall["delayed"] += 1
            cc["delayed"] += 1
        elif s == PromiseStatus.ABANDONED.value:
            overall["abandoned"] += 1
            cc["abandoned"] += 1
        elif s == PromiseStatus.PENDING.value:
            overall["pending"] += 1
            cc["pending"] += 1
        elif s == PromiseStatus.UNVERIFIABLE.value:
            overall["unverifiable"] += 1
            cc["unverifiable"] += 1

    def _rate(kept: int, resolved: int) -> float | None:
        return round(100 * kept / resolved, 1) if resolved else None

    overall["on_time_rate_pct"] = _rate(overall["kept_on_time"], overall["resolved"])
    companies = []
    for c, cc in sorted(by_company.items()):
        cc = dict(cc)
        cc["company"] = c
        cc["on_time_rate_pct"] = _rate(cc["kept_on_time"], cc["resolved"])
        companies.append(cc)

    return {"overall": overall, "companies": companies}
