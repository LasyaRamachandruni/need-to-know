"""The context service: what an agent is allowed to know for one task.

    bundle = ContextService(warehouse).get(user_id, "photo_sharing",
                                          scope={"album": "Kyoto Mar 2026", "recipient": "mom"},
                                          agent_id="assistant")

A request names a purpose and a scope. The service returns a bundle in which
every value carries:
- its provenance: source device and server time;
- a trust assessment, saying whether the agent can act on it;
- a mark if it is free text written by a person, which is untrusted.

The bundle also counts what was withheld and why. Every request is written to the
audit log: who asked, for what purpose, and how many fields were released or withheld.

`mode="raw"` is the baseline the evaluation compares against: the same agent
reading the user's data directly, with no purpose, scope, masking or trust.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import policy as P
from .catalog import ENTITIES
from .data.synth import NOW
from .trust import Trust, assess


@dataclass
class FactView:
    value: object
    source: str | None = None
    updated_at: str | None = None
    trust: float | None = None
    actionable: bool | None = None
    reasons: list[str] = field(default_factory=list)
    untrusted_text: bool = False
    masked: bool = False


@dataclass
class Item:
    entity: str
    id: str
    fields: dict[str, FactView]


@dataclass
class ContextBundle:
    request_id: str
    user_id: str
    purpose: str | None
    scope: dict
    mode: str
    items: list[Item]
    withheld: dict[str, int]
    consents: list[str]
    elapsed_ms: float = 0.0

    def by_entity(self, entity: str) -> list[Item]:
        return [i for i in self.items if i.entity == entity]

    def find(self, entity: str, eid: str) -> Item | None:
        return next((i for i in self.items if i.entity == entity and i.id == eid), None)

    def value(self, entity: str, eid: str, name: str, default=None):
        it = self.find(entity, eid)
        return it.fields[name].value if it and name in it.fields else default

    def field_count(self) -> int:
        return sum(len(i.fields) for i in self.items)

    def released(self) -> set[tuple[str, str, str]]:
        return {(i.entity, i.id, f) for i in self.items for f in i.fields}

    def to_json(self) -> dict:
        return asdict(self)


def _parse(ts) -> datetime:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _match_person(rec: dict, who: str) -> bool:
    who = who.strip().lower()
    return rec.get("relation", "").lower() == who or rec.get("name", "").lower() == who


class ContextService:
    def __init__(self, warehouse: str | Path, now: datetime = NOW, audit: list | None = None):
        self.con = duckdb.connect(str(warehouse), read_only=True)
        self.now = now
        self.audit: list[dict] = audit if audit is not None else []

    # -- loading -------------------------------------------------------------
    def _load(self, user_id: str):
        rows = self.con.execute(
            # timestamps come back as epoch seconds: no timezone library needed on the client
            "select entity, entity_id, field, value, source, epoch(updated_at), conflict, candidates "
            "from facts where user_id = ?", [user_id]).fetchall()
        recs: dict[str, dict[str, dict]] = {}
        meta: dict[tuple, tuple] = {}
        for ent, eid, f, v, src, upd, conflict, cands in rows:
            recs.setdefault(ent, {}).setdefault(eid, {})[f] = json.loads(v)
            meta[(ent, eid, f)] = (src, _parse(upd), bool(conflict), json.loads(cands) if cands else None)
        seen = dict(self.con.execute("select source, epoch(last_event_at) from source_health where user_id = ?",
                                     [user_id]).fetchall())
        return recs, meta, {k: _parse(v) for k, v in seen.items()}

    # -- scoping -------------------------------------------------------------
    @staticmethod
    def _in_scope(purpose: str, entity: str, rec: dict, scope: dict) -> bool:
        if purpose == "photo_sharing" and entity == "photo":
            if "album" in scope:
                return rec.get("album") == scope["album"]
            if "start" in scope:
                return scope["start"] <= rec.get("taken_at", "") < scope["end"]
            return False
        if purpose in ("photo_sharing", "scheduling") and entity == "contact":
            who = scope.get("recipient") or scope.get("with")
            return bool(who) and _match_person(rec, who)
        if purpose == "scheduling" and entity == "event":
            return "start" in scope and rec.get("end", "") > scope["start"] and rec.get("start", "") < scope["end"]
        if purpose == "backup_support" and entity == "device" and scope.get("device"):
            d = scope["device"].lower()
            return d in rec.get("name", "").lower() or d in rec.get("model", "").lower()
        return True

    # -- the request -----------------------------------------------------------
    def get(self, user_id: str, purpose: str | None, scope: dict | None = None, *, mode: str = "governed",
            agent_id: str = "agent") -> ContextBundle:
        t0 = time.perf_counter()
        scope = scope or {}
        recs, meta, last_seen = self._load(user_id)
        consents = set((recs.get("account", {}).get("acct", {}) or {}).get("consents") or [])
        withheld: Counter = Counter()
        items: list[Item] = []

        if mode == "raw":
            for ent, by_id in recs.items():
                for eid, rec in by_id.items():
                    items.append(Item(ent, eid, {f: FactView(v) for f, v in rec.items()}))
        else:
            if purpose not in P.PURPOSES:
                raise ValueError(f"unknown purpose {purpose!r}")
            pur = P.PURPOSES[purpose]
            invitee_ids = {eid for eid, r in recs.get("contact", {}).items()
                           if purpose == "scheduling" and self._in_scope(purpose, "contact", r, scope)}
            for ent, by_id in recs.items():
                for eid, rec in by_id.items():
                    if ent not in pur.fields:
                        withheld["entity_not_needed_for_purpose" if ent != "health_day" else "special_category"] += len(rec)
                        continue
                    if not self._in_scope(purpose, ent, rec, scope):
                        withheld["out_of_scope"] += len(rec)
                        continue
                    views = {}
                    for f, v in rec.items():
                        d = P.field_decision(pur, ent, f, consents)
                        if not d.allowed:
                            withheld[d.reason] += 1
                            continue
                        src, upd, conflict, cands = meta[(ent, eid, f)]
                        tr: Trust = assess(ent, f, updated_at=upd, source=src, conflict=conflict, candidates=cands,
                                           now=self.now, source_last_seen=last_seen.get(src))
                        view = FactView(v, src, upd.isoformat(), tr.score, tr.actionable, tr.reasons,
                                        untrusted_text=ENTITIES[ent][f].free_text)
                        mask = pur.masks.get((ent, f))
                        if mask == "busy_unless_invitee" and not (set(rec.get("attendees") or []) & invitee_ids):
                            view.value, view.masked = "Busy", True
                        views[f] = view
                    if ent == "event" and "attendees" in views:  # other people's ids aren't needed either
                        views["attendees"].value = [a for a in views["attendees"].value or [] if a in invitee_ids]
                    items.append(Item(ent, eid, views))

        bundle = ContextBundle(uuid.uuid4().hex[:12], user_id, purpose, scope, mode, items, dict(withheld),
                               sorted(consents), round((time.perf_counter() - t0) * 1000, 2))
        self.audit.append({"request_id": bundle.request_id, "at": datetime.now(timezone.utc).isoformat(),
                           "agent_id": agent_id, "user_id": user_id, "purpose": purpose, "mode": mode,
                           "scope": scope, "records": len(items), "fields_released": bundle.field_count(),
                           "fields_withheld": sum(withheld.values())})
        return bundle

    def close(self):
        self.con.close()
