# Command API

All command routes require `Authorization: Bearer TOKEN`. Tokens are never accepted in query parameters. Requests and responses use JSON. Unknown query parameters, unknown command fields, malformed values, duplicate object keys, and oversized bodies are rejected. API errors do not echo credentials or command payloads.

## Submit commands

`POST /api/webhook/commands?device_id=ID` requires the administrator token. `/webhook/notify` is an alias. The named device must exist; there is no broadcast or shared default queue. HTTP 202 means the command was queued, not executed. A full queue returns 429 instead of discarding an earlier command.

Supported wire formats:

```json
{"command":"popup_message","message":"Hello","popup_duration":5}
{"command":"channel_by_number","channel_number":3}
{"command":"channel_by_name","channel_name":"News"}
{"command":"random_channel","random_range":[1,10]}
{"command":"change_provider","provider":0}
{"command":"change_playlist","playlist":"https://example.com/list.m3u"}
{"command":"set_volume","volume":35}
{"command":"set_volume","volume_step":5}
{"command":"exit_player"}
```

Channel numbers are one-based; provider indices are zero-based. Volume accepts either an absolute level in 0–100 or a finite relative step, never both. `popup_duration` and `random_range` are optional. A playlist URL must use HTTP or HTTPS. Player-side parental controls and distribution restrictions still apply. Current M3U playlist replacement is provider-specific; unsupported providers report that the command is unavailable.

For compatibility with earlier clients, the server also validates and queues `change_provider_settings` with a `provider_settings` JSON string. The current player has no common provider-settings schema and explicitly rejects this command; do not use it as a configuration-management API.

## Poll with acknowledgement

`GET /api/webhook/commands?delivery=ack` requires the target player's device token. The token selects the queue. An optional `device_id` must match that token's device. `/webhook/poll` is an alias.

```json
{"commands":[{"id":"0123456789abcdef0123456789abcdef","ts":1750000000.001,"expires_at":1750000060,"command":"set_volume","volume":35}],"server_time":1750000000}
```

Commands remain queued until acknowledgement or expiry. `ts`, `expires_at` and `server_time` are Unix seconds and may contain fractions. The entry's expiry remains fixed across retries; `server_time` is refreshed on each poll. Use `expires_at - server_time` to determine remaining lifetime on a player whose local clock is incorrect. Send `POST /api/webhook/commands/ack` with the same device token and body `{"ids":["0123456789abcdef0123456789abcdef"]}`. At most 50 IDs may be acknowledged at once. Repeated acknowledgement is safe; another device cannot acknowledge this queue. Successful polling and acknowledgement return HTTP 200; acknowledgement returns `{"status":"ok","acknowledged":1}`, counting only entries actually removed.

The current player polls about once per second, with timeouts and bounded backoff after errors. It deduplicates repeated IDs before dispatch, retries failed acknowledgements, and invalidates responses when connection settings change. Channel commands can wait for the current channel list to load. Acknowledgement indicates that the client handled or rejected the command; it is not confirmation of picture, audio, PIN approval, or completed provider loading. These legacy command endpoints do not expose execution-result receipts; the optional request API below returns player handler results.

Plain GET without `delivery=ack` retains the legacy array response and removes delivered commands immediately. Do not mix legacy and acknowledgement clients against the same device queue. The server timestamps commands for compatibility, but new clients deduplicate by ID rather than timestamps.

## Status and health

`GET /api/devices` requires the administrator token and returns device IDs, pending queue counts and last device activity. Tokens and command payloads are omitted. Last activity is not proof of successful playback.

`GET /healthz` and `GET /readyz` require no token and expose only process readiness. They do not probe a TV or provider. `ottplay-control-server healthcheck --url http://127.0.0.1:8081/readyz` supplies the container probe without a shell or curl.

## Controller debug snapshot

`GET /api/debug` uses the existing administrator token, origin rules and rate
limits. It accepts no query parameters, returns `Cache-Control: no-store`, and
does not send commands to players. `ott server debug [-j]` reads this endpoint.
The exact version-1 response shape is:

```json
{
  "version": 1, "sampledAt": 1791264000000, "consistent": false,
  "process": {"uptimeMs": 1000, "goroutines": 8, "heapAllocBytes": 1048576, "heapSysBytes": 4194304},
  "control": {"devices": 2, "queues": 1, "pending": 1, "queueBytes": 100, "resultEntries": 0,
    "resultBytes": 0, "commandTtlMs": 60000, "maxPendingPerDevice": 50},
  "diagnostics": {"configured": false, "runtimes": 0, "sessions": 0, "repairs": 0,
    "eventBytes": 0, "reservedStops": 0, "controlSlotsUsed": 0, "controlSlotsCapacity": 64,
    "eventSlotsUsed": 0, "eventSlotsCapacity": 16}
}
```

Numeric values are nonnegative safe integers; `sampledAt` is epoch milliseconds.
`uptimeMs` is the age of this server instance. `queues` counts nonempty device
queues. Sections are independently sampled, so `consistent` is always false.
Counts describe stored entries, which can include expired entries awaiting
ordinary cleanup. Reading does not expire entries or alter queue/diagnostic
state; normal request authentication and rate accounting still apply. No device
IDs, credentials, request payloads or environment variables are returned.

## Limits and origins

