"""Write and close trace spans within the caller's SQLite transaction."""

import json
import sqlite3
from datetime import datetime, timezone


TRACE_FIELDS = {
    "type",
    "span_id",
    "parent_span_id",
    "agent_id",
    "status",
    "elapsed_ms",
    "stage",
    "tool_name",
    "input_summary",
    "output_summary",
    "error_code",
    "error_summary",
}
RUN_SPAN = {"span_id": "run", "parent_span_id": None, "agent_id": None}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def elapsed_ms(started_at: str | None, ended_at: str) -> int:
    if started_at is None:
        return 0
    elapsed = datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
    return max(0, round(elapsed.total_seconds() * 1000))


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def write_event(db: sqlite3.Connection, run_id: str, event: dict, at: str) -> None:
    seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)).fetchone()[0]
    payload = {key: value for key, value in event.items() if key in TRACE_FIELDS}
    if event["type"] == "plan.updated":
        payload["plan"] = event["plan"]
    payload.update(run_id=run_id, seq=seq, at=at)
    db.execute("INSERT INTO events (run_id, seq, payload) VALUES (?, ?, ?)", (run_id, seq, encode_json(payload)))


def close_open_spans(db: sqlite3.Connection, run_id: str, at: str, code: str, message: str) -> None:
    records = db.execute("SELECT payload FROM events WHERE run_id = ? ORDER BY seq DESC", (run_id,)).fetchall()
    finished = set()
    for record in records:
        event = json.loads(record["payload"])
        kind, phase = event["type"].split(".")
        if kind not in {"agent", "tool"}:
            continue
        key = (kind, event["span_id"])
        if phase == "finished":
            finished.add(key)
        elif key not in finished:
            write_event(
                db,
                run_id,
                {
                    "type": f"{kind}.finished",
                    "span_id": event["span_id"],
                    "parent_span_id": event.get("parent_span_id"),
                    "agent_id": event.get("agent_id"),
                    **{name: event[name] for name in ("stage", "tool_name") if name in event},
                    "status": "failed",
                    "elapsed_ms": elapsed_ms(event["at"], at),
                    "error_code": code,
                    "error_summary": message,
                },
                at,
            )
            finished.add(key)
