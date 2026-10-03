"""Policy, trust, context bundles and the action gate."""

from datetime import timedelta

import pytest

from needtoknow.actions import Action, review
from needtoknow.catalog import ENTITIES, Sensitivity
from needtoknow.data.synth import NOW
from needtoknow.policy import PURPOSES
from needtoknow.trust import assess


def _user(dataset, pred):
    return next(u for u in dataset.truth.values() if pred(u))


def _mom(u):
    return next((cid for cid, c in u.state["contact"].items() if c["relation"] == "mom"), None)


# trust -----------------------------------------------------------------------

def test_trust_rules():
    fresh = assess("contact", "email", updated_at=NOW - timedelta(days=300), source="web", conflict=False,
                   candidates=None, now=NOW, source_last_seen=NOW)
    assert fresh.actionable and fresh.score == 1.0  # stable fields don't decay
    conflict = assess("contact", "email", updated_at=NOW, source="web", conflict=True, candidates=["a@x", "b@x"],
                      now=NOW, source_last_seen=NOW)
    assert not conflict.actionable and "a@x / b@x" in conflict.reasons[0]
    stale = assess("device", "reported_free_gb", updated_at=NOW - timedelta(days=20), source="dev_1", conflict=False,
                   candidates=None, now=NOW, source_last_seen=NOW - timedelta(days=20))
    assert not stale.actionable and any("last seen 20 days" in r for r in stale.reasons)
    text = assess("photo", "caption", updated_at=NOW, source="web", conflict=False, candidates=None, now=NOW,
                  source_last_seen=NOW)
    assert not text.actionable


# context ----------------------------------------------------------------------

def test_special_category_never_released(service, dataset):
    for u in list(dataset.truth)[:5]:
        for purpose in PURPOSES:
            b = service.get(u, purpose, {"album": "x", "recipient": "mom", "start": "2026-09-01", "end": "2026-10-01"})
            assert not b.by_entity("health_day")
            assert all(ENTITIES[i.entity][f].sensitivity < Sensitivity.SPECIAL for i in b.items for f in i.fields)


def test_every_released_field_is_listed_for_the_purpose(service, dataset):
    u = next(iter(dataset.truth))
    for purpose, p in PURPOSES.items():
        b = service.get(u, purpose, {"album": "x", "recipient": "mom", "start": "2026-09-01", "end": "2026-10-01"})
        for item in b.items:
            assert set(item.fields) <= set(p.fields[item.entity])


def test_photo_sharing_is_scoped_to_album_and_recipient(service, dataset):
    u = _user(dataset, lambda u: u.trips and _mom(u))
    trip = u.trips[0]
    b = service.get(u.user_id, "photo_sharing", {"album": trip["album"], "recipient": "mom"})
    assert {i.id for i in b.by_entity("photo")} >= set(trip["photo_ids"])
    assert all(i.fields["album"].value == trip["album"] for i in b.by_entity("photo"))
    assert [c.id for c in b.by_entity("contact")] == [_mom(u)]
    assert "address" not in b.by_entity("contact")[0].fields and b.withheld["out_of_scope"] > 0


def test_precise_location_needs_consent(service, dataset):
    for u in dataset.truth.values():
        if not u.trips:
            continue
        b = service.get(u.user_id, "photo_sharing", {"album": u.trips[0]["album"], "recipient": "mom"})
        has_loc = any("lat" in p.fields for p in b.by_entity("photo"))
        assert has_loc == ("share_precise_location" in u.state["account"]["acct"]["consents"])


def test_scheduling_masks_unrelated_event_titles(service, dataset):
    u = _user(dataset, lambda u: _mom(u))
    b = service.get(u.user_id, "scheduling", {"with": "mom", "start": "2026-08-01T00:00:00Z", "end": "2026-10-30T00:00:00Z"})
    mom = _mom(u)
    for e in b.by_entity("event"):
        real = u.state["event"][e.id]
        if mom in real["attendees"]:
            assert e.fields["title"].value == real["title"]
        else:
            assert e.fields["title"].value == "Busy" and e.fields["title"].masked
        assert set(e.fields["attendees"].value) <= {mom}


def test_raw_mode_is_everything(service, dataset):
    u = next(iter(dataset.truth.values()))
    b = service.get(u.user_id, None, mode="raw")
    assert len(b.items) == sum(len(r) for r in u.state.values())


def test_requests_are_audited(service, dataset):
    u = next(iter(dataset.truth))
    service.get(u, "backup_support", agent_id="support-bot")
    entry = service.audit[-1]
    assert entry["agent_id"] == "support-bot" and entry["purpose"] == "backup_support"
    assert entry["fields_released"] > 0 and entry["fields_withheld"] > 0