- 1–64 configured devices, each with a distinct 32–256 character URL-safe token; the administrator token must also be distinct.
- 1–50 pending commands per device; default 50. At most 2 MiB of pending command data across all queues.
- Command lifetime 1–3600 seconds; default 60. HTTP request body limit 16 KiB.
- Fixed rate limits: 240 administrator requests/minute and 120 device requests/minute per device. The shared limit is `120 × configured device count + 240` requests/minute, including unauthenticated attempts. This allows all 64 configured devices to poll once per second. Polling and acknowledgement both count. HTTP 429 requires backoff.
- Popup messages and provider-settings/playlist strings are limited to 8192 bytes; channel names to 1024 bytes. Popup duration, when provided, must be 0.001–3600 seconds. Numeric channel/provider indices must be integers representable exactly by JavaScript.
- At most 64 exact allowed HTTP(S) origins. No wildcard origin, credential-in-URL, or path origin is accepted. Browsers preflight Authorization/Content-Type; allowed responses never use `Access-Control-Allow-Origin: *`.
- `allow_null_origin` defaults to false. Opt-in permits packaged player device routes; administrator routes still reject `Origin: null`. Ordinary trusted CLI clients may omit Origin.

CORS is a browser boundary, not authentication. Keep administrator access separate, protect the configuration file, and use TLS or a trusted network for transport.

## Request/response extension (protocol 1)

`POST /api/requests?device_id=ID` (administrator) accepts `{"action":"status","params":{}}` and returns HTTP 202 with a random request `id`. Poll `GET /api/requests?device_id=ID&id=ID` with the administrator token: 202 means pending, 200 returns `{id,status,data}`, and 404 means expired/missing. Actions: `status`, `providers`, `channels`/`programs` with optional `search`, `play`/`provider` with a string `query`, `command` with a validated legacy command object, and `provider_settings` with `{provider,settings}`. Provider settings are schema-checked by the active player's supported driver and never appear in read results.

`provider_settings` accepts provider IDs `m3u`, `xtream`, `stalker`, `ottclub`
and `plex`. The controller requires exactly `provider` and an object-valued
`settings`; provider-specific field validation remains on the player. For Plex,
select the Plex provider on a compatible player, then send its server URL and
token together:

```json
{"action":"provider_settings","params":{"provider":"plex","settings":{"server":"http://nas.example:32400","token":"YOUR_PLEX_TOKEN"}}}
```

Only the selected device's authenticated command poll receives these settings.
The normal player result contains `{fields:["server","token"],provider:"plex",saved:true}`,
without the server URL or token values. The controller relays the player's result;
the player is responsible for omitting credentials. A saved acknowledgement does
not establish server reachability or completed playback. Older players can reject
unsupported Plex settings; callers must not replay an uncertain request.

For a numbered Stalker slot on a compatible player, `provider_settings` accepts
`{"provider":"stalker","settings":{"profile":1,"name":"My portal","server":"https://portal.example/c/","mac":"02:00:00:00:00:01"}}`.
All four settings fields are required; `profile` is an integer from 1 to 15.
The player saves that slot without changing the selected Stalker slot. Its
receipt contains `provider`, `profile`, `saved` and the field names only.
Editing an inactive slot does not reload playback. The existing `server`/`mac`
form without `profile` continues to update the selected slot.

`profiles` accepts exactly `{}`. For the active M3U provider it returns
`{provider:"m3u",profiles:[{number,name,active,history_hours,playlist_configured,vportal_configured}]}`:
exactly 15 ordered slots numbered 1–15, with one active slot. History is an
integer 0–8760 or `null` for an invalid legacy value. Configuration flags are
booleans; URLs and credentials are not returned.

`profile` accepts exactly `{number:N}` and returns
`{provider:"m3u",profile:METADATA,dispatched:true}` for the selected, active slot.
`profile_settings` accepts exactly `{number:N,settings:{...}}`, with a nonempty
allowlist of `name`, `playlist`, `history_hours` and `vportal`. Numbers must be
JSON integers: slot 1–15, history 0–8760. Strings must be valid Unicode without
C0/DEL controls, limited to 256 UTF-8 bytes for names or 8192 for either link.
Empty strings clear fields. Unknown/duplicate fields and malformed settings are
rejected; the 16 KiB request envelope limit still applies. The player validates
and applies all settings together, returning
`{provider:"m3u",profile:METADATA,saved:true}`. A saved result does not establish
that provider loading completed. These operations require the active M3U driver
and remain subject to parental and platform policy.

`restart` accepts exactly `{target:"stream"}` or `{target:"player"}`. Stream
success is `{accepted:true,target:"stream",dispatched:true}`. Player reload
acceptance is `{accepted:true,target:"player",dispatched:false,effect:"reload-after-ack"}`:
the reload callback runs once only after its response POST receives HTTP 200
with `status:"ok"`. The callback is not reconstructed from a cached response on
replay and is discarded on expiry or command-server reconfiguration/disable.
Clients must not infer completed restart from either acknowledgement or replay
an uncertain request. Unsupported playback backends use `status:"unsupported"`.

`playback` accepts exactly `{operation:"previous_channel"}` or
`{operation:"next_channel"}` for one adjacent-channel switch in the active
playback category, with wrap at either end. These are the operations behind
`ott PLAYER prev` / `previous` and `ott PLAYER next`. No index, position,
repeat count or UI key is accepted. The player validates the current channel
selection and local access policy before calling its normal remote channel
selection path, which closes an open channel list. Browsing a different category
does not change the playback category used for the step. Unready/stale state,
protected UI, standby, kiosk and settings locks reject the operation.

