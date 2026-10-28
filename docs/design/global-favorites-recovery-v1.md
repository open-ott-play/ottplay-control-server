# Global favorites recovery v1 — source proposal

This is an additive design, not an activated route, permission, capability or backend qualification. The eight `favorites_global` operations and existing scoped favorites/recovery remain unchanged. The [schema](global-favorites-recovery-v1.schema.json) defines exact public shapes; the [fixtures](global-favorites-recovery-v1.fixtures.json) contain synthetic digest and pagination examples. Schema validation is necessary but cannot establish the relational, physical-lock or persistence claims below.

## Separate action and fixed authority

Use action and sibling marker `favorites_global_recovery`; the marker is exactly `{ "version": 1 }`. Requests use exactly `inspect` or `acknowledge`, and every request, response and projected record has literal `subject: "global"`. There is no provider/profile/source UUID, client-selected storage key, path, codec or lock name. The original operation still has its real UUIDv4 and `operation_runtime`.

Proposed exact permissions are `favorites_global_recovery.inspect` and `favorites_global_recovery.acknowledge`. Neither inherits from ordinary favorites, scoped recovery or the core eight operations. A new writer should require a successful authenticated read-only global recovery inspection from the same controller/target within its whole deadline, not just a marker that an older controller might forward. A missing route, permission or marker fails before beginning a new mutation. Status-only evidence reconciliation remains available without sending acknowledgement.

The physical receipt family is the previously proposed separate `favorites-global-receipts.v1`, envelope `{version:1,entries:[...]}`, containing only fenced global records. Its version 1 is a new family, not the legacy unfenced scoped format 1. Membership remains in `remoteFavorites.v1`; recovery creates no additional favorites list and reads/writes neither the scoped ledger nor its UUID bindings. Every writer of the global membership store participates in the same actual `ottplay:favorites-ledger` lock and global protected-outcome admission rule.

All inspection is global: inactive, removed, unconfigured or unavailable media sources do not hide their operation records. No player profile is activated. A known malformed/unsupported global ledger is refused completely, never filtered into an apparently valid partial list. Unqualified backends may return valid read-only evidence with `backend_proof:null`; they must never offer an eligible ACK plan or advertise full guarded write readiness.

## Bounded inspection and exact cursors

Keep the existing actual queued-request envelope limit of 16 KiB and raw metadata-response limit of 128 KiB. The private global ledger retains its independent 32-record/64-KiB limit. Inspect returns only original `pending`, `partial` and `unknown` records, including those with acknowledgement audit. Terminal successful records need not appear in this recovery projection, but remain covered by the whole-ledger revision.

A collection starts with exactly `{op:"inspect",runtime,subject:"global",cursor:null}`. Return at most 16 rows, sorted strictly by operation UUID, `total` in 0..32, and either a null `next_cursor` or an exact continuation containing the last operation ID and the same `ledger_revision`, `store_revision`, `context_revision`, and `backend_proof`. Every nonfinal page has exactly 16 rows. Continuations must identify a real page boundary in the current projection; callers cannot request arbitrary offsets or skip ranges.

Under the same physical guard, each continuation rechecks the request runtime and all cursor revisions/proof before reading. A changed ledger, membership store, authority context or backend invalidates the entire collection; return revision/context/runtime error instead of mixing snapshots. No server-side cursor cache, admission reservation or persistent snapshot is created. A client reports a complete collection only after reaching null continuation, receiving exactly `total` unique sorted rows, and validating unchanged metadata and every boundary. On any failure it retains diagnostic evidence but refuses to present a partial collection as complete. A deliberate new read-only collection may start from null; a mutation is never retried by this mechanism.

A selected operation is inspected with `{op:"inspect",runtime,subject:"global",operation_id}`. It returns its record or null and, only when eligible, a deterministic plan. Page records never authorize an ACK: the operator must inspect the selected operation freshly before confirmation.

The record contains only operation ID, original operation runtime, fixed subject, original state/outcome, original-entry hash and optional fixed audit. There are no titles, documents, locators, URLs, credentials, arbitrary error strings or private storage names. State/outcome pairs are exactly pending/unknown, unknown/unknown and partial/partial.

## Physical revisions and absent storage

`ledger_revision` is SHA-256 of the exact whole persisted private-ledger UTF-8 serialization used by CAS/readback. `store_revision` is SHA-256 of the exact physical membership-store preimage, not its public document, export, cache, binding revision or a reserialized projection. The same physical string-to-UTF-8 rule must be used by inspect, conditional write and readback. If a store uses a physical wrapper/compression, hash that complete physical string; do not silently hash its decoded document instead.

