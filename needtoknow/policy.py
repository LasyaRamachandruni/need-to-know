"""Purpose-based access policy.

An agent never asks for "the user's data". It asks for data *for a purpose*, and
each purpose lists the entities and fields it may see:

- Fields not listed for the purpose are withheld.
- Special-category data (health) is never available to these purposes.
- Some fields are released only with a user consent (precise photo location).
- Within an entity, a scope narrows which records are returned (only the
  recipient contact, only the photos in the album being shared). This is data
  minimization: the smallest context that can do the job.
- Some fields are masked rather than dropped. For scheduling, the agent sees that
  you're busy at 3pm, not that it's "Therapy".

The policy also says which actions each purpose may take.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import ENTITIES, Sensitivity


@dataclass(frozen=True)
class Purpose:
    name: str
    description: str
    fields: dict[str, tuple[str, ...]]
    actions: tuple[str, ...]
    masks: dict[tuple[str, str], str] = field(default_factory=dict)  # (entity, field) -> mask name


PURPOSES: dict[str, Purpose] = {p.name: p for p in [
    Purpose(
        "storage_cleanup", "Find space to free in iCloud storage",
        {"account": ("plan", "quota_gb", "used_gb"),
         "file": ("name", "folder", "kind", "size_mb", "created_at", "last_opened_at", "backed_up", "favorite", "duplicate_of"),
         "photo": ("taken_at", "album", "size_mb", "is_screenshot", "favorite", "duplicate_of")},
        actions=("move_to_recently_deleted", "delete_permanently"),
    ),
    Purpose(
        "photo_sharing", "Share a set of photos with one person",
        {"photo": ("taken_at", "city", "album", "people", "size_mb", "is_screenshot", "favorite", "duplicate_of", "caption", "lat", "lon"),
         "contact": ("name", "relation", "email", "phone")},
        actions=("share_photos",),
    ),
    Purpose(
        "backup_support", "Explain why a device isn't backing up",
        {"account": ("plan", "quota_gb", "used_gb"),
         "device": ("name", "model", "backup_enabled", "last_backup_at", "last_seen_at", "reported_free_gb")},
        actions=(),
    ),
    Purpose(
        "scheduling", "Find a time and invite one person",
        {"event": ("title", "start", "end", "attendees"),
         "contact": ("name", "relation", "email")},
        actions=("create_event",),
        masks={("event", "title"): "busy_unless_invitee"},
    ),
]}

# fields that are released only when the user has given a specific consent
CONSENT_REQUIRED: dict[tuple[str, str], str] = {
    ("photo", "lat"): "share_precise_location",
    ("photo", "lon"): "share_precise_location",
}


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def field_decision(purpose: Purpose, entity: str, name: str, consents: set[str]) -> Decision:
    f = ENTITIES[entity][name]
    if f.sensitivity >= Sensitivity.SPECIAL:
        return Decision(False, "special_category")
    if name not in purpose.fields.get(entity, ()):
        return Decision(False, "not_needed_for_purpose")
    needed = CONSENT_REQUIRED.get((entity, name))
    if needed and needed not in consents:
        return Decision(False, "no_consent")
    return Decision(True)


def action_allowed(purpose: Purpose, action: str) -> bool:
    return action in purpose.actions
