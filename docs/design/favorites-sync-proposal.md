# Favorites profile synchronization — current design

**Status: local document commands are wired; remote synchronization remains disabled.**
The source CLI can validate, export, import, compare and restore saved documents.
Player wire, bindings, provider codecs and persistence adapters still need their
release gates; remote commands below remain intended UX, not installed capabilities.
Existing `favorites` v1 remains unchanged. Nothing here implicitly switches a
provider/profile, starts playback or changes the visible UI.

## Authority and portable bundle

Visible membership belongs to channel `favoritesLibrary` and
`mediaJournal.favorites`. `remoteFavorites` is an alias/locator catalog, not an
independent membership store. Preserve it untouched; registry-only orphans are
separate diagnostics, never automatic exports or active-favorite counts.
Preserve media history, resume positions and every unrelated source.

The companion lives in existing `cli.json`, under canonical CLI preset names:

```json
{
  "preset_favorites": {
    "version": 1,
    "presets": {"Home": {"version": 1, "scopes": []}}
  }
}
```

Each scope is a schema-1 document with exactly `schema`, `scope`, `coverage`,
`channelLists` and `mediaFavorites`. Its scope contains `provider`,
`profileBindingId` and `sourceBindingId`; the latter two are public persistent
UUIDs. The whole bundle, including every scope, must fit 1 MiB of UTF-8 JSON.
Reject duplicate keys, unknown fields, invalid UTF-8, nonfinite numbers and
unsupported references. Schema acceptance does not establish provider support.

Scope identity is `(provider, profileBindingId, sourceBindingId)`. Sort only the
bundle's scopes by that tuple. Preserve internal list/member order, multiplicity,
metadata and provenance. Different scopes union; identical same-scope documents
deduplicate. Differing same-scope content, including order or metadata, conflicts
in version one: block rather than merge items, rename lists or select a winner.
Merge includes the saved bundle among those inputs. Explicit replace unions the
incoming snapshots, rejects collisions between them, then computes the delta
against the original saved bundle. Intentionally replacing old saved contents is
allowed; never sum deltas against empty or silently resolve conflicting inputs.

Absent companions mean **do not synchronize favorites**. An empty scope list
supplies no scopes; it does not authorize clearing every player. Clear needs an
explicit supported scope and a planned empty membership state. Preserve orphan
preset keys for inspection instead of silently attaching or renaming them.

## Lossless lists and identity

Preserve named/empty channel lists, exact names/order, active list, original
default provenance, references and media favorites. Names are unique by exact
codepoints; order contains each list exactly once. Active and non-null default
values resolve within that scope. `default.present:false` forbids `value`; true
requires the original value, including explicit null where supported. Never
infer default provenance from whichever list happens to be active.

`shuffleBefore` is an ordered auxiliary reset baseline, not current membership.
The player retains it across edits: it may contain removed references and omit
newly added ones. Preserve it exactly. Do not require matching multisets, count
it as membership, union it into favorites, or reconstruct/prune it. Auxiliary
references neither authorize playback nor establish portable matches.

The first runtime target is **an explicitly bound current Plex source**. M3U's
existing item ID is a 32-bit URL hash, not proven portable identity. A new exact
matcher/codec is still required; neither that hash nor a channel number/title
substitute is sufficient. Plex identity binds machine identifier and rating key
to the explicitly paired authenticated server, using target-private credentials.

Portable profile/source UUIDs require explicit destination pairing. Labels,
slots, URLs, token hashes and renderer IDs are not bindings. Credential rotation
preserves identity; source replacement needs a new identity or reviewed rebind.
Missing/ambiguous binding blocks the complete operation. Imported UUIDs confer
no filesystem/network access. Local uses internal provider ID `offline` and a
separate root binding to an existing grant; relative locators grant no folders.
VPortal, Local, Stalker and other providers remain unsupported until exact codecs
and complete coverage are advertised. A schema enum is not such a promise.
Unresolved references remain faithful, inert and nonsecret or the complete scope
is refused. Never export credentials, stream/signed URLs, raw requests or native
absolute paths as portable locators. No recognized subset counts as complete.

## Intended CLI and local-file behavior

The `ott presets NAME fav ...` commands operate locally and never construct a
network client. The player-side collect/load/diff/clear commands remain disabled
before client creation or config writes:

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

Collect reads complete snapshots; load applies saved scoped favorites. Local
bundle merge/replace differs from player application: each approved scope is a
complete scoped replacement, not a remove/add loop or name-based merge. Preview
and diff do not write player state. Legacy `fav play NAME`, add/remove and aliases
retain their existing roles. No development task implicitly authorizes a live clear.

Resolve group aliases first and read each distinct device once. Collect every
complete scope before planning one local update. Any incomplete source/conflicting
duplicate blocks it. Multi-player load has per-player outcomes, not atomicity.
Reports separate items and memberships: one item in two lists is one item/two
memberships. Both obey `before = removed + retained`, `after = added + retained`.
Duplicates/conflicts are separate; `shuffleBefore` is excluded from membership
counts. Blocked preview exits nonzero; a completed diff may exit zero with changes.

Local storage preserves unrelated config. Snapshot its original byte revision
before collection; carry that revision into guarded apply. Cooperating writers
use a config lock, exact-byte private backup, fsync and atomic replacement, not
kernel CAS. Dry-run/no-op create neither lock nor backup. A replacement followed
by failed durability verification is uncertain, not proof of rollback. Old
CLIs/editors ignoring the lock remain a concurrent-write risk.

