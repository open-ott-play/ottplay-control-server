# Managed Plex server selection v1

**FROZEN INTERFACE v1 — approved for the next managed Plex delivery. Implementation, capability advertisement and device qualification remain pending.**

Basis: native `7eb151cd7ffd74f4a5d9da875086aa79e1d2467e`; `ROW-PROJECTION-v1.md` SHA256 `fccfb5e03925e205ac5eb87e7373b72781a5fe8f70f1d6c07dd8f6154b54e5e2`; `source-selection-map.md` SHA256 `9311c09afb50db07ada2c23364fe9391e3917ba764897eba6cf2365c7b301357`. Existing controller/CLI `ea767aeaad852aff77a3768625fddee39fb6907f` has no server-row selection operation. Existing `plex server URL`, `profile N`, provider selection, library and queue protocols retain their current semantics.

## Scope and capability

New action `plex_servers`; first slice exposes only previously verified, retained managed candidates. A row does not guarantee that its network proof is currently usable; explicit selection re-verifies it when necessary. It does not enumerate arbitrary network servers or make ordinary Saved profiles temporary. The current owner retains one candidate; the agreed first-slice bound is one row, with no pagination:

```json
{"plex_servers":{"version":1,"operations":["list","select"],"max_servers":1}}
```

Closed keys, integer version/max_servers, complete unique operation set (order immaterial). Advertise only after both adapters are implemented and qualified. A missing/malformed capability stops the CLI before sending this action; no fallback to `provider`, `profile`, `provider_settings`, `plex_queue` or a name lookup. Proposed CLI spellings, not existing support:

```text
ott PLAYER plex servers
ott PLAYER plex servers select SERVER_ID --context CONTEXT
```

The CLI obtains fresh capabilities/runtime for every command; discovery and the action share one timeout. Context-bearing selection requires one target; never fan out one receiver's context to other players. Selection is submitted once, with no automatic retry.

Controller operator scopes are explicitly `plex_servers.list` and `plex_servers.select`. Existing profile, library and queue permissions do not implicitly grant these operations. Retained replies remain bound to the requesting operator and exact operation.

## Bounds and validation

- Requests use the existing authenticated `/api/requests` envelope. Keep the current **16 KiB whole request** limit; this proposal does not raise it. The complete player reply envelope, including its escaped JSON, is at most **64 KiB**. Enforce raw byte bounds and duplicate-key rejection before projection.
- `runtime`: the current remote convention `[a-z0-9-]{1,64}` and exact equality with the requested/discovered runtime. `server_id`: verified Plex machine ID, `[A-Za-z0-9][A-Za-z0-9._-]{0,255}`, treated as opaque; never a registry slot or display name.
- `context`: opaque, unpredictable receiver-local ASCII `[A-Za-z0-9_.:-]{1,256}`, with at least 128 bits of entropy or equivalent unforgeable binding. It is not a portable credential and is insufficient without authenticated command authority.
- `name`: nonempty UTF-8 display text, at most 512 bytes, no C0/C1 controls. No authority, lookup, normalization or deduplication derives from this label. Different verified machines may share a label.
- All objects have exactly the documented keys. Unknown fields, nulls where not specified, incorrect types, unsupported versions and oversized values fail closed. Booleans are not numbers.

## List: no activation or persistence

```json
{"action":"plex_servers","params":{"version":1,"runtime":"native-qa-1","op":"list"}}
```

Successful reply data:

```json
{"version":1,"runtime":"native-qa-1","op":"list","state":"ready","servers":[{"server_id":"PMS-1","name":"Family Plex","context":"row-opaque-example","expires_in_ms":120000}]}
```

`servers` is an array of zero or one previously verified machine IDs. Zero means no eligible retained candidate, not a failed inspection. A failed or inconsistent read returns an error. Listing works while Local owns playback. It performs no verification/network fetch, server registration, token activation, provider/profile/category switch, managed owner command, source grant update, Saved write or playback cancellation. Allocating bounded ephemeral row context is its only new bookkeeping.

