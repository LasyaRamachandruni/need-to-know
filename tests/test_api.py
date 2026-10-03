"""The HTTP API around the context service and the gate."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from needtoknow.api import create_app  # noqa: E402
from needtoknow.cli import main  # noqa: E402


@pytest.fixture()
def client(warehouse):
    with TestClient(create_app(str(warehouse))) as c:
        yield c


def _sharing_user(dataset):
    return next(u for u in dataset.truth.values() if u.trips and any(c["relation"] == "mom" for c in u.state["contact"].values())
                and not any(c["field"] == "email" for c in u.conflicts))


def test_context_then_review_then_audit(client, dataset):
    u = _sharing_user(dataset)
    album = u.trips[0]["album"]
    r = client.post("/v1/context", json={"user_id": u.user_id, "purpose": "photo_sharing",
                                         "scope": {"album": album, "recipient": "mom"}}, headers={"X-Agent-Id": "test-agent"})
    assert r.status_code == 200
    b = r.json()
    mom = next(i for i in b["items"] if i["entity"] == "contact")
    photos = [i["id"] for i in b["items"] if i["entity"] == "photo"][:5]
    ok = client.post("/v1/actions/review", json={"request_id": b["request_id"], "action": {
        "kind": "share_photos", "params": {"photo_ids": photos, "contact_id": mom["id"],
                                           "address": mom["fields"]["email"]["value"]}}})
    assert ok.json()["status"] in ("executed",)
    bad = client.post("/v1/actions/review", json={"request_id": b["request_id"], "action": {
        "kind": "share_photos", "params": {"photo_ids": photos, "contact_id": None, "address": "x@evil.test"}}})
    assert bad.json()["status"] == "blocked"
    audit = client.get("/v1/audit", params={"user_id": u.user_id}).json()
    assert audit["context_requests"][-1]["agent_id"] == "test-agent"
    assert [d["status"] for d in audit["action_decisions"]][-2:] == ["executed", "blocked"]


def test_errors(client, dataset):
    u = next(iter(dataset.truth))
    assert client.post("/v1/context", json={"user_id": u, "purpose": "marketing"}).status_code == 422
    assert client.post("/v1/context", json={"user_id": "nobody", "purpose": "backup_support"}).status_code == 404
    assert client.post("/v1/actions/review", json={"request_id": "nope", "action": {"kind": "answer"}}).status_code == 404
    assert set(client.get("/v1/purposes").json()) == {"storage_cleanup", "photo_sharing", "backup_support", "scheduling"}


def test_cli_end_to_end(tmp_path, capsys):
    d = ["--data-dir", str(tmp_path / "data")]
    assert main(d + ["demo", "--users", "6", "--out", str(tmp_path / "reports")]) == 0
    assert (tmp_path / "reports" / "REPORT.md").exists()
    out = capsys.readouterr().out
    assert "governed" in out and "report:" in out
