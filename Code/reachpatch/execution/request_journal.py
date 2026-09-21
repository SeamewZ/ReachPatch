"""Crash-safe, content-free accounting of every admitted provider request."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


def append_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    record = {"event_id": uuid4().hex,
              "timestamp": datetime.now(timezone.utc).isoformat(), **event}
    path.parent.mkdir(parents=True, exist_ok=True)
    # One journal per process/attempt. O_APPEND plus fsync preserves the
    # admission even if the process dies before its ordinary report is saved.
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record


def read_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0  # No journal: caller must report accounting unavailable.
    lines = path.read_bytes().splitlines(keepends=True)
    events = []
    truncated = 0
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeError):
            if index != len(lines) - 1 or line.endswith(b"\n"):
                raise ValueError("corrupt request journal, not an interrupted tail")
            truncated += 1
    return events, truncated


def reconcile_requests(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    requests: dict[str, dict[str, Any]] = {}
    for event in events:
        key = event["event_id"]
        if key in unique and unique[key] != event:
            raise ValueError("conflicting journal event ID")
        if key in unique:
            continue
        unique[key] = event
        request_id = event.get("request_id")
        if not request_id:
            continue
        item = requests.setdefault(request_id, {})
        kind = event["kind"]
        if kind in item:
            raise ValueError("duplicate request lifecycle transition")
        item[kind] = event
    rows = []
    for request_id, lifecycle in requests.items():
        admitted = lifecycle.get("MODEL_ADMITTED")
        if admitted is None:
            raise ValueError("request terminal record without durable admission")
        if "MODEL" in lifecycle and "MODEL_ERROR" in lifecycle:
            raise ValueError("request has conflicting terminal records")
        terminal = lifecycle.get("MODEL", lifecycle.get("MODEL_ERROR", {}))
        usage = terminal.get("usage", {})
        total = usage.get("total_tokens")
        known = isinstance(total, (int, float)) and not isinstance(total, bool) and total >= 0
        rows.append({"request_id": request_id, "stage": admitted["stage"],
                     "status": "RETURNED" if "MODEL" in lifecycle else
                               "ERROR" if "MODEL_ERROR" in lifecycle else "UNKNOWN_INTERRUPTED",
                     "provider_tokens": int(total) if known else None,
                     "reserved_tokens": admitted["reserved_tokens"],
                     "provider_model": terminal.get("provider_model"),
                     "seconds": terminal.get("seconds")})
    unknown = sum(row["provider_tokens"] is None for row in rows)
    return {"requests": rows, "model_calls": len(rows),
            "reported_tokens_lower_bound": sum(row["provider_tokens"] or 0 for row in rows),
            "unknown_usage_requests": unknown,
            "usage_complete": unknown == 0,
            "reservation_tokens": sum(row["reserved_tokens"] for row in rows)}