Success data is `{operation,dispatched:true,channel:{id,number,name}}`, without
stream URLs or credentials. `number` is the one-based `channels` catalogue
position, not the index within the active category. `capabilities` with empty
params reports these operations in its `playback` list when available. They
require an updated controller and player; there is no legacy/input fallback.
Response retries use the same request ID and do not repeat the step within the
player session. Do not submit a new request after an uncertain result.

### Explicit aspect-ratio extension

The typed `aspect` action reads or explicitly selects a supported picture mode.
It does not change the legacy command envelope or the `input` key named
`aspect`. All requests require the page runtime from `capabilities.player.runtime`,
matching `^[a-z0-9-]{1,64}$`:

```json
{"action":"aspect","params":{"operation":"get","runtime":"page-123-abcd"}}
{"action":"aspect","params":{"operation":"set","runtime":"page-123-abcd","mode":"fill"}}
```

These are exact shapes. `get` has no mode; `set` requires exactly `fit` or `fill`.
Unknown, duplicate, null, numeric or extra fields are rejected. The full mode
names accepted by the CLI are aliases only and are not valid wire values.
`fit` means contain (preserve the whole picture); `fill` means cover (preserve
proportions and crop edges). There is no stretch, toggle or arbitrary-ratio mode.

Supporting players add this optional capability:

```json
{"aspect":{"version":1,"operations":["get","set"],"modes":["fit","fill"]}}
```

Operations and modes are distinct allowlisted arrays; subsets are permitted.
An unavailable engine may omit `aspect` or return both arrays empty. Either both
arrays are empty or both are nonempty. A locked player can advertise only
`operations:["get"]` with its supported modes. Clients must check the requested
operation and mode before submission and must not fall back to key events.
The capability does not guarantee that the runtime, media target or local
policy will remain unchanged until execution.

Successful `get` response data is exactly:

```json
{"version":1,"runtime":"page-123-abcd","operation":"get","mode":"fill","saved_mode":"fill","persisted":true}
```

`mode` is `fit` or `fill`; `saved_mode` is one of those modes or `null` when no
explicit persistent value can be read. `persisted` is a JSON boolean equal to
`saved_mode != null && saved_mode == mode`. Memory-only storage fallbacks must
not masquerade as persistent readback. The existing player scope is retained:
current live channel or shared VOD/media setting, not a global override for
every channel. Unknown legacy engine modes are not reported as Fit or Fill.

Successful `set` acceptance data is exactly:

```json
{"version":1,"runtime":"page-123-abcd","operation":"set","mode":"fill","accepted":true,"dispatched":false,"effect":"aspect-after-ack"}
```

The response echoes the bound runtime and requested mode. It does not claim
that the setting has already been applied or saved. The controller validates
response shape and request binding before acknowledgement, including response
retries. Successful reads must also have consistent persistence fields. The
player applies the change once, only after the existing successful response acknowledgement,
and rechecks expiry, runtime/connection generation, media target, screen owner
and kiosk/PIN/protected-input policy first. Expired or invalidated callbacks
must be discarded; replaying a cached result must not recreate the effect.
Use a new read request to confirm mode and persistence. Timeouts and rejected
or malformed results do not authorize automatic resubmission of a set request.
The web operation journal can report `action: "aspect"` for set requests;
handler completion is not evidence of saved settings or visible output.

Negative `rejected` or `unsupported` responses are bound to the same request too.
Their data has exactly these fields (`mode` is present only for a set):

```json
{"version":1,"runtime":"page-123-abcd","operation":"get","error":"unavailable"}
{"version":1,"runtime":"page-123-abcd","operation":"set","mode":"fill","error":"restricted"}
```

`error` is one of `invalid_request`, `runtime_mismatch`, `restricted`,
`unsupported` or `unavailable`. Runtime, operation and set mode must match the
queued request. Generic or mismatched negative responses are rejected without
removing it, so an older page cannot close another runtime's request by replying
that aspect control is unsupported.

