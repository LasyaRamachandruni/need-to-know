# Design

Agents that act on a person's cloud account face three problems that ordinary
access control doesn't solve:
1. They read far more than they need.
2. They can't tell a fact they can act on from one that's merely there.
3. They can be told what to do by anyone who can write text they'll read.

need-to-know handles each one at a separate layer, so each can be tested on its own.

## 1. Data: facts, not rows

A sync backend sees change events, not tidy rows. The pipeline (`data/pipeline.py`) does these things in order:

1. **Validates** each event against the catalog: known entity and field, right types,
   parseable timestamps. Failures go to a quarantine table with the reason.
2. **Deduplicates** by `event_id`. Delivery is at least once, so the same event can
   arrive twice. A replayed stream changes nothing.
3. **Orders** by server receive time, never the device's clock. Device clocks in the
   synthetic data are up to nine hours off. Skew is measured per device and
   reported, not trusted.
4. **Applies field by field.** Two devices editing different fields of one contact
   don't overwrite each other.
5. **Keeps provenance.** Each current value is a *fact*: value, source device,
   server time, number of writes. When the last two writes to a field came from
   different devices within ten minutes and disagree, the fact is flagged as a
   conflict, and both candidates are kept.

The result is exact. Tests compare every field of every record with the
generator's ground truth, also after shuffling arrival order and replaying the stream.

## 2. Context: purpose, scope, consent, masking

`ContextService.get(user, purpose, scope)` applies, per field:

| Rule | Example |
|---|---|
| Special category is never released | Health data, for any purpose |
| Field must be listed for the purpose | Photo sharing can't see contact addresses |
| Consent-gated fields need the consent | Precise photo location only with `share_precise_location` |
| Records must be in scope | Only the album being shared; only the contact being invited |
| Masking where the task needs the shape but not the content | Scheduling sees "Busy", not "Therapy" |

Every request is audited: which agent, which purpose, and how many fields were released and withheld.

### Why purpose-based and not role-based

An assistant has one identity but many jobs. If access were tied to the agent, it
would get the union of everything it ever needs. Tying it to the purpose of each
request lets the same agent see contacts' emails while sharing photos, and not
see them while cleaning up storage.

## 3. Trust: which facts can be acted on

`trust.assess` gives each released value a score and an `actionable` flag, with reasons:

- **Conflict:** score capped at 0.3. There's no right answer to pick, so the agent asks.
- **Staleness:** applies to fast-changing values like storage used or device free space.
  They decay with a per-field half-life. Names and emails don't decay.
- **Silent source:** a fast-changing value reported by a device that hasn't
  connected in two weeks describes the past.
- **Free text:** never actionable. It's content, and the bundle marks it as untrusted.

The evaluation shows why this matters. With direct access, the agent shared photos
or sent invites to a guessed address in 14 tasks. Through the layer, it asked the user in exactly those 14.

## 4. The gate: agents propose, the gate decides

Prompt injection can't be fully prevented at the model, so need-to-know makes
sure injected text can't *act*. Every action is checked against the bundle it was proposed from:

1. **Purpose binding:** a photo-sharing request can only share photos.
2. **Task binding:** every target must be in the bundle. A recipient must be the
   contact the task named, at an address that contact actually has. An address that
   appears only in a caption can never be a recipient.
3. **Trust:** the address used must be actionable.
4. **Safety:** favorites and files with no iCloud copy are protected. Permanent
   deletion needs confirmation. Precise location is stripped from shares without consent.

Outcomes are `executed` (possibly modified, with the modifications listed),
`needs_confirmation`, or `blocked`. In the evaluation, an agent that obeys every
planted instruction executed 24 injected actions with direct access and 0 through the gate.

## 5. Evaluation

`eval/scenarios.py` derives tasks from ground truth, plus the exact field instances
each task needs. That makes it possible to measure context precision and recall,
not just success:

| Metric | Meaning |
|---|---|
| Success | Correct outcome. When the right outcome is to ask, asking counts as success. |
| Wrong actions | Acted on a conflicted address, touched a protected item, or executed an injected instruction |
| Precision / recall | Needed field instances / released; released / needed |
| Sensitive without consent | Sensitive or special fields released beyond what the user consented to |
| Location without consent | Photo shares that would have sent precise coordinates |
| Injection reached / executed | Did planted text get into the context; did it cause an action |

The same agent runs in both modes, so the difference is the layer. The deterministic
agents make that measurement reproducible. An optional LLM agent (`agents/llm.py`)
runs against the same bundles and gate.

## What I'd do next

- **Per-record policies** to raise precision. Photo sharing doesn't need people tags
  for every photo, only enough to confirm the album.
- **Learned trust.** Fit the half-lives and source reliability from how often values
  later turned out to be wrong, rather than setting them by hand.
- **Differential privacy for aggregates.** Some purposes ("how much do I usually
  spend on storage?") only need counts and sums, which can be released with noise
  in place of rows.
- **Signed bundles.** Bind `request_id` to the agent's identity, so a bundle issued to one
  agent can't be used by another.