Export creates a new private file exclusively; an existing path remains intact.
Restore accepts only this config's unchanged private backup family and requires
the selected preset document. Snapshot → backup read → replace plan → revision
check restores only that companion, preserving current credentials, provider
settings, aliases and other presets. Missing documents are errors, never an empty
replacement or permission to restore the whole config.

## Transfer, envelopes and scheduling

Proposed additive capability `favorites_transfer` must advertise implemented
provider/codec coverage and limits. Preserve legacy contracts. Require read-only
relay/player compatibility before mutation; unsupported negotiation has no legacy
mutation fallback. Exact RPC activation awaits the player/controller wire freeze.

A complete scoped snapshot returns scope, revision, binding revision, byte count,
SHA256 and base64 of original document bytes. One bounded response replaces the
obsolete entry-page design; its final result must fit 2 MiB. Hash original bytes,
not normalized/re-serialized JSON. The local multi-scope bundle also has its own
whole-bundle 1 MiB limit.

Planned operations are `snapshot`, `begin`, `append`, `preview`, `commit`, `status`
and `abort`. Begin records operation UUID, runtime, scope, base/binding revisions,
document hash/size and actual chunk count. Append uses contiguous sequence/offset,
chunk hash and strict canonical base64. Identical duplicates acknowledge the same
bytes; changed duplicates conflict. Begin/append/preview never write favorites.
Preview binds the document, physical coverage, intended changes and revisions.

Chunks hold at most 8 KiB raw bytes: 1 MiB needs 128 full chunks, with at most 256
chunks allowed when a smaller advertised cap is used. Byte boundaries may split
UTF-8; validate the complete reassembled document. Both actual client bodies and
conservative queued envelopes must fit 16 KiB including metadata and escaping.
Pack everything before staging; reject overflow rather than send a prefix.

Python RPC bodies contain action, params, then optional `idempotency_key`, with
UTF-8 and default JSON spaces. Go removes the key and adds only `id` and
`expires_at` to the RPC queue, **not `ts`**. Go compacts JSON and escapes HTML/line
separators. Bound generated fields conservatively; placeholders are not actual
server values. The outer key never reaches the player; recovery needs its explicit
operation UUID in params.

Pace starts at least 1.5 seconds apart per device using actual prior start times
and one whole deadline. Account for POSTs, receipt GETs/polls, backpressure and
final-receipt reserve. Existing limits remain: 120 device/240 controller HTTP
requests per minute; 50 results retained for 60 seconds. GET does not free a slot.
Do not reset timeout per chunk. A full document plus five control commands needs
at least 199.5 seconds of pacing before reserve/latency: reject a 45-second budget
before begin. Explicit 300-second mode is not a guarantee under shared traffic.

One RAM stage/device has idle TTL 120 seconds and absolute lifetime 600 seconds.
Only valid accepted activity refreshes idle TTL. Create a target stage just before
servicing it, not while other targets wait. Runtime change/restart invalidates
staging. Abort releases staging; it cannot undo a commit or prove no write occurred.

## Persistence and recovery

Commit rechecks runtime, policy, pairing, binding revision and actual persisted
scope state. Preview enumerates physical documents and persistence class. Use a
real supported adapter batch only for that coverage; otherwise guarded idempotent
per-store SETs require individual verification. Reads must come from persisted
storage. Update caches only while the operation still owns them: stale callbacks
cannot publish success. Never replace a mixed journal with an old favorites-only
snapshot or lose concurrent unrelated history/progress during writes/recovery.

Report covered stores and aggregate as `verified`, `not_applied`, `partial` or
`unknown`. Verified needs every covered document and receipt to match intended
readback. Not-applied requires positive no-write/unchanged-state evidence. Partial
means a known subset changed; unknown means missing, inconsistent or superseded
evidence. Missing receipt proves neither not-applied nor safe replay. There is no
cross-store, cross-tab, fsync, all-scope or all-player atomicity promise.

Cookie adapters follow actual per-store behavior/limits, not a blanket prohibition.
RAM-only adapters need explicit session-only intent/labeling, never a persistent
copy/durable receipt claim. Reject requested persistence before writes if absent.
After lost ACK retain operation/request/plan IDs, runtime, scope and revisions;
read controller receipt/player status and persisted state, never blind-replay.
Matching current contents does not establish which operation produced them.
Restart, expired receipt or unchanged snapshot may leave pending effects unknown.
Reconciliation must establish a new plan before any subsequent write.

## Named-preset integration and release gates

Ordinary load takes settings and a present companion from the same config snapshot.
Preflight schema, coverage, bindings, helper availability and read-only compatibility
before provider writes; create staging only after setup. Include present companions
in checkpoint identity. Absent companions retain exact legacy normalized settings,
digest, RPC sequence and helper requirements. Pure name resolution cannot recurse
through full validation.

Favorites must not inherit generic settings `safe_after` expiry/retry. Keep an
uncertain commit pending for read-only reconciliation even after timeout. Report
completed provider steps if favorites later fails; never claim whole-preset rollback.
Numbered Local import/export remains separate. A server update alone does not
install CLI modules: package/install the complete reviewed helper set atomically,
preserving private config, before advertising these commands as usable.

Acceptance requires real M3U/Plex codecs/pairing, complete snapshots, conservative
same-scope/order/provenance conflicts, unchanged `shuffleBefore`, bounded multibyte
packing, whole-deadline refusal, concurrent history/cache ownership, per-write and
readback failure outcomes, and lost-ACK/restart recovery without mutation replay.
Fixture-only storage/transport success does not complete these player guarantees.