Reuse the same current, unexpired context when nothing relevant changes. Its agreed absolute lifetime is 120 seconds from creation; listing does not extend it. `expires_in_ms` is the remaining integer context lifetime, 1..120000. Expiry uses a monotonic clock, not client time. The configured candidate is not a 120-second lease: it may remain available while Local plays for longer. A later explicit list may replace an expired context without I/O; the old context remains invalid. Runtime restart, candidate replacement, owner/credential/disk identity change, authorization change or successful admission invalidates the context immediately.

Native `ImportProof::current()` checks grant scope and consent generation (`grant.rs:332–347` at the pinned revision); it has no wall-clock expiry or remaining-seconds primitive. Context lifetime does not extend proof validity. Owner, configuration, credential, physical identity and authorization guards remain mandatory, and a current network proof is required before activation.

Listing may issue a context only for a candidate the managed owner still considers eligible. It must not resurrect a consumed, superseded or revoked candidate by assigning it a new context. Authorization/consent revocation, erase, credential/configuration replacement or physical identity change makes that retained descriptor ineligible; none can be repaired by list or by this selection action.

A network-only scope change invalidates previously issued contexts but may leave the exact previously verified descriptor eligible. A later list may issue a fresh context for that descriptor without I/O even when its network proof is stale. This exception requires demonstrably unchanged owner, authorization/consent, credential, configuration and physical identity; an ambiguous invalidation fails closed. Neither `!ImportProof::current()` nor a same-identity check alone proves network-only staleness: the classification must also prove unchanged consent generation and that only the network part of the grant scope changed. It does not make an unverified descriptor eligible. Explicit select, never list, may re-verify the same exact Origin, token and machine identity off-frame under the current network scope. It cannot substitute another candidate, refresh consent, or acquire new authority.

Deduplicate presentation by verified machine ID only after checking compatible authorization. An existing row/client for that machine with the same exact parsed Origin and token proof may be reused without changing its user label or ownership. The same machine ID with a different Origin or token is an authorization conflict: no candidate-backed selectable row and no replacement/retokening; return `authorization_conflict`. A stale Saved profile whose source happens to match is not proof. Secrets, URLs, token hashes, raw disk identities and private proof material stay in the native owner; none enter reply data or diagnostics.

## Select: one typed admission and temporary activation

```json
{"action":"plex_servers","params":{"version":1,"runtime":"native-qa-1","op":"select","server_id":"PMS-1","context":"row-opaque-example"}}
```

Resolve the exact retained context and machine ID before effects. Both the explicit typed UI row action and this remote action call the same app-owned select-server handler. The context binds the retained candidate generation, proof scope/currentness, original Session/managed-owner epoch and identity, runtime credential identity, physical disk identity and managed configuration identity. It does not resolve a newer candidate by machine ID. A fresh explicit landing intent is created for this admitted action and carried through the existing typed handoff pipeline.

Apply the UI source-replacement and Providers-PIN classifications explicitly, with equivalent remote restrictions. Recheck loaded/uncertain state, pinned/strict kiosk, parental policy, caller/runtime/deadline and the exact Origin/token collision at final owner/registry admission. If only the network proof is stale, selection may perform the bounded off-frame re-verification described above within the original deadline. Recheck every non-network authority binding and the exact candidate after that work; failure or supersession cannot mutate or cancel the prior Local session. Deferred completion must still belong to the original invocation, owner and landing intent; it cannot manufacture a replacement context or fresh authority to rescue a stale request.

At final admission, including after off-frame verification, the original context must still be unexpired and unconsumed. Successful admission atomically consumes it into the retained in-flight operation record. Completion validates that original operation, owner and deadline rather than requiring the consumed context to remain in the offered-context table. Context expiry after effects begin is not a pre-admission `expired`/no-change result; apply the uncertainty rules below.

On success, the temporary managed Plex runtime/Session is active at a new correlated App completion following accepted managed credential/runtime-profile publication. The existing source seam is `SessionEvent::Commit(CommitReply)` → `owner::apply_commit_reply`: it checks request/epoch/arrival, accepts the `RegistryOnly` admission once, applies the runtime delta and emits publication effects. Merely entering this seam or queuing its effects is not success. A true return from `apply_commit_reply` is not an activation receipt: its rejected-admission branch also returns true. The App completion must correlate the exact managed operation, request, epoch, arrival and original invocation/owner identity, require accepted `RegistryOnly` admission, fire only after those effects are applied and the temporary managed credentials/runtime/profile is current, and settle at most once. A superseded completion cannot report success for a later session.

