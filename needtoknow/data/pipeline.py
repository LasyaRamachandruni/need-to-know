"""Turn a raw change-event stream into current state with provenance.

    validate -> dedupe -> order by server time -> apply field by field -> facts

Every current value is stored as a *fact*: the value, plus where it came from
(source device), when the server received it, how many times it has been
written, and whether two devices wrote different values to it at nearly the
same time (a conflict, with both candidates kept). Downstream, the trust scorer
and context service work from these facts, so every value an agent sees can be
traced to the event that produced it.

Ordering uses server receive time, never the device's clock: device clocks can be
hours off. Skew is measured per device and reported, not trusted.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from ..catalog import ENTITIES

CONFLICT_WINDOW = timedelta(minutes=10)
SKEW_LIMIT = timedelta(minutes=5)

NUMBER = {"quota_gb", "used_gb", "reported_free_gb", "size_mb", "lat", "lon", "steps", "resting_hr", "sleep_hours"}
BOOL = {"backup_enabled", "backed_up", "favorite", "is_screenshot"}
LIST = {"people", "attendees", "consents"}
REQUIRED = ("event_id", "user_id", "entity", "entity_id", "op", "fields", "source", "client_ts", "server_ts")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def validate(e: dict) -> str | None:
    """Reason the event is invalid, or None."""
    missing = [k for k in REQUIRED if e.get(k) in (None, "") and k != "fields"]
    if missing:
        return f"missing {', '.join(missing)}"
    if e["entity"] not in ENTITIES:
        return f"unknown entity {e['entity']!r}"
    if e["op"] not in ("upsert", "delete"):
        return f"unknown op {e['op']!r}"
    if not isinstance(e.get("fields"), dict):
        return "fields is not an object"
    try:
        _ts(e["server_ts"]), _ts(e["client_ts"])
    except (TypeError, ValueError):
        return "bad timestamp"
    known = ENTITIES[e["entity"]]
    for k, v in e["fields"].items():
        if k not in known:
            return f"unknown field {e['entity']}.{k}"
        if v is None:
            continue
        if k in NUMBER and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"{e['entity']}.{k} should be a number, got {v!r}"
        if k in BOOL and not isinstance(v, bool):
            return f"{e['entity']}.{k} should be true/false, got {v!r}"
        if k in LIST and not isinstance(v, list):
            return f"{e['entity']}.{k} should be a list"
    return None


@dataclass
class PipelineStats:
    events: int
    duplicates: int
    quarantined: int
    applied: int
    entities: int
    facts: int
    conflicts: int
    skewed_sources: int


@dataclass
class _Write:
    value: object
    source: str
    server_ts: datetime


def reconcile(events: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, PipelineStats]:
    """Returns (facts, quarantine, source_health, stats)."""
    seen: set[str] = set()
    quarantine, valid = [], []
    dupes = 0
    for e in events:
        reason = validate(e)
        if reason:
            quarantine.append({"event_id": e.get("event_id"), "user_id": e.get("user_id"), "reason": reason,
                               "raw": json.dumps(e, default=str)})
            continue
        if e["event_id"] in seen:  # at-least-once delivery: same event again
            dupes += 1
            continue
        seen.add(e["event_id"])
        valid.append(e)

    valid.sort(key=lambda e: (e["server_ts"], e["event_id"]))  # server order, not arrival order

    history: dict[tuple, dict[str, list[_Write]]] = {}
    skews: dict[tuple, list[float]] = defaultdict(list)
    last_seen: dict[tuple, datetime] = {}
    counts: dict[tuple, int] = defaultdict(int)
    for e in valid:
        key = (e["user_id"], e["entity"], e["entity_id"])
        st = _ts(e["server_ts"])
        sk = (e["user_id"], e["source"])
        skews[sk].append((_ts(e["client_ts"]) - st).total_seconds())
        last_seen[sk] = max(last_seen.get(sk, st), st)
        counts[sk] += 1
        if e["op"] == "delete":
            history.pop(key, None)
            continue
        fields = history.setdefault(key, {})
        for f, v in e["fields"].items():
            fields.setdefault(f, []).append(_Write(v, e["source"], st))

    rows = []
    for (user, entity, eid), fields in history.items():
        for f, writes in fields.items():
            last = writes[-1]
            conflict, candidates = False, None
            if len(writes) >= 2:
                prev = writes[-2]
                if prev.source != last.source and prev.value != last.value and last.server_ts - prev.server_ts <= CONFLICT_WINDOW:
                    conflict, candidates = True, [prev.value, last.value]
            rows.append({"user_id": user, "entity": entity, "entity_id": eid, "field": f,
                         "value": json.dumps(last.value), "source": last.source, "updated_at": last.server_ts,
                         "versions": len(writes), "conflict": conflict,
                         "candidates": json.dumps(candidates) if candidates else None})
    facts = pd.DataFrame(rows)
    health = pd.DataFrame([
        {"user_id": u, "source": s, "events": counts[(u, s)], "last_event_at": last_seen[(u, s)],
         "clock_skew_s": round(statistics.median(v), 1),
         "clock_skewed": abs(statistics.median(v)) > SKEW_LIMIT.total_seconds()}
        for (u, s), v in skews.items()
    ])
    q = pd.DataFrame(quarantine, columns=["event_id", "user_id", "reason", "raw"])
    stats = PipelineStats(len(events), dupes, len(q), len(valid), len(history), len(facts),
                          int(facts.conflict.sum()) if len(facts) else 0,
                          int(health.clock_skewed.sum()) if len(health) else 0)
    return facts, q, health, stats


def build(events: list[dict], warehouse: str | Path) -> PipelineStats:
    """Reconcile events and write facts, quarantine and source health to DuckDB (replacing previous tables)."""
    facts, q, health, stats = reconcile(events)
    Path(warehouse).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse))
    for name, df in (("facts", facts), ("quarantine", q), ("source_health", health)):
        con.register("_df", df)
        con.execute(f"create or replace table {name} as select * from _df")
        con.unregister("_df")
    con.execute("create or replace table pipeline_stats as select * from (values (?)) t(stats)", [json.dumps(asdict(stats))])
    con.close()
    return stats


def quality_report(warehouse: str | Path) -> dict:
    """Data-quality summary an on-call engineer would look at first."""
    con = duckdb.connect(str(warehouse), read_only=True)
    q = lambda sql: con.sql(sql).fetchall()  # noqa: E731
    out = {
        "stats": json.loads(q("select stats from pipeline_stats")[0][0]),
        "quarantine_reasons": dict(q("select regexp_replace(reason, '''.*''|[0-9.]+', '…'), count(*) "
                                     "from quarantine group by 1 order by 2 desc")),
        "conflicts_by_field": {f"{e}.{f}": n for e, f, n in
                               q("select entity, field, count(*) from facts where conflict group by 1, 2 order by 3 desc")},
        "skewed_sources": [dict(zip(("user_id", "source", "clock_skew_s"), r)) for r in
                           q("select user_id, source, clock_skew_s from source_health where clock_skewed order by abs(clock_skew_s) desc")],
        "stale_devices": q("""
            select count(*) from source_health
            where source <> 'web' and last_event_at < (select max(last_event_at) from source_health) - interval 14 day
        """)[0][0],
        "facts_by_entity": dict(q("select entity, count(*) from facts group by 1 order by 1")),
    }
    con.close()
    return out
