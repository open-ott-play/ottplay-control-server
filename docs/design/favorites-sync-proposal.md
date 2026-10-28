# Favorites profile synchronization — proposal

**Status: PROPOSED; not a frozen protocol or an implemented capability.** Player
adapter decisions, portable bindings, locator support, limits and recovery must
be confirmed with OTT kiosk before production changes. Existing `favorites` v1
remains unchanged. This proposal does not activate a player or change playback.

## Ownership and local configuration

Visible membership belongs to the existing channel `favoritesLibrary` and
`mediaJournal.favorites` stores. `remoteFavorites` is the legacy alias/locator
catalog, not another source of visible membership. A catalog row without a
channel/media membership is a **legacy orphan**: report it separately, but do not
export, count or import it as an active favorite automatically.

Collection produces a normalized view, not a fourth persistent player store.
The approved local shape stays in the existing `cli.json`:

```json
{
  "preset_favorites": {
    "version": 1,
    "presets": {
      "home": {"version": 1, "entries": []}
    }
  }
}
```

Keys are canonical CLI **preset** names, not numbered provider profiles. Preserve
all unrelated configuration. Group collection has one coordinator and one
conditional, atomic local-file update after all selected players/pages succeed;
individual targets must not overwrite the preset independently. Detect concurrent
local configuration changes before replacing the file.

An absent companion or absent preset key means **do not synchronize favorites**.
An explicit empty document is different: merge keeps existing membership, while
replace/clear proposes removing membership in the agreed scope. Retain orphan
preset keys for inspection; do not silently rename, attach or delete them.

The file update uses an exact-byte revision, a config-specific writer lock, an
exact original-byte private backup and atomic replacement. A merge must carry
the revision captured before collecting player snapshots through planning and
commit. No-op and dry-run operations create no backup or lock file. A replacement
followed by a failed durability check has an uncertain outcome; report that fact
and retain the backup, without replaying or claiming rollback.

This is optimistic conflict detection among cooperating writers, not a kernel
compare-and-swap. An old CLI or editor ignoring the lock can overwrite a newer
document. Old CLI builds retain the top-level companion during ordinary complete
configuration writes, but ignore it during preset loading and omit it from their
checkpoint digest. Do not run mixed-version configuration writers concurrently.

## Proposed CLI workflow

The parser and local-storage seams are under fixture testing; these are proposed
commands, **not a claim that the installed CLI supports synchronization**:

```sh
ott t1 fav collect Home --dry-run
ott t1,t2 fav collect Home merge
ott presets Home fav validate
ott presets Home fav export favorites.json
ott presets Home fav diff favorites.json
ott presets Home fav import favorites.json replace --dry-run
ott presets Home fav restore BACKUP_FILE --dry-run
ott t1 fav diff Home
ott t1 fav load Home merge --dry-run
ott t1 fav load Home replace
ott t1 fav clear --dry-run
ott t1 fav clear
```

`collect` reads complete player snapshots into the existing local named preset;
`load` changes player membership using that saved document. Offline import/export
operate on the local companion only. Preview never clears or loads a player.
An explicit clear command is mutation intent; implementing it does not authorize
running it against devices as part of development or testing.

Local `restore` reads the selected preset's document from one of this
configuration's own unchanged private backups. It replaces only that favorites
document, through the same preview and conditional write path. It does not restore
old credentials, aliases, provider settings or the rest of the configuration, and
does not load a player. Use the backup path returned by the earlier operation;
renamed/moved backups and backups without the selected document are rejected.

Resolve aliases before group collection and read each distinct device once.
Merge combines the original preset with every complete source snapshot; replace
uses the union of all source snapshots. Compute one final delta against the
original preset, then perform one conditional local-file update. A failed or
incomplete source prevents the group update. Applying to multiple players has
per-player outcomes, not an all-player atomic transaction.

Reports distinguish favorite-item identities from list memberships. One item in
two lists is one item and two memberships. For each, report before/after totals
and added/removed/retained counts; `before = removed + retained` and
`after = added + retained`. Skipped duplicate inputs and resolved/unresolved
conflicts are separate counts. Unresolved conflicts permit inspection but block
application. Receipts contain validated counts and identifiers, not raw provider
errors or private item payloads.

Ordinary named-preset loading must validate a present companion and negotiate
support before its first mutation, include it in the checkpoint digest, apply it
after provider/source configuration, and verify membership. Missing companions
retain the current load behavior. This integration is not implemented by the
parser/storage scaffold. Numbered Local profile import/export is a separate
operation and does not implicitly carry the named-preset companion.

