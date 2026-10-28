# Favorites recovery v1 — protocol and recovery contract

Wire version and the independent recovery capability are version 1. The private receipt ledger uses its separately validated fenced format 2. This contract defines recovery of administrative uncertainty; it never infers that the original favorites write succeeded or failed.

## Scope, permission and readiness

Action `favorites_transfer`; new op `recovery`; exact modes `list`, `inspect`, `acknowledge`; new exact permission `favorites_transfer.recovery` covers all three. No inheritance from existing transfer/provision/favorites permissions. Sibling capability: `favorites_transfer_recovery: {version: 1}`. Existing seven operations, prepare, provision and the original favorites_transfer capability shape are unchanged.

Before ANY new mutating transfer, including clear, the new CLI must validate a successful authenticated read-only recovery/list response from the same target/controller within its existing whole deadline. A capability marker alone is insufficient: the old controller forwards unknown root capability fields. Missing controller support, missing permission, rejected/invalid/uncertain probe, or missing player recovery marker fails BEFORE prepare, begin or any mutation. No mutation fallback. Existing status-only reconciliation remains reachable without this new-mutation probe.

The new player exposes full transfer capability only when the actual physical exclusion guard and recovery are qualified. Recovery list/inspect uses the GLOBAL ledger and does not depend on the active/current/existing Plex source or binding. The original row supplies scope. Legacy v1 rows, including rows whose source/profile was deleted, remain inspect-only. A currently supported guard does not establish that old records were produced under it. Do not relabel v1 rows as fenced v2.

## Exact wire rules

[wire schema](favorites-recovery-v1.schema.json) gives exact request/response shapes, without extra fields. Require strict UTF-8 JSON, duplicate-key/invalid-Unicode/nonfinite rejection, whole-token patterns and no coercion. Existing actual queued-envelope limit 16 KiB and raw metadata-response limit 128 KiB remain. The private ledger is bounded to 32 records and 64 KiB. List returns all original pending/partial/unknown rows, acknowledged or not, sorted by operation UUID; never truncate or silently skip malformed entries. No raw ledger, URLs, tokens, storage keys, private refs or current-provider guesses are returned.

Public record fields are operation_id, original scope, original_state, original_outcome, original_entry_sha256 and nullable acknowledgement audit. Pending maps to unknown, partial to partial, unknown to unknown. Acknowledgement preserves these fields byte-for-byte where applicable; it never becomes a verified/not_applied original mutation.

The only acknowledgement reason is the literal `accept-unknown-no-replay`. `accept_unknown` must separately be literal true in the mutating request. No caller free text, UUID, time or arbitrary audit metadata is accepted. An acknowledgement ID is now exactly 64 lowercase hexadecimal SHA-256 characters, NOT UUIDv4. The original operation ID and scope IDs retain their existing canonical UUIDv4 grammar.

## Digests and deterministic after-image

`original_entry_sha256` hashes the private canonical original row representation, excluding only recovery audit. `ledger_revision` hashes the exact whole-ledger UTF-8 serialization used by the conditional write and verified persisted-ledger read. This is not a public projection hash or a binding revision. Missing ledger uses UTF-8 `null` for hashing only, lists zero records and initializes nothing. The private serialization/physical codec must be frozen by the player owner so inspection, CAS and persisted readback use identical bytes. No reserialization of public records may substitute for the private original.

`backend_proof` is null without actual qualification; otherwise it is SHA-256 of compact UTF-8 JSON:

    ["ottplay-favorites-recovery-backend-v1", origin, privateLedgerKey,
     privateWebLockName, "synchronous-localstorage", 2, guardImplementationVersion]

Private values are never returned. This is a binding commitment, not external platform attestation. The player separately captures and rechecks actual Storage/WebLocks/owner functions and the frozen codec. Changing any guarded backend identity invalidates the plan.

Define S=[scope.profileBindingId,scope.sourceBindingId,scope.provider], E=original_entry_sha256, L=exact before-ledger revision, B=backend_proof, R="accept-unknown-no-replay". Compact JSON arrays have fixed order, no whitespace/newline and no locale-dependent transformation.