def test_unknown_purpose_rejected(service, dataset):
    with pytest.raises(ValueError):
        service.get(next(iter(dataset.truth)), "marketing")


# gate -------------------------------------------------------------------------

def _sharing(service, u):
    trip = u.trips[0]
    return service.get(u.user_id, "photo_sharing", {"album": trip["album"], "recipient": "mom"}), trip


def test_gate_blocks_actions_outside_purpose(service, dataset):
    u = _user(dataset, lambda u: u.trips and _mom(u))
    b, trip = _sharing(service, u)
    o = review(b, Action("delete_permanently", {"items": [["photo", trip["photo_ids"][0]]]}))
    assert o.status == "blocked" and "not allowed" in o.reasons[0]


def test_gate_blocks_recipients_outside_the_task(service, dataset):
    u = _user(dataset, lambda u: u.trips and _mom(u))
    b, trip = _sharing(service, u)
    o = review(b, Action("share_photos", {"photo_ids": trip["photo_ids"], "contact_id": None, "address": "x@evil.test"}))
    assert o.status == "blocked"
    mom = b.by_entity("contact")[0]
    o = review(b, Action("share_photos", {"photo_ids": trip["photo_ids"], "contact_id": mom.id, "address": "x@evil.test"}))
    assert o.status == "blocked" and "not an address" in o.reasons[0]


def test_gate_blocks_targets_not_in_context(service, dataset):
    u = _user(dataset, lambda u: len(u.trips) >= 2 and _mom(u))
    b, _ = _sharing(service, u)
    mom = b.by_entity("contact")[0]
    other = u.trips[1]["photo_ids"][:3]
    o = review(b, Action("share_photos", {"photo_ids": other, "contact_id": mom.id, "address": mom.fields["email"].value}))
    assert o.status == "blocked" and "not part of this task" in o.reasons[0]


def test_gate_strips_location_without_consent(service, dataset):
    u = _user(dataset, lambda u: u.trips and _mom(u) and not u.state["account"]["acct"]["consents"]
              and not any(c["entity_id"] == _mom(u) for c in u.conflicts))
    b, trip = _sharing(service, u)
    mom = b.by_entity("contact")[0]
    a = Action("share_photos", {"photo_ids": trip["photo_ids"], "contact_id": mom.id, "address": mom.fields["email"].value})
    o = review(b, a)
    assert o.status == "executed" and a.params["strip_location"] is True and o.changes


def test_gate_asks_before_using_a_conflicted_address(tmp_path):
    from needtoknow.context import ContextService
    from needtoknow.data.pipeline import build
    from needtoknow.data.synth import generate

    ds = generate(users=12, seed=3, conflict_rate=1.0)  # every contact edited on two devices at once
    build(ds.events, tmp_path / "w.duckdb")
    service = ContextService(tmp_path / "w.duckdb")
    u = _user(ds, lambda u: u.trips and any(c["field"] == "email" and c["entity_id"] == _mom(u) for c in u.conflicts))
    b, trip = _sharing(service, u)
    mom = b.by_entity("contact")[0]
    o = review(b, Action("share_photos", {"photo_ids": trip["photo_ids"], "contact_id": mom.id,
                                          "address": mom.fields["email"].value}))
    assert o.status == "needs_confirmation" and "uncertain" in o.reasons[0]


def test_gate_protects_favorites_and_local_only_files(service, dataset):
    u = _user(dataset, lambda u: any(not f["backed_up"] for f in u.state["file"].values()))
    b = service.get(u.user_id, "storage_cleanup")
    local = next(fid for fid, f in u.state["file"].items() if not f["backed_up"])
    o = review(b, Action("move_to_recently_deleted", {"items": [["file", local]]}))
    assert o.status == "needs_confirmation" and "no copy in iCloud" in o.reasons[0]
    safe = next(fid for fid, f in u.state["file"].items() if f["backed_up"] and not f["favorite"])
    assert review(b, Action("move_to_recently_deleted", {"items": [["file", safe]]})).status == "executed"
    assert review(b, Action("delete_permanently", {"items": [["file", safe]]})).status == "needs_confirmation"


def test_raw_mode_gate_approves_everything(service, dataset):
    u = next(iter(dataset.truth))
    b = service.get(u, None, mode="raw")
    assert review(b, Action("share_contacts", {"contact_ids": [], "address": "x@evil.test"})).status == "executed"
