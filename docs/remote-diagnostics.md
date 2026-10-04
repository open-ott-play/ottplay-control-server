# Remote diagnostics protocol 2

Diagnostics is an additive, in-memory API under `/api/v2/diagnostics`. It never
enters the protocol 1 command queue. Registration, control polling and telemetry
are separate requests; a legacy request waiting for a player does not block a
diagnostic stop. Run one server replica. Restart discards all diagnostic state
and changes the random `server_epoch` returned in every diagnostic response.

## Enablement and credentials

Diagnostics is off by default. Enable it explicitly on each device with
`"diagnostics": {"enabled": true}` and add a top-level `diagnostics` configuration.
The existing administrator token has no diagnostic operator privileges.

The following is a configuration fragment, not a usable credential:

```json
{
  "diagnostics": {
    "operators": [{
      "id": "support",
      "credential_sha256": "REPLACE_WITH_LOWERCASE_SHA256_OF_A_SEPARATE_RANDOM_OPERATOR_TOKEN",
      "device_ids": ["living-room"],
      "actions": ["runtimes.read", "sessions.start", "sessions.stop", "sessions.read", "runtimes.revoke"]
    }]
  }
}
```

Use a separate random operator bearer of at least 32 characters and store only
its SHA-256 digest in server configuration. Do not hash a password in place of
generating a high-entropy token. Digests must be distinct from device/admin
credentials and other operators. Scopes name exact configured devices and exact
actions; there are no wildcards. Configuration changes apply after restart.
Keep configuration and CLI credentials private. Use HTTPS for credentials and
telemetry when the connection is not a trusted, isolated local network.

`POST /runtimes` requires the device bearer. It creates a random `runtime_id` and
a separate `runtime_credential`; two tabs using one device credential remain
distinct. The authenticated device determines `device_id`. `instance_id` and
`boot_id` are display metadata only, never selectors or authority. Use random
opaque labels, not personal information. `reported_uuid` is validated but not
retained or listed. The server accepts labels matching `[A-Za-z0-9_.:-]{1,80}`.
These character limits are not an anonymization guarantee.

Registration requires `instance_id`, `boot_id`, `capabilities`, and `consent`.
Capabilities is a unique list of at most four of `playback`, `network`, `input`,
and `epg`. Consent is `{ "granted": true, "epoch": "opaque-grant" }` or exactly
`{ "granted": false }`. False consent must omit `epoch`. Unknown, case-variant,
duplicate, and null fields are rejected. The response includes the authenticated
device ID, runtime handle/credential, `runtime_ttl_ms`, and exactly five limits:
`session_lease_ms_max`, `event_bytes_max`, `events_body_bytes`, `events_per_batch`,
and `poll_after_ms`. Runtime credentials are returned only at registration. Unconfirmed registration
lasts at most 10000ms (returned as the initial `runtime_ttl_ms`); a successful
granted-consent poll enables the normal configured sliding TTL. Lost registration
responses therefore cannot consume runtime slots for the full ten-minute TTL.

## Control lifecycle

All responses contain `diagnostics_protocol: 2` and `server_epoch`, including
errors of shape `{ "error": { "code": "bounded_code" } }`. Responses use
`Cache-Control: no-store`. A diagnostic response or allowed-origin OPTIONS
response can provide the epoch before a mutation. An operator must explicitly
use that epoch; do not discover and silently retry a failed mutation.

- `GET /runtimes?device_id=...` requires `runtimes.read`. It returns only that
  scoped device's runtimes, bounded metadata, consent, last-seen age and active
  session ID. It returns no credentials.
- `POST /sessions` requires `sessions.start` and fields `server_epoch`,
  `idempotency_key`, `device_id`, `runtime_id`, `consent_epoch`, `lease_ms`, and
  `profile` (`standard`). The target must be an exact live runtime with the same
  granted consent epoch. A second active/pending session on that runtime is a
  conflict. The session deadline starts at server acceptance, including pending
  delivery, with a lease between 1000 and 600000 milliseconds.
- `POST /poll` requires only the runtime credential and fields `runtime_id`,
  `poll_seq`, `last_control_revision`, and `consent`. `poll_seq` is an increasing
  safe integer, at least 1, for each new poll. An identical latest poll may be
  replayed; a stale or conflicting sequence returns 409 without changing consent
  or extending runtime TTL. Identical replay means the same validated fields,
  not JSON whitespace or key order. Successful granted-consent polls renew the runtime TTL. A final false-consent
  poll instead permanently retires the runtime credential and slot, revokes its
  nonterminal sessions without claiming an acknowledged stop, and returns null
  control. Subsequent requests with that credential fail 401, including a replay
  of the final false poll. A later local grant must register a new runtime.
  A response may contain newly available control;
  replay does not freeze the response in time.
- The poll reply contains `control_revision` at the top level and `control`
  either null or `{request_id,session_id,action,consent_epoch,lease_ms,profile?}`.
  Start contains profile `standard` and the remaining relative lease. Stop has
  `lease_ms: 0`. Nested control has no revision field. Polling is immediate,
  normally every second while a session exists and every three seconds idle.
- `POST /results` requires the runtime credential and exact `runtime_id`,
  `session_id`, `request_id`, `control_revision`, `status`. Status is `applied`,
  `rejected`, or `unsupported`; optional `error_code` is one of `consent_revoked`,
  `lease_expired`, `disconnected`, `suspended`, `unsupported`, `invalid_control`,
  or `local_stop`. Identical recorded results are idempotent; conflicting or
  stale results cannot change state. Results do not renew runtime TTL.