1. ACK_ID = SHA256(UTF8(JSON([
   "ottplay-favorites-recovery-ack-v1", runtime, operation_id, S, E, L, B, R
   ]))).
2. Purely construct the selected row's exact audit:
   {acknowledgement_id:ACK_ID, runtime:runtime, ledger_revision_before:L,
   backend_proof:B, reason:R}.
3. Use the frozen private serializer to append ONLY that audit to the chosen row, preserving every original row/outcome and all other ledger contents. Compute AFTER_SHA256 from these exact prospective whole-ledger bytes.
4. PLAN_SHA256 = SHA256(UTF8(JSON([
   "ottplay-favorites-recovery-plan-v1", runtime, operation_id, S, E, L, B, R,
   ACK_ID, AFTER_SHA256
   ]))).

IMPORTANT: the persisted audit contains neither PLAN_SHA256 nor AFTER_SHA256, so there is no circular hash. It also contains no variable timestamp, random value, or caller-supplied UUID. Actual observation/execution timestamps may be recorded by the existing controller/local journal outside this deterministic ledger after-image. The pure after-image builder may not secretly change unrelated timestamps, revisions or record order; any required private-format change must itself be deterministic and included before computing AFTER_SHA256.

Inspect returns ACK_ID, AFTER_SHA256 and PLAN_SHA256 only for an eligible unacknowledged fenced v2 row. All three fields are null when acknowledge_allowed=false. This includes missing rows, legacy rows, already acknowledged rows or unavailable guard/headroom. Inspect creates no persistent data, stage, admission or dry-run cache. All values are recomputable from a fresh locked read.

Acknowledge repeats scope, E, expected_ledger_revision=L, B, R, ACK_ID, AFTER_SHA256, PLAN_SHA256 and accept_unknown=true. It cannot choose a different after-image. Every derived value must match recomputation under the actual lock. Schema validation alone does not establish these relations. [wire fixtures](favorites-recovery-v1.fixtures.json) supplies an exact synthetic before/after serialization, ACK_ID, after digest and plan digest; its private row layout is illustrative, not the player v2 schema.

## Capacity reservation before protection is created

Reserve ACK_HEADROOM_BYTES=1024 for EACH newly produced fenced protected row with no acknowledgement. All ledger writes must preserve:

    exact_current_ledger_utf8_bytes + 1024 * unacknowledged_protected_v2_rows <= 65536

The exact public audit at maximum runtime/token lengths is 377 compact UTF-8 bytes; adding the illustrative private `recovery_ack` field and delimiter costs 393 bytes. The real private serializer must prove its ENTIRE maximum acknowledgement delta, including field/container delimiters, is <=1024; otherwise this contract must be revised before producing v2 records. The reserve is real capacity inside 64 KiB, not an additional allowance. No audit field is reserved by allocating persistent dummy bytes.

Admission/reservation and every subsequent ledger update enforce the invariant before writing. Acknowledge consumes the selected row's reserve while removing only that row from the unacknowledged-protected reserve count. Other rows retain theirs. This ensures a ledger produced according to the invariant has room to acknowledge any one protected row without deleting unknown evidence. A malformed/nonconforming full ledger is inspectable where safe but never repaired or truncated automatically. No new admission, acknowledgement or completion may silently exceed byte/count limits. Acknowledgement does not auto-evict, extend expiry, free a record slot, or expand the 32-record limit; acknowledged history remains. Capacity exhaustion remains explicit.

## Actual exclusion, CAS and outcome

Every actual physical apply promise holds the identical origin-scoped Web Lock until the underlying promise settles, even after an outer RPC timeout/cancellation. Recovery uses that SAME exclusive lock with ifAvailable=true. A held lock returns busy immediately; it does not wait, drop the old stage or perform writes. This is a cooperating-writer guarantee for verified synchronous-localStorage only. Noncooperating legacy tabs cannot be made safe by a wire field; fail closed if they cannot be excluded. Legacy v1 rows never authorize online acknowledgement.

