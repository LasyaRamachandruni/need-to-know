"""Data catalog: every entity and field, with a sensitivity label.

Labels, from least to most sensitive:

  metadata   system facts about storage and devices (sizes, timestamps, flags)
  personal   identifies or describes the user's life (names, titles, cities, contacts' emails)
  sensitive  could cause harm if exposed (precise location, home addresses, private notes)
  special    special-category data (health); never released without explicit consent

`free_text` marks fields whose content was written by people, not systems
(captions, notes). Their content is data, never instructions: the context layer
passes it on quoted and flagged as untrusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Sensitivity(IntEnum):
    METADATA = 0
    PERSONAL = 1
    SENSITIVE = 2
    SPECIAL = 3


@dataclass(frozen=True)
class Field:
    name: str
    sensitivity: Sensitivity
    description: str
    free_text: bool = False


M, P, S, X = Sensitivity.METADATA, Sensitivity.PERSONAL, Sensitivity.SENSITIVE, Sensitivity.SPECIAL

ENTITIES: dict[str, dict[str, Field]] = {
    "account": {f.name: f for f in [
        Field("plan", M, "storage plan name"),
        Field("quota_gb", M, "storage quota"),
        Field("used_gb", M, "storage used, as measured by the server"),
        Field("consents", P, "data-use permissions the user has granted"),
    ]},
    "device": {f.name: f for f in [
        Field("name", P, "device name, e.g. 'Maya's iPad'"),
        Field("model", M, "device model"),
        Field("backup_enabled", M, "whether iCloud backup is on"),
        Field("last_backup_at", M, "time of the last completed backup"),
        Field("last_seen_at", M, "last time the device contacted the server"),
        Field("reported_free_gb", M, "free space on the device, as last reported by the device"),
    ]},
    "file": {f.name: f for f in [
        Field("name", P, "file name"),
        Field("folder", P, "folder path"),
        Field("kind", M, "document, video, archive, ..."),
        Field("size_mb", M, "size"),
        Field("created_at", M, "creation time"),
        Field("last_opened_at", M, "last time the file was opened"),
        Field("backed_up", M, "whether a server copy exists"),
        Field("favorite", M, "marked as a favorite by the user"),
        Field("duplicate_of", M, "file id this is a byte-identical copy of"),
        Field("note", S, "user's private note on the file", free_text=True),
    ]},
    "photo": {f.name: f for f in [
        Field("taken_at", P, "capture time"),
        Field("city", P, "city where it was taken"),
        Field("lat", S, "precise latitude"),
        Field("lon", S, "precise longitude"),
        Field("people", P, "people tagged in the photo"),
        Field("album", P, "album name"),
        Field("size_mb", M, "size"),
        Field("is_screenshot", M, "screenshot rather than a camera photo"),
        Field("favorite", M, "marked as a favorite"),
        Field("duplicate_of", M, "photo id this duplicates"),
        Field("caption", P, "caption written by the user", free_text=True),
    ]},
    "contact": {f.name: f for f in [
        Field("name", P, "contact's name"),
        Field("relation", P, "relationship to the user, e.g. 'mom'"),
        Field("email", P, "email address"),
        Field("phone", P, "phone number"),
        Field("birthday", P, "birthday"),
        Field("address", S, "home address"),
        Field("note", S, "private note about the contact", free_text=True),
    ]},
    "event": {f.name: f for f in [
        Field("title", P, "event title"),
        Field("start", P, "start time"),
        Field("end", P, "end time"),
        Field("location", P, "event location"),
        Field("attendees", P, "attendee contact ids"),
        Field("note", S, "event notes", free_text=True),
    ]},
    "health_day": {f.name: f for f in [
        Field("date", X, "day"),
        Field("steps", X, "step count"),
        Field("resting_hr", X, "resting heart rate"),
        Field("sleep_hours", X, "hours asleep"),
    ]},
}


def field(entity: str, name: str) -> Field:
    return ENTITIES[entity][name]


def sensitivity(entity: str, name: str) -> Sensitivity:
    return ENTITIES[entity][name].sensitivity