- `POST /sessions/<id>/stop` requires `sessions.stop`, `server_epoch`, a new
  `idempotency_key`, and `reason: operator`. Acceptance closes event ingestion
  immediately, but `device_stop_confirmed` stays false until that exact stop
  control is acknowledged as applied. A reserved receipt slot keeps this first
  stop available even if later start requests exhaust the state budget.
- `GET /sessions/<id>` requires `sessions.read`. The state is `start_pending`,
  `active`, `stop_pending`, `stopped`, `rejected`, `expired`, or `revoked`. It also
  returns device/runtime IDs, control revision, remaining lease and dropped count.
  Remaining lease is a deadline observation, not evidence that capture is active.
- `DELETE /runtimes/<id>` requires `runtimes.revoke` and the exact
  `X-OTT-Diagnostics-Epoch` header. Runtime credentials cannot revoke a runtime.
  Revocation invalidates the runtime credential and terminates server ingestion;
  it is not proof that a disconnected device stopped. A revoked runtime cannot
  submit a later acknowledgement. An expired session may still acknowledge its
  exact outstanding stop while the runtime credential remains valid.

Start/stop return a short 202 acceptance receipt with `session_id`, `request_id`,
`state`, `control_revision`, and `idempotency_retention_ms`. Idempotency is scoped
by operator ID and key; reuse for a different action, target, epoch or body
returns 409. Replays return the original acceptance receipt, not current state;
read the session separately. The receipt horizon is finite, 15 minutes by
default. **After it expires, the same key can produce a new action.** Do not retry
an uncertain mutation blindly, do not replace its key to evade a conflict, and
do not treat a process epoch as indefinite replay protection. Session records
have a fixed terminal retention horizon too; duplicate requests do not extend it.

The client must additionally enforce foreground local consent, a monotonic
session deadline and an independent ten-second connectivity lease. Hidden pages,
pagehide, local stop, revoked consent and lost connectivity stop capture locally.
Clients must not reopen the same session after suspension or extend a deadline
on duplicate control. These client duties are not server-side hardware proof.

## Structured telemetry and bounds

`POST /events` uses only the runtime credential and fields `runtime_id`,
`session_id`, `first_seq`, `events`. Ingestion requires an active acknowledged
session, current consent, and a live deadline. Stop, expiry or revocation closes
ingestion. Events contain `elapsed_ms` (finite 0..600000), `kind`, `code`, and
optional `metrics`. Kind is `lifecycle`, `playback`, `network`, `input`, or `epg`.
Code is `sample`, `start`, `stop`, `waiting`, `playing`, `stalled`, `ended`, `error`,
or `ready`. There is no free-text/log/URL/header/exception-message field.

Boolean metrics: `paused`, `ended`, `available`, `enabled`.
Numeric metrics (finite, nonnegative and at most 9007199254740991): `errors`,
`dropped`, `recoveries`, `stalls`, `waiting`, `bufferAhead`, `currentTime`,
`droppedFrames`, `errorCode`, `networkState`, `readyState`, `height`, `width`,
`duration`, `httpStatus`, `latencyMs`, `loadedBytes`, `inputEvents`,
`inputListeners`, `epgEntries`, `epgPending`, `epgErrors`. At most 24 metric keys
are permitted. Unknown keys, nested objects, strings and nulls are rejected.

`first_seq` and the final contiguous sequence must be safe integers. A forward
gap is accepted and counted as lost events. Backwards/conflicting overlap is 409,
except an identical latest batch. Replay equality uses canonical validated event
values (metrics omission and an empty metrics object are equivalent), not raw
JSON spacing/order. The single latest batch digest is retained even after its
events are evicted. Replaying it never stores events or counts its gap twice.
The response is `{accepted_through_seq,dropped_total}` plus protocol/epoch.
Validation is complete before changing sequence state or evicting records.

`GET /sessions/<id>/events?after_seq=0&limit=32` requires `sessions.read` and
returns `events: [{seq,event}]`, `next_seq`, `truncated_before_seq`, `dropped_total`
and protocol/epoch. `next_seq` is the last returned sequence (use it as the next
`after_seq`), or the supplied cursor for an empty page. Pages may contain fewer
than the requested number to stay within the reply byte limit. Dropped totals
include forward sequence gaps and retention/byte-budget evictions. Oldest stored
events are evicted first; no raw payload is written to logs or disk.

Defaults and hard ceilings are 4 runtimes/device, 256 globally, 1024 retained
sessions, 1024 idempotency records (minimum 2), 10-minute runtime TTL/session
lease, five-minute event retention, 256 KiB of events/runtime and 16 MiB globally.
Limits may be lowered in the diagnostic configuration, never raised above these
ceilings. Control bodies/replies are 4096/8192 bytes; batches/retrieval replies
are at most 16384 bytes; a batch has at most 32 events of at most 1024 bytes each.
The caps count serialized stored event bytes; Go object/map overhead is additional
and bounded by record and byte limits, not claimed equal to those byte ceilings.

Separate control and telemetry budgets apply: 240 control calls/minute/runtime,
120 batches and 256 KiB uploaded/minute/runtime, 10 registrations/minute/device,
240 operator calls/minute/operator, and separate fixed global control/event
request budgets. Admission slots bound concurrent work (64 control, 16 events).
Slow telemetry response writes hold no state mutex and cannot occupy control
slots. Authentication, consent, sequence errors and over-limit bodies never
reflect caller data in errors. Limits are resource bounds, not a claim of
complete denial-of-service protection for the surrounding HTTP deployment.