These requests use the same authentication, device ownership, bounded queue,
TTL and result limits as the other typed actions. They do not grant native
device privileges or bypass local controls. See the
[CLI examples](cli.md#aspect-ratio-fit-and-fill).

### Ordered Plex queue extension

`plex_queue` uses the administrator-authenticated request lane. Player device
credentials may receive requests and post their own results but cannot submit
them; protocol-2 diagnostic operator scopes do not grant queue control. This
additive RPC does not change the generated legacy command envelope.

```json
{"action":"plex_queue","params":{"op":"preview","runtime":"page-123","ids":["78777","78776","78775"]}}
{"action":"plex_queue","params":{"op":"play","runtime":"page-123","ids":["78777","78776","78775"]}}
{"action":"plex_queue","params":{"op":"status","runtime":"page-123"}}
{"action":"plex_queue","params":{"op":"next","runtime":"page-123"}}
{"action":"plex_queue","params":{"op":"previous","runtime":"page-123"}}
{"action":"plex_queue","params":{"op":"stop","runtime":"page-123"}}
```

`runtime` must match `^[a-z0-9-]{1,64}$` and the current
`capabilities.player.runtime`. Only `play` and `preview` accept `ids`: 1–100
strings matching `^[1-9][0-9]{0,19}$`. Their order and duplicates are preserved.
Unknown/duplicate fields, numeric JSON IDs, leading zeros, URLs and extra options
are rejected. The player uses its saved Plex credentials; no URL/token is accepted
in this request. The existing 16 KiB request limit remains.

Capabilities optionally include
`plex_queue:{version:1,operations:["play","preview","status","next","previous","stop"],max_items:100}`.
Absence means the client must not assume support. Updated CLIs discover the
capability before sending an explicit Plex operation; old `plex setup` behavior
is unchanged.

Successful queue data contains exactly the required fields below, with optional
`title` and `error`:

```json
{"version":1,"runtime":"page-123","active":true,"state":"playing","ids":["78777","78776","78775"],"index":0,"repeat":"none","order":"listed","title":"First film"}
```

States are `idle`, `preparing`, `playing`, `paused`, `ended`, and `error`.
`active` means a nonempty retained queue owns navigation, including after an end
or failure. `index` is zero-based within `ids`; an empty queue has `index:null`
and `active:false`. `idle` is empty; `ended` retains the final index. `stop`
returns idle and clears the queue. `play` receipts echo the exact ID sequence and
index zero; preparation/acceptance must not be presented as decoder confirmation.
`title` is at most 512 UTF-8 bytes without C0/DEL controls. `error` is a static
allowlisted message, never a backend response, URL or credential. Queue metadata
is capped at 16 KiB and validated against the runtime bound when the request was
enqueued. Result expiry and authenticated read rules are unchanged.

Preview returns `{version:1,runtime,state:"ready"|"error",ids,titles,order:"listed"}`,
with optional static `error`. Ready `titles` has one title per submitted ID in the
same order; error `titles` may be empty. Each title has the same 512-byte bound,
and the total serialized preview response data is at most 128 KiB. Preview checks availability
without selecting a provider, starting playback or replacing a retained queue.
Readiness does not promise future network availability or successful decoding.

While a queue is retained, `playback` operations `next_channel` and
`previous_channel` route to it atomically. Their successful result is
`{operation,dispatched:true,plex_queue:QUEUE_METADATA}` instead of `channel`.
The controller rejects a result containing both destinations. At either queue
boundary the step is rejected without wrapping or falling back to TV. Outside
the queue, the existing channel behavior remains. Natural end-of-stream advances
the ordered list and stops after its final item, with no shuffle or repetition.

The player checks request expiry, runtime and context before committing prepared
playback and retires stale callbacks. CLI timeout does not cancel a request already
accepted under the server TTL; late execution before that deadline can be uncertain.
Never automatically reissue a mutation on timeout. Read `plex status` to inspect
the current queue, and use a new request only after assessing that state.

### Remote screenshot extension

`screenshot` accepts exactly `{runtime:"page-runtime-id"}`, where runtime matches
`^[a-z0-9-]{1,64}$` and is the identity from `capabilities.player.runtime`.
The player must verify its current runtime, enabled controller connection and
available capture source before capture. That connection authorizes native
screenshots without a separate grant or expiry. Browser capture still needs a
source selected through the browser's local picker; this action never opens that
picker remotely. Player settings, PIN screens and visible credentials may be
captured. OS capture policy and adapter limitations still apply. It does not extend the
legacy command envelope or protocol-2 telemetry/repair scopes.

Capabilities optionally add
`{screenshot:{state:"ready"|"permission_required"|"unsupported",source:"player-view"|"player-window"|"browser-tab"|"window"|"display"|null}}`.
A ready state requires a non-null source. Omission denotes an older player, not
capture support. `permission_required` remains the compatible wire value for a
missing browser source or disabled connection; older players can also use it for
their legacy local permission. Source and runtime are rechecked by the CLI after capture.

A successful `status:"ok"` screenshot response contains exactly:

```json
{
  "version": 1,
  "runtime": "page-runtime-id",
  "mime": "image/png",
  "encoding": "base64",
  "image": "CANONICAL_BASE64_PNG_BYTES",
  "width": 1280,
  "height": 720,
  "captured_at": 1791288000000,
  "source": "player-view",
  "video": "unknown"
}
```

`width` and `height` are integers in 1–1280 and 1–720; `captured_at` is a positive
JavaScript-safe integer Unix timestamp in milliseconds. `source` uses the
non-null capability source values; `video` is `"unknown"` or `"excluded"`.
The decoded PNG is at most 1 MiB. No URL, filename or alternate image encoding
is accepted. The controller binds the successful result to the runtime in its
queued action, validates exact metadata, canonical base64, actual PNG dimensions,
chunk checksums and compressed pixels before storing it. Invalid results return
HTTP 400 and do not remove the request or create a result. Full PNG validation
runs outside the shared queue lock; queue ownership/expiry is checked again
before storage. Other request actions retain their existing response behavior.

The existing authenticated device/admin boundaries, allowed origins, request
TTL, 2 MiB result-envelope limit, total result-memory bound and 60-second result
TTL apply. Images remain in memory only and responses use `Cache-Control: no-store`.
The player drops cached/pending image bytes on disconnect, connection changes,
browser sharing stop or reload, and at the
earlier of original request expiry or 60 seconds after capture completion.
Bounded rejection tombstones prevent recapture on replay after image eviction.
Already accepted server receipts keep their separate result TTL; local
revocation cannot retract an in-flight upload. `rejected` and `unsupported`
remain explicit negative response statuses. Clients
must not retry capture automatically after an uncertain receipt; they may retry
reads of the same result ID within their deadline. See [CLI screenshots](cli.md#remote-screenshots)
for connection/source setup, filesystem handling and platform limitations.

`epg_catalog` accepts exactly `{}` and returns a lightweight snapshot:
`{catalog,channels:[{id,number,name,tvgId,tvgName,shift,archiveHours}],archive:{version:1,revision}}`.
`archiveHours` is the channel retention capped at 144 hours; zero means no archive.
`archive.revision` remains stable across receipt renewal but changes when the
source, channel load, metadata or retention changes. Older players can omit the
archive extension and still support live EPG search. It must not fetch guide
rows. `catalog` is an opaque player-owned revision, `number` is the one-based
catalogue position, and `shift` is the provider's additional time adjustment in
integer seconds. No source URLs or credentials are included. `play_catalog`
accepts exactly `{catalog,id}`; both strings must be nonempty, with at most 128
UTF-8 bytes for `catalog` and 2048 for `id`. The player verifies its current
catalogue still matches before dispatching playback and returns
`{dispatched:true,channel:{id,number,name}}`. A changed catalogue is rejected.

The CLI's optional EPG configuration uses these RPCs around a separate public
`POST /current` call. The control server never forwards its administrator token
to that service. Existing `programs` remains available for compatibility when
the CLI has no EPG service configured.

`resolve_archive` and `play_archive_catalog` both accept exactly
`{catalog,id,start,end,title}`. Times are integer Unix seconds, `start < end`, and
the title is nonempty (at most 65536 UTF-8 bytes on the server, 16384 UTF-16 code
units on the player). The player revalidates the receipt, category membership,
parental access and `now - min(retention,144h) <= start < end <= now`.

`resolve_archive` is an explicit authenticated read returning `{resolved:true,url}`.
It does not switch channels or open a PIN prompt. Unlike ordinary catalogue and
guide queries, this RPC returns a private provider URL for a local media-byte
probe. The CLI never prints or caches that URL or forwards it to the public EPG
service. The Mac/controller must be able to reach that source. A locked channel
must first be unlocked on the player.

`play_archive_catalog` resolves the provider URL again at dispatch time, selects
the channel through the shared playback coordinator, and opens the archive at
`start`. It returns `{dispatched:true,channel:{id,number,name},start,end}` without
a URL. Dispatch is not a decoder/visible-picture confirmation. An uncertain
mutation response is never replayed automatically. Both the controller and the
player must include these additive RPCs before archive launch is available.

`vportal`, `vportal_random` and `vportal_search` accept exactly `{"query":"TITLE FILTER"}`. The
query must be a string of at most 1024 UTF-8 bytes and must not be empty after
trimming whitespace. Matching is case-insensitive and performed by the player.
`vportal` requests playback of every matching VPortal video in catalogue order,
repeating from the first after the last ends. `vportal_random` shuffles the
complete matching selection once and repeats that permutation; a new request
creates a new shuffle. `vportal_search` only lists the
matches. Success data is `{"items":[{"number":1,"title":"Video"}],"total":1}`;
playback adds `"loop":true,"dispatched":true`. `vportal_random` additionally
returns `"shuffled":true`, which clients must require before reporting success.
Numbers are one-based positions
within the result queue. Results contain title metadata only, never stream URLs
or credentials. Dispatch does not establish rendered playback or successful
completion of the queue. Unsupported or rejected requests use the existing
response statuses; the client never retries the initial POST automatically.
The player dispatches only after collecting the complete bounded selection.
Natural completion advances and wraps the queue; Stop or source replacement
cancels pending work. A media or resolution error does not count as completion.

ACK-mode device polling additionally returns `request_protocol:1` and a `requests` array. Old players ignore the extension. Requests share the existing per-device queue and TTL. Legacy polls and command ACKs cannot consume requests. The device submits `{id,status,data}` to `POST /api/responses`; accepted statuses are `ok`, `rejected`, `unsupported`. The matching pending request is removed atomically with result retention. Retries are idempotent, cross-device responses are rejected, and expired requests cannot create results.

Results are bounded to 2 MiB each, 50 per device and 16 MiB globally; they expire 60 seconds after receipt. Command requests remain bounded to 16 KiB and 2 MiB of total pending queue data. All endpoints use the existing authentication, CORS and rate limits. Null origins can access device responses only when explicitly enabled. Results and last activity are in-memory; a restart clears them. Player request execution is generation-cancelled on disconnect and completed IDs are deduplicated with bounded retention during one page session. A dispatch result is not proof of playback or completed provider loading.

Programme results use one `as_of` Unix-seconds timestamp captured at collection start. `checked` counts completed channel lookups and `partial` indicates an unfinished collection, not whether every provider feed was available. Channels with no current programme are omitted; the guide service does not distinguish missing data from a fetch failure in an empty result.

## Opt-in DNS-SD discovery and pairing

These routes return 404 unless the optional `discovery` configuration is present:

```json
{
  "discovery": {
    "domain": "alvit.cf",
    "nameserver": "192.168.160.1:53",
    "public_url": "https://www.2560801.xyz/ott-control"
  }
}
```

`domain` and `nameserver` may be omitted to use the server's resolver search
configuration from `/etc/resolv.conf`. Explicit resolvers must be literal
`IP:port` addresses. This is server-side unicast DNS-SD, not browser mDNS.
Clients cannot choose a resolver, domain, or URL through query parameters.
`public_url` is this server's externally reachable HTTPS base and is required;
it is also the authority against which a selected controller is checked before
any device credential is released.

DNS contains routing information only, never access codes or credentials:

```dns
_ottplay-ctrl._tcp.alvit.cf. 30 IN PTR home._ottplay-ctrl._tcp.alvit.cf.
home._ottplay-ctrl._tcp.alvit.cf. 30 IN SRV 0 0 443 www.2560801.xyz.
home._ottplay-ctrl._tcp.alvit.cf. 30 IN TXT "txtvers=1" "scheme=https" "path=/ott-control"
```

An instance must have exactly one SRV and one TXT record. The TXT fields above
are required, unique, and the only accepted fields. Only HTTPS addresses with a
valid host/port and an unambiguous base path are accepted: no credentials, query,
fragment, percent-encoded path, empty path segment, or `.`/`..` segment. Instance
IDs are lowercase DNS instance FQDNs with the trailing dot. The instance label
must be an ASCII DNS label (letters, digits, hyphens, at most 63 characters),
immediately above the service domain; escaped or multi-label instance names are
not accepted. A resolution returns at most eight instances and considers at most
four system search domains and three system resolvers. One complete resolution
has a three-second deadline, UDP truncation falls back to TCP, concurrent
lookups coalesce, and successful results are cached for at most 30 seconds or
the shortest record TTL. Each caller can cancel its own wait without cancelling
the shared lookup, which retains its three-second deadline even if no callers
remain. Failures have a two-second discovery cache.

`GET /api/discovery` needs no credential and returns:

```json
{
  "version": 1,
  "servers": [{
    "id": "home._ottplay-ctrl._tcp.alvit.cf.",
    "domain": "alvit.cf",
    "address": "https://www.2560801.xyz/ott-control"
  }],
  "pairing_url": "https://www.2560801.xyz/ott-control/api/pairings"
}
```

This hosted bridge exposes only DNS descriptors whose canonical HTTPS base
equals its configured `public_url`, including the port and base path. Foreign
controllers are omitted even when their DNS records are otherwise valid; an
empty or foreign-only DNS service set returns `servers: []`. The `pairing_url`
always identifies this configured server and does not select a controller.
Thus a hosted player using a fixed trusted HTTPS discovery URL cannot be
redirected to another controller merely by changing a DNS-SD record.

Native OS DNS discovery is a separate trust model: it can list multiple
controllers and trusts the network administrator's DNS configuration to nominate
them. TLS authenticates the nominated hostname; it does not prove that the
controller belongs to the user. The administrator must check the controller
address and comparison code before approving pairing.

DNS/configuration failures return 503. Bootstrap responses have
`Cache-Control: no-store`. Existing exact CORS
origins apply; `allow_null_origin` additionally permits null origins on device
bootstrap routes, but never on the administrator listing or approval route.
CORS preflights permit only Authorization and Content-Type headers. Public
bootstrap traffic and pairing creation have separate bounded rate limits.

### Creating a pairing request

`POST /api/pairings`, Content-Type application/json:

```json
{"device_id":"registered-device-uuid","server_id":"home._ottplay-ctrl._tcp.alvit.cf."}
```

`server_id` is optional only when exactly one valid server is discovered in the
full internal DNS result, before the hosted response filter. Clients should
send the selected descriptor's ID even when the hosted response has one entry.
The selected server must match a freshly discovered descriptor and its normalized
address must equal this server's configured `public_url`; a server must never
issue its own credentials for a different discovered controller. Ambiguous,
missing, changed, or nonmatching selections return 409. The device UUID must
already be registered in the private server configuration; an unknown UUID
returns 400. UUID knowledge alone never authorizes command access.

A successful request returns 201 with:

```json
{"id":"32-lowercase-hex-characters","secret":"43-URL-safe-random-characters","code":"ABCD2345","expires_in":600}
```

The ID uses 128 random bits, the private claim secret uses 256 random bits and is
stored only as a SHA-256 hash, and the visible comparison code uses 40 random
bits encoded as eight uppercase Base32 characters. The client retains the
secret privately and shows the comparison code on the device. There are at most
64 live pairing requests and two per device. Creation is limited to three
requests per device and 30 globally per minute; exhausted limits return 429.
Pairing data is memory-only and expires after ten minutes, including approval
and redemption. A restart invalidates all outstanding pairing requests.

### Administrator approval

`GET /api/pairings` with the administrator Bearer credential returns
`{"pairings":[{"id":"…","device_id":"…","code":"…","address":"…","expires_in":600}]}`.
Only pending requests appear. Neither a claim secret nor any device/admin token
is included. The administrator compares the code with the device and checks the
bound controller address before approving.

`POST /api/pairings/approve` with the administrator credential and body
`{"id":"…","code":"ABCD2345"}` returns `{"status":"approved"}`. Codes match
exactly, including case. Incorrect codes return 403; five failed comparisons
invalidate the request. Unknown/expired requests return 404. Approval is
one-time and atomic; repeated/concurrent approvals after the first return 409.
A fresh DNS check precedes approval. A successful lookup proving that the bound
descriptor changed or disappeared invalidates the request and returns 409.
A lookup error or cancelled wait returns 503 without approving or deleting the
request; the same request can be retried until its original expiry.

### Private claim

`GET /api/pairings?id=…` uses `Authorization: Bearer <claim-secret>`, not an
administrator or device credential. It returns 202 `{"status":"pending"}` until
approved. Once approved, a fresh DNS check precedes the 200 response:

```json
{
  "status":"approved",
  "device_id":"registered-device-uuid",
  "address":"https://www.2560801.xyz/ott-control",
  "token":"existing-private-device-token"
}
```

The token is exactly the registered device's existing credential, never an
administrator token. Repeating the claim with the same private secret is allowed
until expiry so a lost response can be recovered. Missing/wrong/expired claim
credentials return 401. A successful lookup proving that the bound descriptor
changed or disappeared invalidates the request and returns 409. A lookup error
or cancelled wait returns 503 without disclosing the token or deleting the
request; retrying does not extend its original expiry. The client stores the
device credential locally, discards the pairing secret, and enables normal
outbound command polling only after the user completes pairing. Existing
manually configured players require no re-pairing.

### Cancelling or retiring a claim

`DELETE /api/pairings?id=…` uses the matching claim secret and returns
`{"status":"cancelled"}`. No DNS lookup is needed. Administrator credentials,
device credentials, and another claim's secret cannot cancel the request; wrong,
missing, or expired secrets return 401. A request without an ID returns 400.
DELETE is included in CORS methods only for `/api/pairings`.

Clients should best-effort cancel before discarding a pending flow, replacing it,
or after successfully storing the approved device credential. This frees the
per-device queue; TTL expiry remains the fallback when cancellation is lost.
The existing device credential remains valid after its pairing receipt is removed.

## Native Android maintenance (protocol 1)

A native agent must have a separate provisioned device identity/token. Polling the
same device queue from both the WebView and the agent is unsupported: protocol 1
does not route requests to runtime instances. `maintenance` accepts exactly
`{operation: health|logs|recover_video}` or
`{operation: update, manifest: HTTPS_URL, sha256: HEX_SHA256}`. The latter is only
an envelope; the agent independently verifies a pinned Ed25519 signature and the
payload digest before execution. There is no remote shell/JavaScript operation.

`vportal_queue` accepts `{operation: play, ids: [POSITIVE_SAFE_INTEGER,...],
loop: BOOLEAN}` with 1–100 unique IDs, or `{operation:
status|next|previous|restart|stop}`. The experimental legacy Android adapter
supports `loop: true` only. IDs remain in caller order. Capability/policy checks
and outcome reporting belong to the device; a successful enqueue is not evidence
of installation or successful playback. Use the dedicated native CLI commands.

## Read-only player inspection (protocol 1)

`inspect` reuses `/api/requests`, the existing command poll, and
`/api/responses`; it does not start another poller or a diagnostic capture.
Discover the page runtime from player capabilities before issuing a request:

```json
{"action":"inspect","params":{"version":1,"runtime":"page-123","section":"doctor"}}
```

`section` is `doctor`, `snapshot`, `operation`, or `debug`. The first two return the same
bounded player snapshot in version 1. For `operation`, add the required
`operation_id` of the earlier command; that field is forbidden on the other
sections. Runtime identifiers are 1–96 ASCII letters, digits, underscores,
periods, or hyphens. Operation IDs are the existing 32-character lowercase hexadecimal command IDs.
No other request fields, arbitrary selectors, scripts, or URLs are accepted.

A successful `/api/responses` body has the ordinary `id` and `status:"ok"`,
with a bound inspection envelope in `data`:

```json
{
  "id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "status":"ok",
  "data":{
    "version":1,
    "runtime":"page-123",
    "section":"operation",
    "data":{
      "operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "state":"invoked",
      "action":"playback",
      "evidence":{"kind":"handler_completed","generation":null,"position":null}
    }
  }
}
```

Operation states are `unknown`, `accepted`, `invoked`, `observed`, `rejected`,
`unsupported`, or `expired`. Evidence kinds are `none`, `handler_completed`,
`media_progress`, or `runtime_changed`. `action` is a known mutation action or
`null`; evidence generation and position are numbers or `null`. Position is in
seconds. The current player journal does not emit `observed`; that state and
its `media_progress`/`runtime_changed` evidence are reserved for future
independently verified outcomes. The schema requires `observed` to carry one of
those evidence kinds, and `media_progress` to carry non-null generation and
position.

`accepted` means an after-ACK effect was queued, not that it is still pending.
A disconnect, server rejection or ACK deadline can discard the effect without
updating that receipt. `invoked` means the handler ran; it can still decline the
effect if policy or ownership changed. `handler_completed` does not establish
the effect's outcome. Neither stage proves a visible image or successful reboot.
The player holds at most 128 request IDs in memory. A retained receipt reports
`expired` after ten minutes; eviction or reload loses the history. An `unknown`
receipt does not prove the earlier operation never executed. Do not automatically
replay a mutation after losing its response. The server does not manufacture
progress states.

Doctor snapshots contain required `version`, `runtime`, `capturedAt`,
`collectionMs`, `consistent`, `build`, `ui`, `media`, `capabilities`, and `reasons`.
The inner runtime must also match the request. Build identity contains a bounded
version token (including SemVer `+` metadata), nullable exact 40-character lowercase source revision and build
ID, and `embedded`/`partial` identity. UI fields contain document visibility and
focus, a nullable typed UI owner and revision, at most 8 typed panes with bounded
rectangles, and a semantic focus category. Media contains a nullable generation,
typed kind and phase, at most 2 distinct `main`/`pip` lanes, and
`displayEvidence:"unavailable"`. Lanes contain numeric playback state and video
visibility/geometry; they contain no media URLs or settings. At most 10 distinct
capability observations and 16 distinct reason codes are allowed. All nested
keys and enum values are checked; unavailable scalar observations use explicit
`null` where the schema allows it. Snapshots cannot claim physical-display
verification. The canonical player type is `DoctorSnapshot` in
`src/plugins/remote-doctor.ts` of `ottplay-foss`.

The complete response is limited to 32 KiB and a doctor snapshot to 8 KiB.
Integer counters/timestamps are 0–9007199254740991; playback seconds are
0–315576000; collection time is 0–60000 ms. Rectangles use integral CSS pixels:
coordinates −32768–32768 and dimensions 0–32768. Browser ready/network states use
their defined numeric ranges. Duplicate keys, unrecognized fields, invalid
nullable values, and mismatched identities are rejected before dequeueing.

Negative statuses `unsupported` and `rejected` also require the bound envelope:

```json
{"id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","status":"unsupported","data":{"version":1,"runtime":"page-123","section":"doctor","error":"unsupported"}}
```

The only error codes are `invalid_request`, `runtime_mismatch`, `unsupported`,
and `unavailable`. A naked legacy error or a reply from another page cannot
close the request. Invalid replies leave it pending until a valid response or
ordinary expiry. Valid responses retain the ordinary 60-second result TTL.
An older controller may reject the new action and an older player may not
answer it; clients must report unsupported/unknown, not pretend inspection
succeeded.

Runtime matching is an observation fence, not new authorization or routing.
Existing administrator/device access rules still apply, and a protocol-1 device
queue still needs exactly one consumer. In particular, keep native maintenance
agents on their separate provisioned identity; do not share their token with
the WebView. Inspection neither grants a second local permission nor bypasses
local playback restrictions.

### Runtime debug section

Discover support using the separate `debug:{"version":1}` field of the
capabilities result (`controls.debug` in player status). Existing
`inspect.sections` stays `doctor`/`snapshot`/`operation` for compatibility with
earlier CLIs. Only send the following request when debug support is advertised:

```json
{"action":"inspect","params":{"version":1,"runtime":"page-123","section":"debug"}}
```

The existing success/negative envelopes and runtime binding apply. A debug
`data` object is limited to 16 KiB; the full inspection envelope remains limited
to 32 KiB. The exact data keys are `version` (integer 1), `runtime`, `capturedAt`
(epoch milliseconds), `platform`, `metrics`, `media`, `events`, `eventsDropped`,
and `native`. Platform is `browser`, `webos`, `capacitor-android`,
`capacitor-ios`, `tauri`, or `unknown`. Missing metrics mean unavailable; null
is not a metric value. Unknown fields and metric names are rejected.

- Browser numeric metrics: `uptimeMs`, `loopSamples`, `loopDelayMs`,
  `loopMaxDelayMs`, `loopLongDelays`, `jsHeapUsedBytes`, `jsHeapTotalBytes`,
  `jsHeapLimitBytes`, `hardwareConcurrency`, `deviceMemoryGiB`, `errorCount`,
  `rejectionCount`, `controlPendingRequests`, `controlPendingResponses`,
  `controlConsecutiveFailures`. Boolean metrics: `online`, `focused`, `visible`,
  `secureContext`, `controlActive`.
- `media` has at most two objects, with exact keys `lane` (`main` or `pip`,
  unique), `generation` and `handleId` (nullable safe integers), and `metrics`.
  Numeric metrics: `positionSeconds`, `durationSeconds`, `bufferAheadSeconds`,
  `videoWidth`, `videoHeight`, `totalFrames`, `decodedFrames`, `droppedFrames`,
  `corruptedFrames`, `readyState`, `networkState`, `mediaErrorCode`, `volume`.
  Boolean metrics: `paused`, `ended`, `muted`, `seeking`. `totalFrames` and
  `decodedFrames` are distinct measurements.
- `events` contains at most 32 exact `{sequence,elapsedMs,code}` objects.
  Sequences are strictly increasing safe integers starting at 1; elapsed times
  are nonnegative finite offsets within this collector lifetime. `eventsDropped`
  is a nonnegative safe integer. Event codes are `started`, `resumed`,
  `suspended`, `visible`, `hidden`, `online`, `offline`, `focus`, `blur`, `error`,
  `unhandled_rejection`, `loop_delay`, `media_error`, `media_waiting`,
  `media_stalled`, `media_playing`, or `media_ended`.
- `native` has exact keys `state` and `data`. State is `available`, `unsupported`,
  `unavailable`, `timeout`, or `invalid`; data is nonnull exactly when available.
  Producer keys are `version` (1), `platform` (`android`, `ios`, `tauri`, `server`),
  `appVersion`, `osVersion`, `webviewVersion`, and `metrics`. Versions are null
  or 1–64 ASCII letters/digits/underscores/periods/plus/hyphens.
- Native numeric metrics: `uptimeMs`, `systemUptimeMs`, `residentBytes`,
  `pssBytes`, `footprintBytes`, `heapUsedBytes`, `heapLimitBytes`,
  `systemAvailableBytes`, `systemTotalBytes`, `thermalState`, `logicalProcessors`,
  `nativeHlsSessions`, `nativeHlsBytes`, `nativeHlsErrors`, `epgChannels`,
  `epgProgrammes`, `epgMappings`, `epgShifts`, `requestsTotal`, `requestsActive`,
  `requestsFailed`. Boolean metrics: `lowMemory`, `foreground`, `lowPower`.

All numeric metrics are finite, nonnegative and at most 9007199254740991.
`volume` is at most 1, `readyState` and `mediaErrorCode` at most 4, and
`networkState` at most 3. Native `uptimeMs` measures producer age;
`systemUptimeMs` measures OS uptime. PSS, physical footprint and RSS are separate
fields and cannot be compared as the same measurement. A snapshot contains no
raw logs, error messages, URLs, filenames, stack traces, keys or DOM content.
