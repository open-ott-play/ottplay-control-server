# Global favorites items v1

Status: accepted by Kiosk on 2026-10-28; source contract only. No route, capability, permission, installer or player is activated by these files. Existing `favorites_global` core8, `favorites_global_recovery`, and legacy `favorites` v1 remain unchanged.

## Action and exact shapes

The separate action and sibling capability are both `favorites_global_items`; the capability is exactly `{ "version": 1 }`. Request params use `op`, `runtime`, and fixed `subject: "global"`. Results use common `version: 1`, the requested `runtime`, `op`, and `subject: "global"`. The sibling schema defines exact objects with no unknown keys; fixtures give complete examples. The unchanged controller request/result envelope supplies the request ID and status. Successful data use status `ok`; the five-field common-plus-error data use `rejected`.

Advertising items version 1 requires support for all five portable locator kinds,
including `channel-name`. A partial implementation must withhold this marker.
The separate core8 `describe.locator_kinds` negotiation still applies to writes;
items support never implies that a transfer kind or permission is available.

- `list`: params add `cursor: null | opaque_string` and `max_entries: 1..100`. Result adds `revision`, `context_revision`, echoed `cursor`, `page_offset`, `total`, `entries: [{id, entry}]`, `next_cursor`, and `unresolved_legacy_count`.
- `current`: no extra params. Result adds `revision`, `context_revision`, `entry`, and `unavailable_reason`. A captured portable entry requires null reason. Null entry requires exactly one reason: `no_portable_current_item`, `standby`, or `unsupported_provider`. Use the general first reason unless the more specific condition is actually established. There is no invented UUID for an item which has not been saved.
- `play`: params add receiver-local UUIDv4 `id`, `expected_revision`, and `expected_context_revision`. Result adds the same `id`, `revision` equal to the admitted expected revision, `context_revision` equal to the admitted expected context revision, and literal `dispatched: true`. This reports dispatch acceptance, not visible playback, decoding, progress, or durable membership mutation. There is no `verified` flag or transferable receipt.

All IDs are lower-case UUIDv4, hashes are 64 lower-case hex characters, and runtime uses the existing lower-case ASCII grammar with maximum 64 characters. JSON booleans are never accepted as numbers. Match all patterns against the complete string (a trailing newline is not accepted). Reject invalid UTF-8, duplicate JSON keys, lone UTF-16 surrogates, non-finite numbers, malformed fields and extra keys before action dispatch. Strict transport/body validation is additional to JSON Schema.

## Portable entry and privacy

`entry` is the exact existing global portable v1 codec value (including the embedded M3U/VPortal pairing): `{title, origin, locator}`. Reuse that codec; do not create a second identity definition. Its normative text lengths count UTF-16 units, require well-formed Unicode, use the explicit JavaScript trim set, exclude C0/DEL and `://`, and preserve exact text without Unicode normalization or case folding. Schema `maxLength` is only the structural upper bound; it does not replace these rules.

Provider/locator rules remain exact: Plex uses `plex-media`, Local uses `local-media`, VPortal uses `vportal-media`, other supported channel providers use `channel` or `channel-name`. The sole additional pairing is `m3u` plus `vportal-media` for a configured embedded VPortal: preserve the outer M3U origin and actual folder subprofile (`null` at the root), and require receiver-owned portal/folder provenance. Reject derived `channel:<digits>` identities. Local paths are relative and exclude absolute/drive prefixes, backslashes and empty/dot/dot-dot segments. Do not return resolved URLs, credentials, signed query strings, private selectors, absolute native roots, profile slot numbers as identity, or backend storage bytes. Canonical identity remains origin plus locator, excluding title.

Provider keys follow the [portable origin rules](global-favorites.md): exact lowercase ASCII registry IDs, at most 64 characters, with digit-led segments and safe dots, underscores, hyphens and slash separators. Preserve punctuation; no alias normalization, URL decoding, path resolution or dynamic plugin loading is implied. Unknown safe keys require receiver resolution and grant no authority.