An absent physical value uses SHA-256 of UTF-8 `null` for hashing only:

`74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`

Absence initializes nothing. Missing ledger returns logical `ledger_format:1`, zero rows and null selected record/plan. `store_revision` may legitimately be the absent sentinel for an operation interrupted before its first membership write. A protected record cannot have an absent before-ledger; neither its prospective after-ledger nor successful ACK can be absent or equal to its before-ledger.

`context_revision` binds the globally readable recovery authority/codec context. It must not depend on having an active/current Plex source or require resolving deleted profiles. The facade owner must freeze its exact input/hash domain before activation. This field is checked in page cursors, selected plans and every ACK, in addition to actual captured storage/lock/function identity and current policy. It is not a substitute for those live checks.

`backend_proof` uses a distinct global domain committing to actual origin, the fixed global receipt and membership keys, the same lock, synchronous-localStorage backend, global family/codec version and guard implementation. It must not reuse scoped proof bytes, fabricate a key or imply native qualification. Exact global proof serialization and production private-row serializer remain facade-owner freeze items. Missing/changed qualification makes ACK ineligible.

## Deterministic confirmation plan

Let `T` be the current authenticated response runtime, `O` the selected operation ID, `U` its immutable original operation runtime, `E` its original-entry SHA, `L` the whole before-ledger revision, `S` the whole membership-store revision, `C` recovery context revision, `B` qualified global backend proof, and `R` literal `accept-unknown-no-replay`.

`E` hashes the frozen canonical private original row representation excluding only its recovery audit. It includes original state/outcome and original operation evidence. The implementation must never reconstruct it from the public projection. Arrays below use compact UTF-8 JSON, the exact shown order, no BOM/newline or locale transformation:

```text
ACK_ID = SHA256(JSON([
  "ottplay-favorites-global-recovery-ack-v1", T, "global", O, U, E, L, S, C, B, R
]))
```

Construct only this audit on the selected row:

```text
{acknowledgement_id:ACK_ID, runtime:T, ledger_revision_before:L,
 store_revision:S, context_revision:C, backend_proof:B, reason:R}
```

Serialize the complete prospective ledger with the frozen private serializer and compute `F = SHA256(exact_after_ledger_utf8)`. Preserve every original field and every other row. Do not update unrelated timestamps, revisions, order or expiry. No caller time, free text, random value or UUID is accepted. Then:

```text
PLAN_SHA256 = SHA256(JSON([
  "ottplay-favorites-global-recovery-plan-v1", T, "global", O, U, E, L, S, C, B, R,
  ACK_ID, F
]))
```

The persisted audit contains neither `F` nor `PLAN_SHA256`, avoiding a circular hash. Selected inspect returns ACK_ID/F/PLAN only for a valid unacknowledged fenced protected record with qualified backend, current policy and sufficient reserved capacity. Otherwise `acknowledge_allowed:false` requires all three null, including absent/already-acknowledged cases. The original row remains visible even when acknowledgement is unavailable.

The CLI displays the selected operation, unchanged original uncertainty and fixed acknowledgement reason. Explicit confirmation authorizes at most one `acknowledge` POST containing the exact inspected tuple, `accept_unknown:true` and reason. Preview, page listing, status, timeout, expiry and another operation never imply confirmation. The controller retains owner/device/action/op/runtime correlation and idempotency rules; it must not treat this result as generic mutation `verified` evidence.

## Lock, CAS, audit and crash boundaries

The actual lock covers every participating global physical apply from before reservation/body through settlement of its underlying Promise. An outer RPC timeout/cancellation is not physical settlement. Acknowledge acquires the identical exclusive lock with ifAvailable behavior; contention returns busy without waiting, stealing, cancelling another writer or making any write. Waiting 300 seconds or a changed page runtime alone never proves quiescence. No TTL or lease stands in for the backend lock. Backends unable to establish same-store Storage/Web Locks scope fail closed.

While holding the guard, recheck current authenticated runtime/deadline, permissions/policy, actual backend and captured function/owner identities, fenced provenance, original row/digest/runtime, absence of prior audit, all three revisions, capacity, and the deterministic plan/after-image. Any revision change before this point means zero writes. After all callback-based business/admission checks, perform direct captured physical reads for the final comparison immediately before CAS; no callback may intervene between that comparison and the ledger write. Use the physical helper's postwrite checks without claiming multi-key atomicity or protection against uncoordinated external writers.