Preset loading must use settings and favorites from the same configuration
snapshot. A pure canonical-name resolver must not recurse through full preset
validation. Check helper availability and read-only compatibility before provider
writes, but create the short-lived player staging plan only after those writes
finish; provider setup can exceed the staging TTL. On failure, retain the list of
already completed provider steps rather than claiming an atomic whole-preset
rollback. A server release alone does not install the new CLI helper modules.

## Proposed portable document

The document has exactly `version:1` and `entries`. Each proposed entry separates
identity, replay locator, display and memberships:

```json
{
  "version": 1,
  "entries": [{
    "identity": {
      "provider": "m3u",
      "profile_binding": "profile-v1:opaque-binding",
      "source_id": "source-v1:opaque-source",
      "item_id": "channel-v1:1234abcd.5678abcd",
      "kind": "live"
    },
    "locator": {
      "type": "channel",
      "channel_ref": {"itemId": "channel-v1:1234abcd.5678abcd"}
    },
    "display": {
      "label": "Evening news",
      "title": "News channel",
      "provider_label": "M3U",
      "profile_label": "Home",
      "profile_number": 1,
      "breadcrumbs": [],
      "program_label": "Evening bulletin"
    },
    "memberships": [{"store": "channels", "list_key": "Favorites"}]
  }]
}
```

- Deduplicate `provider + profile_binding + source_id + item_id + kind`. Merge
  memberships, not merely display rows. A legacy `fav-*` ID is an alias, not the
  portable deduplication key.
- A profile slot, label or channel number alone cannot identify a saved source.
  Changing an account/source in the same slot must invalidate an old binding.
- Use existing canonical provider IDs, including internal `offline` for Local
  where applicable. Display aliases do not rewrite identity.
- Title, programme label, breadcrumb and native path are never item identity.
  `kind` initially means `live` or `vod`; a programme label does not implicitly
  turn a channel favorite into an archive request.
- Memberships use existing list keys in the bound source. Media uses
  `{"store":"media","list_key":"favorites"}`. There is no `remote` membership.
- Display is a bounded, control-character-free safe projection, not a copy of a
  provider payload. Do not expose URL-shaped diagnostic values, credentials or
  query tokens in labels. Display conflicts must not create duplicate identities.

**Binding portability is unresolved.** Ordinary preset/profile APIs currently
carry numbers and settings, not a proven persistent profile UUID. The opaque
values above illustrate the required contract; they do not assert that such IDs
already exist or can be matched between installations. The adapter must specify
portable source/profile binding, explicit target mapping and migration. Do not
infer mappings from labels/slots, hash rotating authentication tokens as identity,
or treat a native installation-local source UUID as automatically portable.

All entry field names and encodings remain proposed. Reject unknown/duplicate
fields and malformed references instead of silently coercing or dropping them.

### Proposed locator variants

Locators resolve with credentials already configured on the target. They never
contain credentials, stream URLs, signed URLs or query tokens:

```json
{"type":"channel","channel_ref":{"itemId":"channel-v1:1234abcd.5678abcd"}}
{"type":"plex","rating_key":"78777"}
{"type":"vportal","object_id":"stable-provider-object-id","ancestor_ids":["stable-category-id"]}
{"type":"local","relative_path":"Cartoons/episode.mp4"}
```

- **Channel:** use the existing stable-reference codec under the exact binding;
  never fall back to a channel number or title match.
- **Plex:** resolve a rating key only on the configured server/library matching
  the binding. The same rating key on another server is a different item.
- **VPortal:** the adapter must confirm stable object/navigation IDs can rebuild
  a request using configured credentials. Breadcrumb labels are display only.
  If only a transient/signed URL is available, report an unsupported reference.
- **Local:** resolve a normalized relative path inside an already configured,
  granted source and verify the resolved stable item identity. Reject absolute
  paths, parent traversal, alternate roots and symlink escapes through existing
  native authorization. Import never grants or connects a new folder. The
  locator does not replace `source_id` and `item_id`.

An authorized native full path may appear only in an explicit `include_paths`
display export, for example `display.native_path`; omit it by default. Importers
ignore it for lookup, authorization and playback. Missing or changed bindings
are preview blockers, not permission to guess a match.

## Negotiation and provisional bounds

Add sibling capability and dedicated action `favorites_sync`. Keep legacy
favorites capability/operations/entries unchanged. A client first requires the
new marker, then sends a read-only snapshot request. Acceptance proves relay
support: an old controller rejects an unknown action before queueing. Unsupported
negotiation never falls back to legacy mutations or destructive remove/add loops.

Proposed operations: `snapshot`, `begin`, `append`, `preview`, `commit`, `status`,
`abort`. The capability advertises confirmed operations and limits. Provisional
planning quantities, pending the adapter audit:

- 1000 entries and 1 MiB for an aggregate document/active staged plan.
- 50 entries and 128 KiB maximum per snapshot page.
- 12 KiB maximum append data. The **entire serialized request** must still fit
  the existing 16 KiB RPC limit, including escaping, metadata and its envelope.
- One in-memory import plan per runtime, expiring within 120 seconds.

Existing per-store limits are not raised implicitly. Bound concurrent snapshots
and export memory as well as staging. Exceeding a limit fails explicitly; there
is no truncation or partial application. Quantities are not yet wire constants.

**Transfer capacity remains a freeze blocker.** The current controller retains
50 results per device for 60 seconds; reading a result does not free its slot.
Its device/controller rate limits are 120/240 HTTP requests per minute, including
POSTs and receipt reads. One MiB needs at least 86 nominal 12 KiB append requests,
and whole-entry packing can need substantially more. Snapshot, verification and
receipt requests add to that cost. Do not raise global guards to fit this draft.
Agree actual packing/page counts, pacing and a bounded whole-operation deadline
before advertising limits. An idle staging TTL with a separate absolute lifetime
is a candidate, not an approved change. A default 45-second CLI budget must not
silently become 45 seconds per chunk or allow a partial favorites application.

## Snapshot and complete-or-refuse collection

An initial snapshot captures an explicit selection, such as `current` or
`all_configured`, without selecting providers or starting playback. It returns
opaque scope/revision/snapshot IDs. Subsequent pages use that snapshot's opaque
cursor. Each response is bound to runtime, snapshot, scope and revision, with a
stable total and deterministic order. The final page ends the cursor and proves
complete coverage; a document digest covers the assembled document.

Collect updates the local preset only after every selected target/page is
verified and deduplicated. Unreadable stores, expired/missing pages, changed
bindings or unrepresentable active references prevent that update. Offline items
with valid identities remain represented as unavailable rather than disappearing.
Legacy-orphan counts are separate diagnostics, not imported memberships.

### Revision and digest semantics — adapter decision required

Keep three distinct meanings:

1. `document_digest` binds the exact transferred document representation under
   the agreed serialization/canonicalization rule. Its captured display values
   may be included; this is transport integrity, not a membership CAS revision.
2. `revision` covers authoritative memberships, stable context/bindings and any
   replay-relevant locator state. It must not change merely because a programme
   title, availability flag, playback timestamp or history position changed.
3. `plan_digest` binds the resolved intended membership state, stable locators
   and explicitly requested persistent metadata changes. It excludes volatile
   display. For merge it includes retained memberships, so it can differ from
   the uploaded document digest.

The adapter must define these projections and concurrent-writer ownership before
wire freeze. Do not simply hash the whole media journal or current display, and
do not invent UUID mappings to make two contexts appear equivalent. User-authored
renames require an explicit policy distinct from live programme/title updates.

## Bounded staging and conditional commit

All requests carry a fresh runtime. Results bind runtime/op and relevant
scope/snapshot/plan/digest, including retained duplicate acknowledgements.

1. **Begin:** scope, expected revision, `merge|replace|clear`, declared count and
   `document_digest`. Return a bounded temporary plan ID and expiry; no membership
   storage is written.
2. **Append:** plan ID, sequence, entries and chunk digest. Accept an identical
   sequence only once; different content for that sequence conflicts. Enforce
   cumulative limits before admission.
3. **Preview:** verify contiguous chunks, count and full input digest, resolve
   identities, then seal the plan. Return before/after counts, added/kept/removed,
   duplicates/unresolved/conflicts/orphans, expected revision and `plan_digest`.
   Blockers produce `ready_to_commit:false`. Appends cannot change a sealed plan.
4. **Commit:** sealed plan, scope, expected revision, `plan_digest`, and explicit
   `commit_id`. Recheck runtime, policy, bindings and current persisted memberships
   before the first write. Conflict rejects without writing. Success echoes the
   commit ID, scope, plan digest, before/after revisions, summary and verified
   persistence.
5. **Status/abort:** status is read-only and distinguishes staging, previewed,
   committing, committed, aborted, expired and unknown. Abort releases a
   pre-commit plan; it cannot cancel an already started commit and imply rollback.

The outer `idempotency_key` is retained by the controller but **not forwarded to
the player**. Therefore player status needs an explicit bounded `commit_id` in
the payload. Use one corresponding outer key and an unchanged payload, without
automatic commit replay. Different content with the same identifier conflicts.

Normal states: `staging → previewed → committing → committed`. Before committing,
expiry/cancellation/conflict leaves memberships unchanged. After the first write,
report committed, verified rollback, or `outcome_unknown`/`recovery_required`;
deadline expiry alone cannot justify claiming an untouched rejection.

