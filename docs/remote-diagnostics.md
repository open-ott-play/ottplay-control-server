# Remote diagnostics protocol 2

For installation, operator credential setup, player registration and connection authorization,
all CLI/MCP commands, revocation and troubleshooting, start with the
[operator guide](diagnostics-cli.md). This page defines the HTTP protocol and
server configuration used by those tools.

Protocol-2 **capture** means structured telemetry, not an image. Remote
[screenshots](cli.md#remote-screenshots) use the separate authenticated request
API. An enabled player connection authorizes both diagnostics and supported
screenshots without separate local grants. Diagnostic operator credentials
cannot request images; screenshots require the command API's administrator
credential and, in a browser, a locally selected capture source.

Diagnostics is an additive, in-memory API under `/api/v2/diagnostics`. It never
enters the protocol 1 command queue. Registration, control polling and telemetry
are separate requests; a legacy request waiting for a player does not block a
diagnostic stop. Run one server replica. Restart discards all diagnostic state
and changes the random `server_epoch` returned in every diagnostic response.

## Enablement and credentials

Diagnostics is off by default. Enable it explicitly on each device with
`"diagnostics": {"enabled": true}` and add a top-level `diagnostics` configuration.
The existing administrator token has no diagnostic operator privileges.

The following is a capture-and-repair operator configuration fragment, not a
usable credential or complete server configuration. Add it to an existing
configuration, enable `"diagnostics": {"enabled": true}` on the existing
`living-room` device entry, and retain that device's own token. Omit the repair
scopes if that operator should only capture diagnostics:

```json
{
  "diagnostics": {
    "operators": [{
      "id": "support",
      "credential_sha256": "REPLACE_WITH_LOWERCASE_SHA256_OF_A_SEPARATE_RANDOM_OPERATOR_TOKEN",
      "device_ids": ["living-room"],
      "actions": [
        "runtimes.read", "sessions.start", "sessions.stop", "sessions.read",
        "runtimes.revoke", "repairs.start", "repairs.read"
      ]
    }]
  }
}
```

Use a separate random operator bearer matching `[A-Za-z0-9_-]{32,256}` so it
works with the bundled CLI and MCP clients. Generate it with Python's
`secrets.token_urlsafe(32)` or `secrets.token_hex(32)` and store only its lowercase
SHA-256 digest in server configuration. Do not hash a password in place of
generating a high-entropy token. Digests must be distinct from device/admin
credentials and other operators. Scopes name exact configured devices and exact
actions; there are no wildcards. Configuration changes apply after restart.
Remove an operator entry to revoke all of its access, or replace its digest to
rotate its credential. An operator cannot have empty device/action scopes; at
least one of each is required. Setting a device's diagnostics flag to `false`
denies its diagnostic registration/operator access after restart. These
configuration edits do not change the player's saved connection. An online
player observing a diagnostic authorization rejection stops the affected runtime;
its enabled connection may register a new one later. Disable the device's
diagnostics for a lasting server-side block. Removing an operator does not revoke the
player's separate device access code; the required server restart still retires
all current runtime credentials. Use the
[revocation procedures](diagnostics-cli.md#deregister-disconnect-or-remove-operator-access)
for the intended lifetime.
Keep configuration and CLI credentials private. The bundled player diagnostics
client and diagnostic CLI/MCP require HTTPS, including on a trusted LAN. Follow
the [server TLS setup instructions](../README.md#start-a-server) to use
`--tls-cert` and `--tls-key` or a TLS-terminating reverse proxy, with a certificate
trusted by each client.

`POST /runtimes` requires the device bearer. It creates a random `runtime_id` and
a separate `runtime_credential`; two tabs using one device credential remain
distinct. The authenticated device determines `device_id`. `instance_id` and
`boot_id` are display metadata only, never selectors or authority. Use random
opaque labels, not personal information. `reported_uuid` is validated but not
retained or listed. The server accepts labels matching `[A-Za-z0-9_.:-]{1,80}`.
These character limits are not an anonymization guarantee.

Registration requires `instance_id`, `boot_id`, `capabilities`, and `consent`.
Capabilities is a unique list of at most five of `playback`, `network`, `input`,
`epg`, and `repairs`. A client advertises `repairs` only when its typed repair
callback is implemented. Consent is `{ "granted": true, "epoch": "opaque-grant" }` or exactly
`{ "granted": false }`. False consent must omit `epoch`. Unknown, case-variant,
duplicate, and null fields are rejected. The response includes the authenticated
device ID, runtime handle/credential, `runtime_ttl_ms`, and exactly five limits:
`session_lease_ms_max`, `event_bytes_max`, `events_body_bytes`, `events_per_batch`,
and `poll_after_ms`. Runtime credentials are returned only at registration. Unconfirmed registration
lasts at most 10000ms (returned as the initial `runtime_ttl_ms`); a successful
granted-consent poll enables the normal configured sliding TTL. Lost registration
responses therefore cannot consume runtime slots for the full configured TTL.
The default confirmed-runtime TTL is 30000ms, configurable from 10000 to 600000ms.
Successful granted control polls keep an active runtime alive; if final revocation
is lost, its slot becomes reclaimable after the TTL without matching tab metadata.

Updated players derive consent from the configured, enabled HTTPS controller
connection. No separate temporary grant, saved trust switch or foreground-only
authorization is required. Startup, reload and reconnection register new
runtimes automatically. The wire consent epoch still binds each operation to the
exact connection/runtime. It does not remove server scopes, session deadlines or
the need for a new operator request to start each capture or repair. Older
players can continue to use their existing local consent policy with this API.

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
  of the final false poll. A later enabled connection must register a new runtime.
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

The client must additionally enforce current connection authorization, a
monotonic session deadline and an independent ten-second connectivity lease.
Background visibility alone does not stop capture. OS suspension/pagehide, local
capture stop, disconnect and a lost connectivity lease retire the affected work.
Resume/reconnection can register a new ready runtime without another permission
prompt, but must not reopen the old session or extend a deadline on duplicate
control. These client duties are not server-side hardware proof. The maximum
ten-minute session lease is a resource bound, not an authorization expiry.

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

Defaults are 4 runtimes/device, 256 globally, 1024 retained sessions, 1024
idempotency records (minimum 2), a 30-second confirmed-runtime TTL, a maximum
10-minute session lease, five-minute event retention, 256 KiB of events/runtime
and 16 MiB globally. Runtime TTL may be configured from 10 seconds to the
10-minute ceiling. The separate client connectivity lease remains 10 seconds.
Other capacity/retention limits may be lowered in the diagnostic configuration,
never raised above their documented ceilings. Control bodies/replies are 4096/8192 bytes; batches/retrieval replies
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


## Exact-runtime typed repairs

Repairs are an additive control lane, independent of diagnostic capture sessions
and event uploads. They do not change the `/poll` or `/results` schema and never
enter the protocol 1 command queue. Only `restart_stream` and `reload_player` are
permitted; there is no arbitrary command, script, parameter map, configuration
write, credential access or provider API. Add `repairs.start` and/or `repairs.read`
to a separate operator's exact device scopes to grant that authority. A diagnostic
operator may have at most seven distinct actions including the two repair scopes.

- `POST /repairs` requires the scoped operator bearer and exactly `server_epoch`,
  `idempotency_key`, `device_id`, `runtime_id`, `consent_epoch`, `action`,
  `deadline_ms`. Deadline is an integer 1000..30000 milliseconds from acceptance.
  The target must be a live, consenting runtime advertising `repairs`; device
  tokens, instance/boot labels and reported UUIDs cannot select another runtime.
  At most one pending repair exists per runtime. A 202 reply contains `repair_id`,
  `state: pending`, `idempotency_retention_ms`, and the protocol/epoch envelope.
- `POST /repairs/poll` requires the runtime credential and exactly `runtime_id`.
  The response is the envelope plus `repair`, either null or
  `{repair_id,action,consent_epoch,lease_ms}`. There is no runtime ID echo or
  diagnostic control revision. Delivery is read-only and does not consume the
  repair, renew consent/runtime TTL, or extend its original deadline. The client
  polls this independent lane every three seconds while eligible; the remaining
  lease must never increase when it handles repeated delivery.
- `POST /repairs/results` requires the same runtime credential and exactly
  `runtime_id`, `repair_id`, `status`. `applied` is valid only for `restart_stream`;
  `accepted` only for `reload_player`; `rejected`/`unsupported` for either action.
  There are no free-text errors. Success returns envelope plus `status: recorded`.
  Identical results replay only while the runtime credential, consent epoch and
  original deadline remain valid. A conflicting recorded status returns 409
  `result_conflict`; expired/revoked operations or an identical replay after the
  deadline return 409 `repair_terminal`. Already recorded applied/accepted history
  is preserved after that late rejection. A retired credential returns 401, wrong
  runtime 403 and an unknown repair 404. Clients must not repeat effects on an
  uncertain result acknowledgement.
- `GET /repairs/<repair_id>` requires `repairs.read` for the exact device and
  returns `repair_id`, `device_id`, `runtime_id`, `action`, `state`,
  `lease_remaining_ms`, and the envelope. States are `pending`, `applied`,
  `accepted`, `rejected`, `unsupported`, `expired`, and `revoked`.

The server records the client's assertion, not observed recovery. For restart,
clients invoke the existing guarded restart once and then report `applied`. For
reload, clients first prepare an after-reply effect and report `accepted`; they
execute only after an exact result acknowledgement while the original lease,
connection consent and the repair action's own safety checks still hold.
`accepted` is acknowledged intent,
not evidence of a reload. On terminal rejection, retire that pending repair effect
and continue diagnostic control polling. On unknown acknowledgement outcomes,
retry only the same result within the bounded deadline, never the repair effect.

A pending repair is revoked on consent withdrawal/epoch change, runtime deletion,
runtime expiry or disabled diagnostics, and expires on its own deadline. That
transition releases its pending slot but preserves bounded history. A restart
loses all repair state and changes the process epoch. The ten-second unconfirmed
registration TTL still applies; repair polls/results do not confirm registration.

There are at most 256 repair records globally. Terminal records are retained for
15 minutes from their first terminal transition; replay does not extend retention.
The server fails with 429 instead of evicting pending work. Repair creation shares
operator/key idempotency with diagnostic start/stop and respects already reserved
diagnostic-stop receipt slots. Changing the action, target or request for a key
conflicts. The configured finite receipt horizon is returned honestly; after that
horizon the same key can act again, even if older repair history is still retained.
Use explicit status reads, not blind retries or a replacement key.

Repair requests use the existing bounded control admission/rate budgets, never the
telemetry budget. Browser requests still require an exact allowed origin. When
`allow_null_origin` is explicitly enabled, `Origin: null` is permitted for the
runtime repair POST routes `/repairs/poll` and `/repairs/results`, alongside device
registration and diagnostic runtime routes. It does not permit null-origin
operator creation (`POST /repairs`) or status reads (`GET /repairs/<id>`). CORS
preflight grants only the route's method and allowed headers; it does not grant
credentials, device/action scopes or repair authority. The server authenticates route role, action scope and URL target
before admitting control work or reading a body, then revalidates current access
and body targets after reading. No mutex is held across request/response I/O.
Unauthenticated slow bodies cannot consume those control slots. This does not
claim denial-of-service immunity against authorized clients or transport traffic.

Client stop handling cancels the repair lane and pending after-reply effects.
Server repair state is currently independent of a diagnostic session-only stop;
that stop alone does not revoke a repair or its consent grant. Explicit consent
withdrawal/runtime revocation provides the server-side retirement described above.