Perform exactly one conditional ledger write; membership bytes are untouched. Read exact persisted ledger bytes back under the same guard and recheck the membership preimage/context. Only exact after-image bytes/hash and unchanged originals yield `acknowledged:true,persistence:"persistent"`. The response echoes current request runtime, operation ID/original runtime, ACK_ID, plan SHA, unchanged store/context revisions, exact after-ledger revision and projected record with the exact audit. It contains no `verified`, `not_applied` or rewritten original outcome.

After any possible write, failed readback, lost ownership, cancellation or ambiguous persistence is `outcome_unknown` (or a lost reply), never a claim of unchanged storage. Preserve the original evidence and require read-only reconciliation. This contract does not make two physical keys crash-atomic and does not turn localStorage acknowledgement into an fsync guarantee.

An acknowledged record ceases to block independently admitted NEW global operations only through the explicit global admission rule. It remains an original unknown/partial operation and is never replayable. Other protected records remain blocking. ACK does not delete rows, free record slots, extend TTL, perform migration or automatically reclaim unknown records. The full 32-record limit and audit retention remain honest; any future archival policy requires a separate explicit design.

## Capacity and recovery across runtimes

Reserve 1024 bytes inside the existing 64-KiB ledger limit for EACH fenced protected row without audit:

```text
exact_current_ledger_utf8_bytes + 1024 * unacknowledged_protected_rows <= 65536
```

The global serializer must prove its largest complete audit delta, including enclosing keys/delimiters, fits 1024 bytes before producing records. Admission and every subsequent ledger update preserve this invariant. ACK consumes only its selected reserve; no truncation, hidden eviction or extra limit is allowed. A legacy/prototype/nonconforming record is not retroactively qualified by the presence of today's guard.

On a lost/negative/invalid ACK response, save the immutable intent, request identity and uncertainty. Never automatically resend the acknowledgement or original transfer. Fresh read-only inspect may confirm an exact stored audit by recomputing its ACK_ID from the audit's original T/L/S/C/B/R and unchanged O/U/E. The observing runtime can differ, but it must not relabel the audit or revive the old plan. A later whole-ledger revision may differ because another row changed: report an exact audit now observed, not an invented verification of the complete historical after-image.

If no matching audit is found, uncertainty remains. A later explicit operator confirmation may authorize one new conditional attempt only after a fresh coherent selected inspection. Changed runtime/context/revisions require displaying and confirming a newly derived plan and retaining the previous attempt; they cannot be silently substituted into the saved request. A matching historical audit means no new ACK. Lock plus exact ledger CAS prevent two separately confirmed/queued attempts from both changing storage. Missing evidence never authorizes replay.

Read-only inspection can remain available in standby using the existing trusted management-asset rule; ACK remains a mutation subject to standby, parental/kiosk and permission gates. Runtime and expiry gates apply to both. Global recovery must also remain usable after an unrelated provider change or deletion of a formerly referenced profile/source.

## Acceptance before activation

1. Exact schema/unknown-field, UUID/runtime/hash, UTF-8, duplicate-key, surrogate, finite-number and request/response-byte tests. Literal true confirmation; no scoped UUID placeholders or arbitrary key/URL fields.
2. First/last/empty pages, 17- and 32-row boundaries, duplicate/out-of-order/skipped cursors, wrong totals, changed ledger/store/context/backend/runtime: complete collection or explicit failure, never partial success.
3. Absent ledger/store performs no initialization. Protected before/after ledger rejects the null sentinel; before/after equality and recomputed malicious plan tuples fail.
4. Synthetic private before/after digests reproduce ACK_ID/PLAN exactly. Wrong operation/original runtime, state, E/L/S/C/B, reason or after-image fails before write. Audit size/headroom is measured using the ACTUAL serializer, not merely these illustrative fixtures.
5. A real delayed writer holds the same lock after RPC timeout. ACK is busy with zero writes until actual settlement. Final-CAS callback mutation yields conflict/no overwrite; prewrite refusal is zero writes; postwrite failure remains unknown.
6. Successful ACK changes only one audit, leaves membership and all original/other records unchanged, uses exact persisted readback and never grants old-operation replay or generic verified evidence.
7. Lost ACK followed by same/fresh runtime readback recognizes the exact historical audit; missing/different audit stays uncertain. Another confirmation never causes an automatic retry, and racing confirmed attempts have one conditional write at most.
8. Existing scoped/core-eight fixtures and permissions remain exact. Rollback preserves the separate global ledger and must not enable older uncoordinated membership writers to bypass its protected-outcome fence.

The checked-in fixtures use a deliberately illustrative private row representation and synthetic context/backend hashes. They establish public structure and digest arithmetic only. The facade owner must still freeze production private serialization, context/proof domains and actual exclusion/readback qualification before either marker or route is enabled.
