"""
SQLite-backed store for the falsifiable prediction ledger.
Every prediction is logged with a real timestamp and checked later against a
real re-pull of Google Trends data - no LLM involved in either step, so
"we predicted X and it was correct" is never an invented claim.
"""

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

# Absolute, cwd-independent so callers (MCP server, scheduled task, tests) all
# see the same database no matter where the process was launched from.
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ledger.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    baseline_geo TEXT NOT NULL,
    target_geo TEXT NOT NULL,
    baseline_rank INTEGER,
    baseline_search_volume TEXT,
    baseline_velocity_score INTEGER,
    baseline_saturation_level TEXT,
    baseline_lifecycle_stage TEXT,
    evaluation_window_hours REAL NOT NULL,
    evaluate_after TEXT NOT NULL,
    predicted_outcome TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    resolved_at TEXT,
    actual_outcome TEXT
);
CREATE INDEX IF NOT EXISTS idx_predictions_status_evaluate_after
    ON predictions(status, evaluate_after);
"""


def utcnow_iso() -> str:
    # timespec="microseconds" forces a fixed-width string (isoformat() alone
    # drops the fractional part when microsecond==0). All ordering/comparison
    # of these timestamps happens as plain SQLite TEXT, so a variable-width
    # format could sort incorrectly against a fixed-width one at the exact
    # instant a timestamp lands on a whole second.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # sqlite3.connect() creates the .db file but never its parent directory.
    # data/ is generated at runtime and isn't guaranteed to exist yet - a
    # fresh clone, a fresh Docker build, or the ledger.db being intentionally
    # dropped before submission would otherwise crash on the very first call.
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def record_prediction(
    db_path: str,
    topic: str,
    baseline_geo: str,
    target_geo: str,
    baseline_rank: int,
    baseline_search_volume: str,
    baseline_velocity_score: int,
    baseline_saturation_level: str,
    baseline_lifecycle_stage: str,
    evaluation_window_hours: float,
) -> str:
    prediction_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    evaluate_after = created_at.timestamp() + evaluation_window_hours * 3600
    evaluate_after_iso = datetime.fromtimestamp(evaluate_after, tz=timezone.utc).isoformat(timespec="microseconds")

    with closing(get_connection(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO predictions (
                id, created_at, topic, baseline_geo, target_geo, baseline_rank,
                baseline_search_volume, baseline_velocity_score, baseline_saturation_level,
                baseline_lifecycle_stage, evaluation_window_hours, evaluate_after,
                predicted_outcome, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'WILL_APPEAR_IN_TARGET', 'PENDING')
            """,
            (
                prediction_id, created_at.isoformat(timespec="microseconds"), topic, baseline_geo, target_geo,
                baseline_rank, baseline_search_volume, baseline_velocity_score,
                baseline_saturation_level, baseline_lifecycle_stage,
                evaluation_window_hours, evaluate_after_iso,
            ),
        )
        conn.commit()
    return prediction_id


def get_due_predictions(db_path: str = DEFAULT_DB_PATH, now: str | None = None) -> list[dict[str, Any]]:
    now = now or utcnow_iso()
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE status = 'PENDING' AND evaluate_after <= ? ORDER BY evaluate_after ASC",
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]


def resolve_prediction(db_path: str, prediction_id: str, status: str, actual_outcome: str) -> None:
    if status not in ("CORRECT", "INCORRECT"):
        raise ValueError(f"status must be CORRECT or INCORRECT, got {status!r}")
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            "UPDATE predictions SET status = ?, resolved_at = ?, actual_outcome = ? WHERE id = ?",
            (status, utcnow_iso(), actual_outcome, prediction_id),
        )
        conn.commit()


def get_accuracy_stats(db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    with closing(get_connection(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        pending = conn.execute("SELECT COUNT(*) AS n FROM predictions WHERE status = 'PENDING'").fetchone()["n"]
        correct = conn.execute("SELECT COUNT(*) AS n FROM predictions WHERE status = 'CORRECT'").fetchone()["n"]
        incorrect = conn.execute("SELECT COUNT(*) AS n FROM predictions WHERE status = 'INCORRECT'").fetchone()["n"]

    evaluated = correct + incorrect
    accuracy_pct = round((correct / evaluated) * 100, 1) if evaluated > 0 else None

    return {
        "total_predictions": total,
        "pending": pending,
        "evaluated": evaluated,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy_pct": accuracy_pct,
    }


def list_recent_resolved(db_path: str = DEFAULT_DB_PATH, limit: int = 10) -> list[dict[str, Any]]:
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE status != 'PENDING' ORDER BY resolved_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
