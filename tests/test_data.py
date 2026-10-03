"""The event pipeline rebuilds the true state despite duplicates, disorder, skew and bad events."""

import json
import random

import pytest

from needtoknow.data.pipeline import reconcile, validate


def _state(facts):
    out = {}
    for r in facts.itertuples():
        out.setdefault((r.user_id, r.entity, r.entity_id), {})[r.field] = json.loads(r.value)
    return out


def _truth(dataset):
    return {(u, ent, eid): rec for u, t in dataset.truth.items() for ent, recs in t.state.items() for eid, rec in recs.items()}


def test_reconstructs_exact_state(dataset):
    facts, quarantine, _, stats = reconcile(dataset.events)
    assert _state(facts) == _truth(dataset)
    assert stats.duplicates == dataset.duplicates
    assert stats.quarantined == dataset.malformed == len(quarantine)


def test_arrival_order_does_not_matter(dataset):
    shuffled = dataset.events[:]
    random.Random(1).shuffle(shuffled)
    assert _state(reconcile(shuffled)[0]) == _truth(dataset)


def test_replaying_the_stream_is_idempotent(dataset):
    facts, _, _, stats = reconcile(dataset.events + dataset.events)
    assert _state(facts) == _truth(dataset)
    assert stats.duplicates == 2 * dataset.duplicates + stats.applied  # the whole second copy is a replay


def test_concurrent_edits_are_flagged_with_both_values(dataset):
    facts = reconcile(dataset.events)[0]
    flagged = {(r.entity_id, r.field): json.loads(r.candidates) for r in facts[facts.conflict].itertuples()}
    expected = {(c["entity_id"], c["field"]): c["values"] for t in dataset.truth.values() for c in t.conflicts}
    assert flagged == expected and len(expected) > 0


def test_device_clock_skew_is_measured_not_trusted(dataset):
    health = reconcile(dataset.events)[2]
    assert health.clock_skewed.any()
    # skewed devices still produce exact state (ordering uses server time): covered by test_reconstructs_exact_state


@pytest.mark.parametrize("event, reason", [
    ({"entity_id": None}, "missing entity_id"),
    ({"entity": "spaceship"}, "unknown entity"),
    ({"fields": {"nickname": "x"}}, "unknown field"),
    ({"fields": {"size_mb": "big"}}, "should be a number"),
    ({"fields": {"favorite": "yes"}}, "should be true/false"),
    ({"op": "merge"}, "unknown op"),
    ({"server_ts": "yesterday"}, "bad timestamp"),
])
def test_validation_reasons(event, reason):
    base = {"event_id": "e1", "user_id": "u1", "entity": "file", "entity_id": "f1", "op": "upsert",
            "fields": {"size_mb": 1.0}, "source": "web", "client_ts": "2026-01-01T00:00:00Z",
            "server_ts": "2026-01-01T00:00:00Z"}
    assert reason in validate(base | event)
    assert validate(base) is None