Under the acquired lock, recheck authenticated dispatch expiry, current runtime, exact backend/functions/owner, fenced v2 provenance, no active physical writer, whole-ledger revision, selected original row/scope/digest, no prior audit, capacity, and all deterministic plan fields. Perform one conditional write of the already-hashed exact after-image. Read persisted bytes back while still holding the lock; only an exact digest AND byte/readback match yields acknowledged=true/persistence=persistent. Preserve original unknown/partial and every other protected row. Only this explicitly acknowledged row ceases to block future NEW independently admitted operations. It does not create a prepare grant or authorize replay of any original operation.

## Correlation and lost acknowledgement

Success echoes immutable request runtime/op/mode. Inspect echoes operation_id and any matching row. Acknowledge echoes operation_id, ACK_ID and PLAN_SHA256; returned ledger_revision must equal the requested AFTER_SHA256, and the returned row/audit must match the inspected original fields and exact derived audit. No generic outcome=verified is used.

Errors preserve the existing minimal `{version,runtime,op,error}` shape and authenticated outer-request correlation. Reuse busy, unsupported_persistence, revision_conflict, policy_denied, storage_unavailable and existing request/expiry/limit errors. Storage/readback failure after a possible write is uncertain, never unchanged/not_applied. No automatic acknowledgement retry, even with identical hashes. Only fresh read-only inspect/list follows a lost, negative or invalid ACK.

A fresh inspect can confirm the exact stored audit by recomputing ACK_ID from its stored runtime/L/B/R and the immutable selected original. The observing runtime can differ; do not relabel the audit's original runtime or allow an old plan to mutate the new runtime. The caller compares its retained ACK_ID/plan context. If the whole ledger has since changed, its current digest need not equal the old AFTER_SHA256: report the exact audit now observed, not a claim that the complete historical after-image was verified at that past instant. Missing/different audit is unresolved and never permits automatic retry. Original mutation outcome remains unknown/partial in every case.

A new explicit operator confirmation may authorize one further conditional acknowledgement attempt after a fresh coherent inspect still shows the exact unacknowledged protected row and identical complete plan. The CLI reuses only a strictly identical durable logical intent; it does not discard the first attempt or alter the original transfer evidence. An already matching stored audit is reported as observed with no new acknowledgement request. A changed, busy or mismatched plan causes no acknowledgement. Within one invocation there is never an automatic resend. The shared physical lock and exact before-ledger CAS ensure an older queued request and a newly confirmed attempt cannot both change storage.

## Deployment and rollback

A new controller/parser/result descriptor/permission, CLI explicit confirmation flow, and player guarded v2 ledger are required. New recovery grants are configured explicitly only after deployment. Old beta27 refuses recovery before enqueue. Original seven ops/prepare/provision remain unchanged. Controller rollback disables the new operation without erasing evidence. An old player may reject v2; preserve exact backups and fail closed, never blindly overwrite later ledger/favorites changes with a pre-acknowledgement backup.

The private fenced serializer must preserve the original record and audit independently. Every participating physical writer must hold the same actual Web Lock through settlement. The initial rollout must exclude pre-lock development runtimes; acquiring a new lock alone cannot stop an older writer that never participates in it. Existing deployed players that never advertised this transfer protocol do not create those prototype receipts. These requirements do not claim cross-store atomicity or support for an unavailable backend.

## Absent storage

An absent physical ledger is represented read-only as logical `ledger_format: 2`, `ledger_revision: SHA256(UTF8("null"))` and an empty record list. This neither initializes storage nor asserts that a fenced record exists. Inspecting a missing operation returns `record: null`, `acknowledge_allowed: false` and null acknowledgement/after-image/plan fields. See the [empty-ledger fixtures](favorites-recovery-v1-empty.fixtures.json).

## Web Locks reference

The callback returned promise determines lock lifetime. Its settlement must follow the underlying physical apply, including when an outer RPC stops waiting. Do not race that returned promise with a timeout or use lock stealing. Locks coordinate participating contexts in the same storage bucket; separate browser profiles have separate storage and locking. See [Web Locks: lock lifetime](https://w3c.github.io/web-locks/#locks) and [lock managers](https://w3c.github.io/web-locks/#lock-managers).