This completion is a required implementation addition. The existing `Ready`/`follow_auth_landing` path does not provide a correlated page-mounted receipt. `activated:true` therefore confirms managed runtime/profile publication, not that a page is mounted, a library loaded, or media decoded. This may replace current Local playback, but **does not write Saved.active_profile, persist the candidate/profile, write nativePlexSources or change the durable authorized-source allowlist**. Ordinary Saved-profile selection keeps its existing durable behavior. Browsing or generic provider selection never implies consent to this managed activation.

Successful reply data, only after that activation boundary is confirmed:

```json
{"version":1,"runtime":"native-qa-1","op":"select","state":"ready","server_id":"PMS-1","context":"row-opaque-example","activated":true,"persisted":false,"playback_confirmed":false}
```

Echo the exact requested server/context. All three booleans have these fixed values. This is an activation receipt, not merely queued/accepted and not evidence of decoded video. It makes no promise of playlist or media playback; media admission remains separate. No earlier `ready` reply is allowed. A consumed/stale context cannot start a second handoff.

## Errors and uncertainty

Standard outer status is `rejected` for a known error, or `unsupported` for an unavailable action; `ok` requires a ready success. Retain the standard request-ID correlation. For requests with unambiguously parsed, valid runtime and operation, error data has only `version`, `runtime`, `op`, `state:"error"`, `error`. Echo that original request runtime/op, including stale-runtime failures; do not substitute the player's newer runtime:

```json
{"version":1,"runtime":"native-qa-1","op":"select","state":"error","error":"source_changed"}
```

Missing, invalid or ambiguous runtime/op cannot produce this closed-schema typed error. Reject such malformed input through the existing outer validation/rejection path, retaining any valid outer request-ID correlation and omitting `plex_servers` typed data. Never invent a runtime, default an operation, echo an invalid operation into the typed schema, or dispatch malformed input merely to obtain a typed reply. A valid runtime/op with malformed remaining fields may receive the correlated typed `invalid_request` error when the existing validation path supports it.

Closed codes: `invalid_request`, `unsupported`, `busy`, `stale`, `source_changed`, `authorization_conflict`, `unavailable`, `restricted`, `expired`, `cancelled`, `limit_exceeded`, `activation_failed`, `outcome_unknown`. Do not return free-form private errors or empty-success substitutes.

Before admission, errors leave the prior Local playback identity, catalogue, profile, grants and physical Session intact. After effects begin, use `activation_failed` only if the exact prior state is known to be retained/restored; otherwise use `outcome_unknown`. A deadline/cancellation after the irreversible handoff boundary cannot claim that no change happened. A lost reply also leaves the client uncertain even if activation actually succeeded. Do not retry automatically. Existing read-only status/diagnostics can observe the current player, but are not a durable receipt for the timed-out selection.

## Implementation and qualification gates

1. The one-candidate bound and fixed 120-second context lifetime have coordination agreement, but are not existing implementation constants. Confirm the complete revised contract before freezing it. Any larger candidate inventory needs an explicit bound and complete-result semantics, not silent truncation.
2. Implement and validate the correlated App completion after applied managed runtime/profile publication within the original deadline. If only acceptance is observable, change the contract before advertisement; do not label acceptance `activated:true`. This two-operation slice deliberately provides no durable operation-status/replay mechanism.
3. Confirm secret-preserving candidate-context binding, reliable network-only invalidation classification and the exact UI/remote policy hooks with the native owner. The context is not authorization by itself; existing authenticated transport checks remain mandatory.
4. Freeze shared positive/negative fixtures before advertisement: read while Local unchanged beyond 120 seconds; renewed context with old context still invalid; network-only stale proof retained without list I/O and explicitly re-verified on select; revocation/erase/config/physical identity changes never resurrected; exact compatible dedup; conflicting Origin/token rejection; stale/expired/cross-runtime/cross-receiver context; PIN/kiosk; replaced owner during deferred completion; applied temporary activation versus queued effects; lost reply without replay; missing/invalid runtime or op and malformed/raw-oversize/duplicate-key envelopes.
