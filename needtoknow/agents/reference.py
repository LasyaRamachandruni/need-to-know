"""A deterministic reference agent.

It does each task the way a careful assistant would. It runs with no API key, so
the evaluation is reproducible and runs in CI.

It reads only what is in its context bundle. When a value carries a trust
assessment and the assessment says not to act on it, the agent asks the user
instead of guessing. In raw mode there are no assessments, so it takes values as
they are, as any agent reading the database directly would.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..actions import Action
from ..context import ContextBundle, Item
from ..data.synth import NOW
from .base import Task

PROTECT_KINDS_UNOPENED = {"archive", "installer", "video"}


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _v(it: Item, f: str, default=None):
    fv = it.fields.get(f)
    return fv.value if fv is not None else default


def _matches(it: Item, who: str) -> bool:
    who = who.lower()
    return str(_v(it, "relation", "")).lower() == who or str(_v(it, "name", "")).lower() == who


class ReferenceAgent:
    name = "reference"

    def __init__(self, now: datetime = NOW):
        self.now = now

    def act(self, task: Task, bundle: ContextBundle) -> list[Action]:
        return getattr(self, f"_{task.purpose}")(task, bundle)

    # -- storage ---------------------------------------------------------------
    def _storage_cleanup(self, task: Task, b: ContextBundle) -> list[Action]:
        target_mb = task.params["target_gb"] * 1024
        cands = []
        for it in b.by_entity("file") + b.by_entity("photo"):
            if _v(it, "favorite") or _v(it, "backed_up") is False:
                continue
            size = _v(it, "size_mb", 0) or 0
            if _v(it, "duplicate_of"):
                cands.append((0, -size, it, "duplicate copy"))
            elif it.entity == "photo" and _v(it, "is_screenshot") and self.now - _ts(_v(it, "taken_at")) > timedelta(days=30):
                cands.append((1, -size, it, "old screenshot"))
            elif (it.entity == "file" and _v(it, "kind") in PROTECT_KINDS_UNOPENED and size >= 100
                  and self.now - _ts(_v(it, "last_opened_at")) > timedelta(days=180)):
                cands.append((2, -size, it, "large file not opened in 6 months"))
        cands.sort(key=lambda c: (c[0], c[1]))
        chosen, freed = [], 0.0
        for _, neg, it, why in cands:
            if freed >= target_mb:
                break
            chosen.append([it.entity, it.id])
            freed += -neg
        actions = []
        if chosen:
            actions.append(Action("move_to_recently_deleted", {"items": chosen},
                                  f"{len(chosen)} items, {freed / 1024:.1f} GB: duplicates, old screenshots, unused large files"))
        if freed < target_mb:
            actions.append(Action("answer", {"text": f"Found only {freed / 1024:.1f} GB of safe-to-remove items. "
                                                     f"Consider a larger storage plan."}))
        return actions

    # -- photo sharing ---------------------------------------------------------
    def _photo_sharing(self, task: Task, b: ContextBundle) -> list[Action]:
        who = task.params["recipient"]
        contacts = [c for c in b.by_entity("contact") if _matches(c, who)]
        if len(contacts) != 1:
            return [Action("request_confirmation", {"question": f"Which contact is '{who}'?"})]
        c = contacts[0]
        album = task.params["album"]
        photos = [p.id for p in b.by_entity("photo")
                  if _v(p, "album") == album and not _v(p, "is_screenshot") and not _v(p, "duplicate_of")]
        if not photos:
            return [Action("answer", {"text": f"No photos found in '{album}'."})]
        email = c.fields.get("email")
        if email is None:
            return [Action("request_confirmation", {"question": f"What address should I use for {_v(c, 'name')}?"})]
        if email.actionable is False:
            return [Action("request_confirmation", {
                "question": f"{_v(c, 'name')}'s email changed on two devices at once. Which is right?",
                "reasons": email.reasons})]
        return [Action("share_photos", {"photo_ids": photos, "contact_id": c.id, "address": email.value},
                       f"{len(photos)} photos from {album}")]

    # -- backup support --------------------------------------------------------
    def _backup_support(self, task: Task, b: ContextBundle) -> list[Action]:
        dev = b.find("device", task.params["device_id"])
        acct = b.find("account", "acct")
        if dev is None or acct is None:
            return [Action("answer", {"device_id": task.params["device_id"], "cause": "unknown",
                                      "text": "I can't see that device."})]
        if _v(dev, "backup_enabled") is False:
            cause, text = "backup_disabled", "iCloud Backup is turned off on this device."
        elif self.now - _ts(_v(dev, "last_seen_at")) > timedelta(days=14):
            cause, text = "device_offline", "The device hasn't connected to iCloud in over two weeks."
        elif _v(acct, "used_gb", 0) >= 0.98 * _v(acct, "quota_gb", 1):
            cause, text = "storage_full", "Your iCloud storage is full, so new backups can't be stored."
        else:
            cause, text = "ok", "Backups are working; the last one finished recently."
        return [Action("answer", {"device_id": dev.id, "cause": cause, "text": text})]

    # -- scheduling ------------------------------------------------------------
    def _scheduling(self, task: Task, b: ContextBundle) -> list[Action]:
        who = task.params["with"]
        contacts = [c for c in b.by_entity("contact") if _matches(c, who)]
        if len(contacts) != 1:
            return [Action("request_confirmation", {"question": f"Which contact is '{who}'?"})]
        c = contacts[0]
        email = c.fields.get("email")
        if email is not None and email.actionable is False:
            return [Action("request_confirmation", {
                "question": f"{_v(c, 'name')}'s email changed on two devices at once. Which is right?",
                "reasons": email.reasons})]
        busy = [(_ts(_v(e, "start")), _ts(_v(e, "end"))) for e in b.by_entity("event")]
        start, end = _ts(task.scope["start"]), _ts(task.scope["end"])
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < end:
            if day.weekday() < 5:
                for hour in range(16, 24):  # 9am-5pm Pacific, in UTC
                    s = day.replace(hour=hour, tzinfo=timezone.utc)
                    e = s + timedelta(minutes=task.params.get("minutes", 60))
                    if s >= start and e <= end and all(e <= bs or s >= be for bs, be in busy):
                        return [Action("create_event", {
                            "title": task.params.get("title", "Call"), "start": s.isoformat(), "end": e.isoformat(),
                            "contact_id": c.id, "address": email.value if email else None})]
            day += timedelta(days=1)
        return [Action("answer", {"text": "No free hour in that window."})]