A receiver UUID identifies one local stored row and stays stable while that row exists. It is not portable identity, is not retained by an import solely because another receiver used it, and cannot authorize access to another target/runtime. Each full list has unique UUIDs and unique portable identities.

## Complete bounded pagination

Maximum total is 1000 global rows, in stored order. Each original raw result **data object** (including an error) is at most 65536 UTF-8 bytes; measure before parsing/reserialization so whitespace and escaping cannot bypass the bound. Existing 16 KiB request-envelope and 2 MiB outer-response limits remain unchanged. A page contains at most the requested count and at most 100 rows. The server reduces the returned count to fit the raw-body limit. If remaining data cannot yield even one complete row, return `limits_exceeded`; never truncate a row.

First request uses null cursor and receives offset zero. An opaque cursor contains no authority, URL or storage key. The server binds it to the same runtime, whole membership revision, context revision and next offset; clients must not manufacture or decode it. A continuation revalidates both revisions and returns the next contiguous page. Cursor grammar is nonempty ASCII base64url characters, maximum 512; its implementation encoding is not a public contract. Unknown/malformed cursor returns `invalid_cursor`; changed membership returns `revision_conflict`; changed context returns `context_changed`. A runtime change returns `runtime_changed`.

The returned `cursor` must exactly echo the request. `next_cursor` is non-null iff `page_offset + len(entries) < total`; it must differ from the request and all previously seen cursors. Every non-final page advances by at least one row. Empty entries are valid only for the initial empty list (`offset=total=0`, both cursors null). Totals, revisions and unresolved count stay constant across all pages. The client validates offsets, uniqueness and all relations, discards raw pages after validation, and exposes a complete list only after exact `total` rows and final null cursor. The assembled portable document must pass the existing <=1 MiB/1000-row codec bound. No partial list may silently drive a name match, deletion or playback decision.

Request pacing uses the existing >=1.5 second interval and a single caller deadline, with no per-page reset. At 100 rows/page a typical 1000-row list uses ten calls; a fixed 25-row page would need forty calls and >=58.5 seconds of intervals before other work. Large entries can require more pages and may exhaust the deadline; fail clearly, without reducing the requested scope or extending the deadline automatically. At most 1000 nonempty pages are possible by the strict progress rule.

`unresolved_legacy_count` exposes that legacy-only favorites remain outside this global list. They are never silently inserted with guessed portable identities or silently counted as portable rows. The CLI must label a nonzero count and retain an explicit legacy access path. This is not proof that legacy membership was migrated or removed.

## Current and playback guards

`list` and `current` read a coherent membership snapshot and `captureContext()` result, rechecking both before reply. They do not migrate, initialize storage, pair sources, qualify via write probes, start scans, fetch provider catalogs, open dialogs, wake standby, or write operation receipts. `current` uses already-observed current item/source data and the existing read-only capture helper; recheck its capture guard after async hashing and serialization. Unavailable is not an empty favorite to append. A policy/storage failure is an error, not a fabricated null capture.

`play` validates both expected revisions and exactly one ID before invoking the normal global router. The portable value comes exclusively from that stored row, never from caller-supplied locator/text/slot/path. Preserve current runtime, cancellation, expiry, target-owner and policy checks at every existing handoff. A legitimate owned source transition is checked through the existing handoff owner; do not require the entire old active context to remain byte-identical after the router itself changes it, and do not accept unrelated concurrent changes. Returned revisions identify admission, not a new post-play snapshot.

This action has no favorite add/remove/replace/acknowledge operation. `play` has normal authorized playback effects, but may not rewrite global membership or manufacture mutation/acknowledgement evidence. Existing router-side mapping persistence must be explicitly reviewed rather than assumed to be a read-only effect. No automatic retry of play is allowed after rejection, timeout or lost reply; an uncertain result remains uncertain. A caller must issue a fresh explicit action if it wants to try again. No late dispatch is permitted after cancellation/expiry.

## Permissions and error codes

