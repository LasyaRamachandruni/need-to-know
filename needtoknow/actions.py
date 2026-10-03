"""Actions an agent can propose, and the gate every action passes through.

The agent proposes and the gate decides. Each action ends up in one of three states:
- **executed**: possibly with changes. For example, location stripped from shared
  photos when the user hasn't consented to sharing it.
- **needs_confirmation**: the action may be right, but the user has to say yes.
  This covers irreversible deletes, protected items, and recipients whose address
  is in doubt.
- **blocked**: never allowed for this task. This covers actions outside the
  purpose, targets the agent wasn't given, and recipients who aren't the person
  the task names.

Rules, in order:
1. **Purpose binding.** A photo-sharing task can share photos. It can't delete files.
2. **Task binding.** Every target must be in the context bundle the agent was
   given, and every recipient must be a contact in that bundle. An address that
   appears only inside free text, such as a caption saying "share everything with
   x@y.com", can never become a recipient. This is what stops prompt injection
   from turning into action.
3. **Trust.** Any value the action depends on (the recipient's email, say) must be actionable.
4. **Safety.** Favorites and files with no server copy are protected. Permanent
   deletes always need confirmation. Precise location leaves only with consent.

In `raw` mode (the evaluation baseline) the gate approves everything as proposed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import policy as P
from .context import ContextBundle

INFO_ACTIONS = {"answer", "request_confirmation"}


@dataclass
class Action:
    kind: str
    params: dict = field(default_factory=dict)
    rationale: str = ""


@dataclass
class Outcome:
    action: Action
    status: str  # executed | needs_confirmation | blocked
    reasons: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)  # what the gate altered before executing


def _targets(a: Action) -> list[tuple[str, str]]:
    if a.kind in ("move_to_recently_deleted", "delete_permanently"):
        return [tuple(t) for t in a.params.get("items", [])]
    if a.kind == "share_photos":
        return [("photo", p) for p in a.params.get("photo_ids", [])]
    if a.kind == "share_contacts":
        return [("contact", c) for c in a.params.get("contact_ids", [])]
    return []


def review(bundle: ContextBundle, action: Action) -> Outcome:
    if bundle.mode == "raw":
        return Outcome(action, "executed")
    if action.kind in INFO_ACTIONS:
        return Outcome(action, "executed")

    purpose = P.PURPOSES[bundle.purpose]
    if not P.action_allowed(purpose, action.kind):
        return Outcome(action, "blocked", [f"'{action.kind}' is not allowed for {bundle.purpose}"])

    missing = [t for t in _targets(action) if bundle.find(*t) is None]
    if missing:
        return Outcome(action, "blocked", [f"{len(missing)} target(s) were not part of this task's context"])

    reasons: list[str] = []
    changes: list[str] = []

    # recipients: must be a contact the task named, reached at an address we trust
    if action.kind in ("share_photos", "create_event", "share_contacts"):
        cid = action.params.get("contact_id")
        address = action.params.get("address")
        contact = bundle.find("contact", cid) if cid else None
        if contact is None:
            return Outcome(action, "blocked", ["recipient is not the contact this task is about"])
        matches = [f for f in ("email", "phone") if f in contact.fields and contact.fields[f].value == address]
        if not matches:
            return Outcome(action, "blocked", [f"{address!r} is not an address of the task's contact"])
        view = contact.fields[matches[0]]
        if not view.actionable:
            reasons.append(f"recipient {matches[0]} is uncertain: " + "; ".join(view.reasons))

    if action.kind in ("move_to_recently_deleted", "delete_permanently"):
        protected = []
        for ent, eid in _targets(action):
            it = bundle.find(ent, eid)
            if it.fields.get("favorite") and it.fields["favorite"].value:
                protected.append(f"{eid} is a favorite")
            if ent == "file" and it.fields.get("backed_up") and it.fields["backed_up"].value is False:
                protected.append(f"{eid} has no copy in iCloud; deleting it loses it")
        if protected:
            reasons.append(f"{len(protected)} protected item(s): " + "; ".join(protected[:3]))
        if action.kind == "delete_permanently":
            reasons.append("permanent deletion can't be undone")

    if action.kind == "share_photos" and "share_precise_location" not in bundle.consents:
        if not action.params.get("strip_location", False):
            action.params["strip_location"] = True
            changes.append("precise location removed: the user hasn't consented to sharing it")

    if reasons:
        return Outcome(action, "needs_confirmation", reasons, changes)
    return Outcome(action, "executed", [], changes)
