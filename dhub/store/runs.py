"""SQLite analysis-run persistence and replay."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from .events import RUN_SPAN, close_open_spans, elapsed_ms, encode_json, utc_now, write_event


TERMINAL_STATUSES = {"succeeded", "partial", "failed"}


class RunStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    requirement TEXT NOT NULL,
                    data_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    source_snapshot TEXT,
                    report TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    seq INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def create_run(self, requirement: str, data_mode: str) -> str:
        run_id = uuid4().hex
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO runs (run_id, requirement, data_mode, status, created_at)
                VALUES (?, ?, ?, 'queued', ?)
                """,
                (run_id, requirement, data_mode, utc_now()),
            )
        return run_id

    def start_run(self, run_id: str) -> bool:
        at = utc_now()
        with self._transaction() as db:
            result = db.execute(
                "UPDATE runs SET status = 'running', started_at = ? WHERE run_id = ? AND status = 'queued'",
                (at, run_id),
            )
            if result.rowcount == 0:
                return False
            write_event(db, run_id, {**RUN_SPAN, "type": "run.started", "status": "running"}, at)
        return True

    def set_snapshot(self, run_id: str, snapshot: dict) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE runs SET source_snapshot = ? WHERE run_id = ? AND status = 'running'",
                (encode_json(snapshot), run_id),
            )

    def append_trace(self, run_id: str, event: dict) -> None:
        if event.get("type") not in ("agent.started", "agent.finished", "tool.started", "tool.finished", "plan.updated"):
            raise ValueError("Invalid analysis event type")
        if not isinstance(event.get("span_id"), str) or not event["span_id"] or event["span_id"] == "run":
            raise ValueError("Invalid analysis span id")
        with self._transaction() as db:
            row = db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is not None and row["status"] == "running":
                write_event(db, run_id, event, utc_now())

    def finish_run(
        self,
        run_id: str,
        status: str,
        report: dict | None,
        error: str | None,
        error_code: str | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("Invalid terminal run status")
        with self._transaction() as db:
            row = db.execute("SELECT run_id, status, started_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is not None and row["status"] not in TERMINAL_STATUSES:
                self._finish_run(db, row, status, report, error, error_code)

    def _finish_run(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        status: str,
        report: dict | None,
        error: str | None,
        error_code: str | None,
    ) -> None:
        at = utc_now()
        run_id = row["run_id"]
        close_open_spans(db, run_id, at, error_code or "SPAN_UNCLOSED", error or "The task ended early.")
        db.execute(
            "UPDATE runs SET status = ?, finished_at = ?, report = ?, error = ? WHERE run_id = ?",
            (status, at, encode_json(report) if report is not None else None, error, run_id),
        )
        event = {**RUN_SPAN, "type": "run.finished", "status": status, "elapsed_ms": elapsed_ms(row["started_at"], at)}
        if error is not None:
            event.update(error_code=error_code, error_summary=error)
        write_event(db, run_id, event, at)

    def recover_unfinished(self) -> None:
        with self._transaction() as db:
            rows = db.execute("SELECT run_id, status, started_at FROM runs WHERE status IN ('queued', 'running')").fetchall()
            for row in rows:
                self._finish_run(db, row, "failed", None, "The analysis service stopped before the run finished.", "RUN_INTERRUPTED")

    def get_run(self, run_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = dict(row)
        for field in ("source_snapshot", "report"):
            if run[field] is not None:
                run[field] = json.loads(run[field])
        return run

    def get_events(self, run_id: str, after_seq: int, limit: int = 100) -> list[dict] | None:
        batch = self.get_event_batch(run_id, after_seq, limit)
        return batch[1] if batch is not None else None

    def get_event_batch(self, run_id: str, after_seq: int, limit: int = 100) -> tuple[str, list[dict]] | None:
        with closing(self._connect()) as db:
            run = db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            rows = db.execute(
                "SELECT payload FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after_seq, limit),
            ).fetchall()
        return run["status"], [json.loads(row["payload"]) for row in rows]