Add only `favorites_global_items.list`, `.current`, and `.play` to explicit controller permission admission. Do not infer these permissions from old `favorites.*`, core8, recovery, generic reads or other targets. Preserve normal target ownership/result access and request-ID correlation. Result descriptors retain the operation, runtime, list cursor/requested maximum, or play ID/expected hashes as applicable. Client-side multi-page validation remains necessary; the stateless controller does not pretend to validate a complete list from one page.

`list` and `current` use read permissions and existing read privacy policy; standby alone does not prohibit reading the list. `current` can report standby without waking. `play` retains standby, parental, kiosk, provider and normal playback restrictions. Invalid or unauthorized params never reach router/capture callbacks. Capability discovery is read-only and is not proof of authorization or readiness for any individual action.

Errors are exactly: `invalid_request`, `envelope_too_large`, `unsupported`, `runtime_changed`, `expired`, `cancelled`, `policy_denied`, `busy`, `storage_unavailable`, `unsupported_persistence`, `limits_exceeded`, `invalid_cursor`, `revision_conflict`, `context_changed`, `favorite_not_found`, `source_unavailable`, `ambiguous_source`, `item_unavailable`, `ambiguous_item`, `unsupported_item`, `playback_failed`. Emit only a code established by the implementation; do not invent source-vs-item distinctions from the existing router's generic missing/ambiguous status. Unclassified router failure maps to `playback_failed`, unexpected storage/capture exception to `storage_unavailable`. No raw exception text is public. Transport uncertainty is not converted into proof of `playback_failed` or non-dispatch.

## CLI integration and feasibility

A successful new `list` or `current` request is the read-only proof of controller support. The marker alone proves only player advertisement. Ordinary `fav list` and `fav play` route to this action when advertised; on new-route failure there is no fallback to legacy mutation/playback. With no marker, existing legacy behavior stays available and must be identified as legacy. Resolve names only against a complete pinned global list; require a unique match, then send its ID plus pinned revisions. Explicit IDs still require a pinned read, not an unbound play.

`fav add` can obtain `current`, export a complete document through core8, combine by canonical portable identity, and use existing core8 preflight/admission/preview/commit. `fav remove` selects a listed row and removes that portable identity from a fresh complete core8 export with matching membership/context revisions before existing core8 replace. No direct store write and no replay are added. Preserve existing ambiguity, title-conflict, recovery readiness and replace semantics. A current capture describes the item at capture time; it does not freeze future playback. A context change between these reads requires a fresh explicit preflight, never blind reuse.

Candidate reuse is concrete: `global-favorites-facade.ts:list` already pages global rows and `play` selects an exact stored ID; `global-favorites-host.ts:captureContext` produces the revision/current guard, and its private `capture()` calls `captureGlobalFavorite`. New work is a read-only capture export, the strict dispatcher/validators, context-bound opaque cursor, and expected-revision/ID guards around router invocation. Do not use `facade.add`, `toggle`, `qualify`, or `pair` to implement `current`. Frozen core8 and recovery stay separate.

## Focused acceptance cases

1. 0, 1, 100, 101 and 1000 rows; adapted pages with long valid entries; exact 65536-byte boundary and +1 raw byte; full totals/order/UUID/identity uniqueness.
2. Null/unknown/repeated/skipped/cross-runtime cursor; membership or context changes on page two; deadline exhausted after a valid prefix yields no complete result or play.
3. All five portable locator kinds, Unicode V3 boundaries, invalid local path, derived channel ID, malformed/extra fields and invalid UTF-8; no secrets in public errors.
4. Current found and each established unavailable reason; generation/source changes during capture; zero persistent writes, pairing, fetches, dialogs and wake actions.
5. Play stale revision/context/ID, unsupported/ambiguous target, policy change, cancellation and late resolve; exactly one dispatch on success, zero late dispatch, no false playback confirmation.
6. New player/old controller rejects the action after read proof without legacy fallback; absent marker keeps explicitly labeled legacy behavior; global-only imported row can be listed and played without a legacy alias.
7. Nonzero legacy-only count is visible; add/remove use core8 only; replace of a full 1000-row set with one new row remains a separate store regression, not fixed by this API.
