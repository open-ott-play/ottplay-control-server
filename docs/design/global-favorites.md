# Portable global favorites

This revision supplies the CLI, controller protocol and local storage for global
Favorites. It requires a matching player implementation. The controller never
invents a player capability or migrates its data; the player must qualify its
physical store, cooperating writers and recovery before advertising support.

The public document is `{version:1,entries:[{title,origin,locator}]}`. Origin
contains provider/profile/subprofile/sourceAlias. These descriptors identify
candidates, not authority. Locators cover channel IDs, explicit channel names
with an optional exact group, Plex, Local relative paths, and VPortal IDs.
Channel-name resolution requires one exact match in a verified receiver context;
it is never a fuzzy or ordinal fallback. Missing or ambiguous entries remain data.

Identity is full canonical origin plus locator, excluding title. Order, case,
Unicode normalization and leading zeroes are preserved. Merge keeps existing
order/titles and appends new identities; conflicting titles yield
`can_commit:false`. Replace explicitly adopts incoming order/titles. `moved`
counts retained entries whose absolute position changed, including deletion
shifts. Preview results do not prove receiver availability or durable storage.

A merge title conflict blocks the whole plan. The CLI shows up to 64 conflicting
title pairs from its validated documents, with the total count, before starting
a transfer. Edit the incoming titles to match or deliberately replace the whole
list. The wire represents this as `blocker: "metadata_conflict"` with bounded
input indices on the affected preview rows; it does not transmit private source
resolution details or choose a title policy implicitly.

Each document is limited to 1000 entries and 1 MiB canonical UTF-8. Raw JSON has
the same byte ceiling and rejects duplicate keys, non-finite numbers and excess
nesting. Multi-document previews admit at most 64 documents, collectively 1000
entries/1 MiB. Oversized data is rejected without truncation. Text uses valid
Unicode scalars, UTF-16 unit limits and explicit ECMAScript edge whitespace.
Numeric version values equal to one normalize to integer1; booleans are invalid.
Canonical bytes use sorted keys, compact separators and UTF-8 without BOM/newline.

The new `preset_global_favorites` companion uses a version1 `presets` mapping.
It shares the existing config lock, full-byte CAS, private backup and atomic
replacement implementation. Plans are bound to their storage key; unrelated
configuration and the old `preset_favorites` companion are preserved. The
whole-config 4 MiB limit remains. There is no automatic format migration.
Older CLI versions retain the unknown key but do not transfer global favorites
with ordinary preset load. This revision refuses an ordinary settings load that
would silently omit either selected favorites companion. `--settings-only`
explicitly skips both; `fav global load` transfers the global document separately.

The separate `favorites_global` action has eight operations: describe, export,
begin, append, preview, commit, status and abort. The [wire schema](global-favorites-v1.schema.json)
and [fresh admission rules](global-favorites-admission.md) define their bounds.
The CLI validates a complete export, computes the expected result locally and
refreshes describe before uploading. The fresh one-use operation ID, whole-store
revision, context and negotiated kinds must match. A changed snapshot stops the
transfer before begin. Every preview page must describe the same immutable plan.

Every preview also includes `legacy_effects`: `linked_removed` counts old
memberships associated with global entries that the plan removes, and
`unmapped_retained` counts legacy memberships that cannot yet be represented
portably and remain untouched. Each count is bounded by the 100-entry legacy
store. They are separate from global document counts, identical on every page,
and bound by the plan digest together with the full private-store baseline.
Commit rechecks that baseline under the shared writer lock. A preview describes
planned effects, not completed cleanup; preserved unmapped rows are not silently
invented as global entries.

The separate [items interface](favorites-global-items-v1.md) reads the complete
global list and current portable item and dispatches playback by receiver UUID.
UUIDs are local selectors, not portable identities. Ordinary add/remove reuse
the same staged transfer above, after matching items and export revisions. This
keeps one mutation and recovery path. The legacy wire stays available explicitly
for unresolved old memberships.

The original command deadline covers discovery, every page/chunk, preview and
reconciliation. A necessary lower-bound budget rejects oversized transfers before
begin; it is not a latency guarantee. `-t 300` is available for larger documents.
The CLI does not silently raise the deadline or truncate the favorites list.

Before commit, the CLI retains exact begin/preview evidence in a private,
controller/device-bound `.favorites-global-operations-<digest>` directory next
to its configuration. The request is sent once. A lost reply triggers read-only
status reconciliation, including after restart, against that retained context.
Missing evidence remains unknown and never grants replay permission.

[Global recovery](global-favorites-recovery-v1.md) uses a separate action and
marker. Inspection reads bounded protected records; acknowledgement requires a
fresh explicit plan hash and attaches audit evidence without changing the
original outcome. It never repeats an import. The new physical receipt family
is separate from the scoped Plex receipt family; both cooperating physical
writers use the shared player lock. The existing scoped wire remains unchanged.

Controller permissions are explicit per operation, for example
`favorites_global.export`, `favorites_global.commit`,
`favorites_global_recovery.inspect` and `favorites_global_recovery.acknowledge`.
Existing ordinary or scoped favorites grants do not imply the new permissions.
Install all CLI Python modules from the same revision, update the controller,
and verify the player's advertised capability before using the commands.
