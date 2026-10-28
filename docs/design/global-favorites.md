# Portable global favorites foundation

This change supplies data validation and local storage. It does not activate
remote commands, capabilities, player migrations, or playback.

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
with ordinary preset load. Updated command integration must handle both
companions explicitly and retain clear `--settings-only` semantics.

Before remote activation, the separate `favorites_global` action and `fav global`
commands require negotiated locator kinds, bounded staged transfer, immutable
previews, receiver-context and whole-store revisions, a compatible legacy
facade, shared writer guards, and durable uncertain-outcome recovery without
replay. None of those runtime guarantees follows from this foundation alone.