Merge preserves existing memberships and labels unless an explicit rename policy
says otherwise. Replace affects only the selected scope. Clear is an empty
candidate through the same preview/CAS path: remove memberships, preserving list
structure, media history/progress, unrelated scopes, playback, active
provider/profile and registry-only rows.

## Rollback limits and unknown acknowledgements

This draft does **not** promise crash-atomic writes across existing storage keys.
The player adapter must define a common writer/recovery boundary before that
guarantee is advertised. A controller queue/journal is not such a transaction,
and an in-memory plan is not a durable fourth favorites store.

Media writes must preserve the latest unrelated history/progress fields, not
restore an old whole-document snapshot. All UI/background writers need compatible
ownership checks. Rollback may restore only values still owned by the attempted
transaction while preserving unrelated updates. Failed write/readback/rollback
or lost ownership cannot produce `persisted:true` or a false “unchanged” result.

For an uncertain commit acknowledgement:

1. Preserve commit/plan/digest/request IDs, base revision and failure stage.
2. Read controller receipt/operation status and available player commit status.
   Do not replay the mutation to discover its outcome.
3. Read a fresh complete snapshot. Matching the candidate proves current state,
   not which operation produced it. Different state is a conflict; missing
   evidence remains unknown. Even an unchanged snapshot does not authorize replay
   while the original effect may still be pending.
4. After restart, absent durable evidence remains unknown. A new application
   attempt requires a fresh snapshot/preview and explicit new intent.

The existing preset-session recovery path may clear an unknown settings request
after its `safe_after` deadline and then compare/retry settings. Favorites commits
must not inherit that rule. They need a pending descriptor carrying commit ID,
plan digest, base revision, runtime and scope, followed by read-only receipt/status
and snapshot recovery. Deadline expiry cannot clear an uncertain membership
commit or authorize replay. With no companion, keep the current normalized preset
and checkpoint digest unchanged for compatibility.

Proposed errors cover malformed/unsupported requests, invalid references, policy
denial, runtime/profile/source change, revision/chunk conflict, incomplete or
expired snapshot/plan, limits, storage failure, busy, timeout/cancelled, unknown
outcome and recovery required. Final enum names await agreement. Never return raw
provider error text, credentials or URLs.

## Remaining wire-freeze decisions

- Enumerating configured scopes without changing playback, and preserving
  unresolved active membership references losslessly.
- Stable portable profile/source bindings despite today's number/settings-based
  APIs; explicit mapping/migration without labels, slot guesses or auth-token
  hashes. Confirm VPortal and Local codec support.
- Exact revision/document/plan projections excluding volatile display from CAS
  and plan identity, with a separate policy for persistent user renames.
- Platform-specific multi-store ownership, rollback and recovery guarantees.
- **Legacy v1 list after clear:** it currently reads the alias catalog and may
  still show registry-only rows. Decide/document this; do not silently rewrite v1.
- **Alias pruning:** default clear/replace does not prune `remoteFavorites`.
  Any pruning or migration needs a separate explicit policy.
- Actual capacities, safe display limits, snapshot lifetime and merge ordering.
- A lossless collection/list manifest for named and empty channel lists, their
  ordering/default-list provenance, and unresolved references. Entries alone
  must not be assumed to represent all existing library state.

## Focused acceptance cases

- Orphans are excluded; enrichment cannot create membership. One identity in
  several lists retains every membership.
- Renumbering, labels, equal Plex IDs on different servers and native paths cannot
  substitute identity. Missing Local grants are not created.
- Exports are complete or refused; expired/missing pages and unrepresentable
  references leave the local preset unchanged. Group targets cannot overwrite
  each other's collected data.
- Same-slot source replacement and membership edits after preview conflict before
  writes. Programme/availability/timestamp changes do not cause false conflicts.
- Concurrent history updates survive both commit and rollback. Inject failure
  after each persistence step: verified ownership-safe rollback or explicit
  uncertainty, never false success or “unchanged”.
- Duplicate chunks/commits do not duplicate effects; changed content conflicts.
  Invalid/extra fields and credential-bearing locators are rejected.
- Clear preserves list structure, history, unrelated scopes, registry-only rows
  and active playback. Bounds fail without slicing or applying a prefix.
- Lost ACK, restart, expired receipt, runtime change and unavailable status never
  trigger automatic commit replay.
- Missing companions keep the exact existing preset digest, RPC sequence and
  helper requirements. A present document changes the checkpoint revision;
  invalid/unsupported companions stop before any provider mutation.
- Slow provider setup cannot expire a staging plan created prematurely. Settings
  and favorites come from one configuration snapshot; final membership verification
  failures retain the summary of completed provider changes.
- Resuming an uncertain favorites commit performs no mutation POST, even after
  the generic settings `safe_after` deadline.
