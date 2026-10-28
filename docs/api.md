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

## Home Assistant event banners

The optional [Home Assistant bridge](home-assistant.md) has a separate,
ephemeral notification channel. It does not enqueue playback commands or use
the durable operation journal.

`GET /api/home-assistant/status` accepts no query parameters. It requires the
administrator credential or a command-operator credential allowed the `status`
action. The
JSON response contains `version: 1`, a credential-free `bridge` snapshot and
`players` rows (`device_id`, `active`, `pending`, optional `last_poll_at`,
`notifications_enabled` and `media_jpeg`). The latter values describe the latest
heartbeat, not a separate persistent server setting.
Administrators additionally receive `delivery` counters: `queued`, `polled`,
`skipped`, `dropped`. Operators receive only their allowed device rows and bridge
fields `enabled`, `state`, optional `last_error_code`; global bridge and delivery
counters are administrator-only. The administrator bridge snapshot reports `enabled`, `state`,
optional `last_error_code`, `reconnects`, `events_received`, `events_matched`,
`events_dropped`, `notifications_queued`, `notifications_skipped`,
`camera_acquired`, `camera_failed`, `camera_skipped` and optional
`last_event_at`. Counts reset on process restart and do not prove display.

`GET /api/notifications?runtime=RUNTIME&active=true` uses the **device** token.
An optional `device_id` must match that token's device. `runtime` is a 1–128
character identifier using ASCII letters, digits, `_`, `.`, `:`, `-`; `active`
must be exactly `true` or `false`. Updated clients initially send only these
legacy fields. After the response advertises `capabilities.enabled:true` and
`capabilities.media:["jpeg-v1"]`, they may add `enabled=true|false` and
`media=jpeg-v1`. Missing `enabled` defaults to true; missing `media` means text
only. A locally disabled preference always also sets `active=false`, including
when connected to an older controller. Unknown parameters are rejected. Only the
updated player should poll this destructive-read endpoint; manually polling it
can consume a real banner before the player receives it.

The response has this shape (sample values, not a delivery receipt):

```json
{
  "version": 1,
  "server_time": 1792764000.0,
  "poll_after_ms": 5000,
  "notifications_enabled": true,
  "capabilities": {"enabled": true, "media": ["jpeg-v1"]},
  "notifications": [
    {
      "id": "0123456789abcdef0123456789abcdef",
      "runtime": "example-runtime-1",
      "title": "Home",
      "message": "Dinner is ready",
      "severity": "info",
      "duration_ms": 5000,
      "expires_at": 1792764010.0
    }
  ]
}
```

Times are Unix seconds. Queues are bounded to four entries per device. Only a
runtime that reported `active=true` and has not reported `enabled=false` within
the preceding 15 seconds is eligible
for enqueue. Presence expiration is applied **before** a poll refreshes it, so
reconnecting cannot revive a stale queue. A different runtime, `active=false`
or `enabled=false` clears pending entries and revokes media grants. A successful
poll removes returned items; there is no
display ACK or redelivery guarantee. The player also checks its local activity,
runtime and expiration before showing a banner. No notification wakes playback.

### Notification media

A negotiated image recipient may receive an additional `media` descriptor on
an item: `id` (32 lowercase hex characters), `kind:"image"`,
`content_type:"image/jpeg"`, integer `width`, `height`, and `bytes`. Images are
bounded to 640 by 360 pixels and 163840 JPEG bytes. Without a usable image the
same event remains a text notification; no direct camera URL is sent.

`GET /api/notifications/media?id=ID&runtime=RUNTIME` requires the same device
credential and permits an optional matching `device_id`. It returns version 1,
`id`, `runtime`, `content_type`, `data_base64`, `width`, `height`, `bytes`,
`server_time` and `expires_at` (Unix seconds). Access requires a fresh, enabled,
active JPEG-capable runtime and an unexpired grant for that exact device and
runtime; another device's token or a previous runtime cannot read it. Media
responses use `Cache-Control: no-store`. The bounded in-memory store and grants
expire; they are not a recording archive or a public download service.

### Persistent notification preference RPC

The typed `notifications` action uses the existing `/api/requests` and
`/api/responses` exchange. Its capability is
`"notifications":{"version":1,"operations":["get","set"]}`. Requests and
receipts bind to the exact `capabilities.player.runtime`:

```json
{"action":"notifications","params":{"version":1,"runtime":"page-123","operation":"get"}}
{"action":"notifications","params":{"version":1,"runtime":"page-123","operation":"set","enabled":false}}
```

These are exact shapes. Success data contains exactly `version:1`, `runtime`,
`operation`, boolean `enabled` and `persisted:true`; a set receipt must return
the requested boolean. A rejected/unsupported response contains exactly
`version:1`, `runtime`, `operation` and `error`, restricted to `invalid_request`,
`runtime_mismatch`, `storage_unavailable` or `unsupported`. Invalid or unbound
receipts cannot consume the pending request. Operator scopes are
`notifications.get` and `notifications.set`; CLI discovery also needs
`capabilities`.

This is one global setting in the player installation, shared by text and
notification media. It does not alter primary playback. The server never writes
this preference to configuration. See the [canonical examples](../contracts/notifications-v1.json)
and [persistence and compatibility guide](notification-preferences.md).

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

The `kiosk` action uses the same authenticated request route with
`params: {"mode":"status|on|set|off"}`. `on` optionally accepts a string `query`;
`set` requires it. Both accept optional boolean `strict`. `status` and `off`
accept no other fields. For example:

```json
{"action":"kiosk","params":{"mode":"on","query":"Новости","strict":true}}
```

Queries contain 1–1024 UTF-8 bytes without control characters and select a
one-based TV catalogue number or the first literal case-insensitive name match.
Without a query, `on` arms the next TV selection, preserves an existing lock,
or captures the current standalone VPortal/Plex media queue. Omitting `strict`
preserves the existing setting; explicit `false` downgrades without releasing
the target. Updated results include `enabled`, `state`, `strict`, provider and
selection metadata, `retry_seconds`, retry count and observed health; they do
not expose media URLs. A stored-policy receipt does not verify visible playback
or Android system-exit protection. See [kiosk mode](cli.md#kiosk-mode) for
source changes, recovery timing, version requirements and native-wrapper limits.
Newer players may include `startup_grace_seconds: 60` for a media queue and
boolean `screen_suspended` for intentional screen-off/background suspension;
older players may omit them. `strict: true` with `state: waiting` arms strict control locking
for the first channel selection, rather than declaring an already locked UI.

`provider_settings` accepts provider IDs `m3u`, `xtream`, `stalker`, `ottclub`
and `plex` through the existing envelope. Dedicated `vportal` slots additionally
require the runtime/context/revision envelope described below. For the existing
providers, the controller requires exactly `provider` and an object-valued
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

For VPortal, first issue `settings_compare` with
`{provider:"vportal",number:1,settings:{vportal:"portal::...",name?:"Family"},runtime}`.
It returns `{provider,number,matched,runtime,context,revision}` without saved
values. Save with `{provider:"vportal",settings:{profile:1,vportal:"portal::...",name?:"Family"},runtime,context,revision}`.
The receipt contains `{provider,profile,fields,saved:true,reloaded,reload_required,runtime,context,revision}`.
All fields are required except the request's optional name; omitting it preserves
the dedicated slot name. Slot and runtime are request-bound, and both reload
flags cannot be true. No provider/UI switch is performed. Capability
`vportal_settings:{version:1}` identifies support. See the
[fixture](../contracts/vportal-settings-v1.json) and [migration guide](player-settings.md).

### Typed individual preferences

Capability `player_settings:{version:1,operations:["schema","get","set"],max_page_size:20}`
adds a separate `player_settings` action. Requests are `schema` with runtime and
optional cursor/limit, `get` with runtime/id, or `set` with runtime/id/context/
revision/value. Values are bounded integers, strings or string arrays; arbitrary
objects are rejected. Each get returns its descriptor and saved-state snapshot.
The set response requires verified persistence and reports application separately.
All results are at most 64 KiB and bound to the submitted runtime/operation;
write results also bind to the setting ID, source context and supplied value.
Secret descriptors cannot return defaults or raw snapshot values. No settings
payload is added to the operation journal or diagnostic operation evidence.
The [wire fixture](../contracts/player-settings-v1.json) defines common shapes,
limits and the application state/reason matrix. The
[settings guide](player-settings.md) explains scopes, conflicts, CLI examples
and settings outside this registry.

Capability `plex_folders` version 1 adds `list`, `add`, `remove`, and `clear`
operations for the `plexFolderPaths` collection. Lists use consistent pages of
at most 10 entries, with up to 1000 canonical Plex roots in a saved selection.
Writes require runtime, context, and revision; the result distinguishes verified
persistence from catalogue application. Empty selection means all libraries.
See the [folder contract](../contracts/plex-folders-v1.json).

Capability `player_settings_patch` version 1 adds `get` and `set` for the finite
`command_server` and `local_http` groups. All three fields share one revision;
`set.values` contains one to three group fields. Sensitive snapshots report only
presence. A changed tuple returns `deferred/after_ack` with transition `pending`;
an unchanged tuple returns `completed` with transition `applied` only when the
live owner already matches. Otherwise it also uses `deferred/after_ack` and
`pending` with `changed:false`. Subsequent
reads can report a bounded failure or rollback reason. Results remain at most
64 KiB. The [group contract](../contracts/player-settings-patch-v1.json) defines
the exact field, storage, and transition rules. See the
[connection workflow](player-settings.md#change-a-connection-as-one-transaction).

`profiles` accepts exactly `{}`. For the active M3U provider it returns
`{provider:"m3u",profiles:[{number,name,active,history_hours,playlist_configured}]}`:
exactly 15 ordered slots numbered 1–15, with one active slot. History is an
integer 0–8760 or `null` for an invalid legacy value. Configuration flags are
booleans; URLs and credentials are not returned.

`profile` accepts exactly `{number:N}` and returns
`{provider:"m3u",profile:METADATA,dispatched:true}` for the selected, active slot.
`profile_settings` accepts exactly `{number:N,settings:{...}}`, with a nonempty
M3U allowlist of `name`, `playlist` and `history_hours`. Numbers must be
JSON integers: slot 1–15, history 0–8760. Strings must be valid Unicode without
C0/DEL controls, limited to 256 UTF-8 bytes for names or 8192 for playlist URLs.
Empty strings clear fields. Unknown/duplicate fields and malformed settings are
rejected; the 16 KiB request envelope limit still applies. The player validates
and applies all settings together, returning
`{provider:"m3u",profile:METADATA,saved:true}`. A saved result does not establish
that provider loading completed. These operations require the active M3U driver
and remain subject to parental and platform policy.

The shared request schema also accepts `vportal` for the standalone VPortal
provider, whose profile settings are `name` and `vportal`. The active player
driver enforces the provider-specific fields: M3U rejects `vportal`, and its
metadata omits `vportal_configured`. Legacy M3U portal links are not used or
deleted. Update controller, CLI and player together for this contract.

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

### Player standby and wake

Player standby uses the existing lifecycle request. For example, submit
`{"action":"lifecycle","params":{"operation":"standby"}}` to enter standby
and `{"action":"lifecycle","params":{"operation":"wake"}}` to leave it.
The CLI command `ott PLAYER go` sends `wake`; `go`, `start` and `stop` are CLI
aliases, not additional wire operations or operator permissions.

Read `capabilities.lifecycle` for the operations supported in the current
player state. The usual accepted response contains `operation`,
`accepted: true`, `dispatched: false` and `effect: "lifecycle-after-ack"`.
It confirms acceptance before the effect, not completed media teardown or
playback startup. Inspect player status after the effect to observe its state.
Standby retains remote control; it does not power off the device or provide
Wake-on-LAN. Runtime and host background work must be distinguished from
playback traffic when measuring standby.

Updated player status can include `player.standby` (boolean) and
`player.background` with exactly `state` and `pending`. States are `running`,
`suspending`, `suspended`, `unsupported` or `error`; pending is a nonnegative
integer at most 2147483647, or null when unknown. The fields are optional for
older players. Interpret standby on, background suspended and zero pending
together as the player's report of drained managed work, not an external
measurement of all device traffic. Capability identity snapshots can change
activity while a status request is in flight; only version, platform and runtime
identify the responding player. The CLI displays the status activity snapshot
and validates capabilities separately.

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

### Media categories and bound channel pages

This additive extension is advertised by compatible Plex and standalone
VPortal players:

```json
{"categories":{"version":1,"operations":["list","select"],"maxPageSize":200}}
```

Read `runtime` from `capabilities.player.runtime`. Category operations use
the authenticated request route and retain local player admission controls.
Category selection changes browsing only: it does not start playback or
replace the currently playing category or explicit queue.

Scoped operators need `capabilities` for CLI discovery, `categories.list`
for category reads, and `channels` for video pages. Grant
`categories.select` separately for browsing changes and `play_catalog` for
stable-ID playback. The CLI now discovers capabilities before legacy `s`
and title searches too; update existing operator grants accordingly. An
authorization failure does not trigger a fallback to another protocol.

```json
{"action":"categories","params":{"op":"list","runtime":"page-123","offset":0,"limit":200}}
{"action":"categories","params":{"op":"list","runtime":"page-123","catalog":"CATEGORY_RECEIPT","offset":200,"limit":200}}
{"action":"categories","params":{"op":"select","runtime":"page-123","catalog":"CATEGORY_RECEIPT","category":"CATEGORY_ID"}}
{"action":"channels","params":{"runtime":"page-123","catalog":"CATEGORY_RECEIPT","category":"CATEGORY_ID","offset":0,"limit":200,"search":"picnic"}}
{"action":"channels","params":{"runtime":"page-123","catalog":"CHANNEL_RECEIPT","category":"CATEGORY_ID","offset":200,"limit":200,"search":"picnic"}}
{"action":"play_catalog","params":{"catalog":"CHANNEL_RECEIPT","id":"VIDEO_ID"}}
```

`categories list` accepts exactly `op`, `runtime`, `offset`, `limit` and an
optional `catalog` on the first page; subsequent pages require the receipt.
Its successful data is
`{version:1,runtime,op:"list",catalog,provider,selected,offset,total,has_more,loading,categories:[{id,name,count}]}`.
`selected` is always a stable category ID. The external ID `all` denotes
the player's virtual All category, independent of its translated label.
Category names are display text, not identity; videos may belong to several
categories while All is deduplicated.

`categories select` accepts exactly `op`, `runtime`, `catalog`, `category`.
Success is
`{version:1,runtime,op:"select",catalog,provider,selected,dispatched:true}`.
A successful selection receipt confirms the browsing mutation, not playback.
Read-only listing remains available in kiosk mode; mutations still require
the player's permission.

Modern `channels` requires `runtime`, `catalog`, `category`, `offset`, `limit`
and accepts optional `search`. Data is
`{version:1,runtime,catalog,category,offset,total,has_more,loading,channels:[{id,name,number}]}`.
The first page exchanges a category-list receipt for a category/search-bound
channel receipt. Use that returned receipt on every subsequent page and on
`play_catalog`. Preserve the same category and search. `number` remains the
one-based number in the full provider catalogue, not the filtered page or
category position. `id` is an opaque stable video ID and need not be numeric.
Legacy `channels {}` and `channels {search}` responses are unchanged.

Page limits are integers from 1 to 200; returned pages contain at most that
many rows. Category and paged-channel result data is limited to 1 MiB.
Offsets, totals and category counts are nonnegative integers
at most 1,000,000. IDs are unique within a page; `has_more` must agree with
the offset, returned row count and total. Names are at most 512 UTF-8 bytes,
category IDs 1,024 bytes and video IDs 2,048 bytes. Receipts are opaque ASCII
tokens of at most 128 characters. Do not reconstruct them from source URLs
or expose provider credentials.

Receipts are bounded, expire after 120 seconds of inactivity and belong to a
player runtime, provider, source/profile and catalogue revision. Successful,
validated category-list and channel-page reads renew the same receipt token.
Category selection, playback and rejected access do not renew it. A category receipt cannot
be used for explicit-ID channel playback; the negotiated random selector below
can use the category-list receipt for All. A reload, source change, append or
membership/order edit invalidates a prior snapshot. Ordinary browsing does
not. Pagination reports a fixed loaded snapshot; `loading:true` separately
signals that provider discovery is incomplete. Clients must not combine
pages across receipts or treat a partial title search as complete.

Rejected `categories` and paged `channels` requests return
`{version:1,runtime,op:"list"|"select"|"channels",state:"error",error:CODE}`
with a code from `invalid_request`, `runtime_mismatch`, `catalog_changed`, `catalog_incomplete`,
`category_not_found`, `restricted`, `unsupported`, `unavailable`. The
controller validates the requested runtime and operation, and echoed receipt
metadata in successful results. The player checks source freshness and
category membership before selection or playback. `play_catalog` retains
its existing success and rejection envelopes. A rejected or
uncertain playback must not be repeated automatically or downgraded to a
numeric switch. Existing peers may return the legacy unsupported envelope;
clients require the capability before using this extension.

### Player-owned random selection

Compatible players advertise `"random":{"version":1}` in `capabilities`.
The controller accepts an additive `play_catalog` union: either the existing
`{catalog,id}` or `{catalog,random:{...}}`, never both. Check the capability
before sending a random request; an old player/controller must fail closed,
without retrying through a legacy random command or numeric playback.

```json
{"action":"play_catalog","params":{"catalog":"SOURCE_RECEIPT","random":{}}}
{"action":"play_catalog","params":{"catalog":"SOURCE_RECEIPT","random":{"range":[1,1000]}}}
{"action":"play_catalog","params":{"catalog":"SOURCE_RECEIPT","random":{"search":"News"}}}
```

Use the complete `epg_catalog` receipt for legacy catalogues, or a
`categories.list` receipt to select from All without downloading every media
page. A category/search-bound channel receipt restricts selection to its
bound catalogue. A loading catalogue is not complete and cannot be used.
Existing runtime, provider, source and catalogue-revision fences still apply.
When switching provider first, re-read capabilities after it becomes ready:
category support can differ between the source and destination providers.

`search` is optional, nonempty text of at most 1,024 UTF-8 bytes without control
characters. A matching full name takes priority over substring matches.
`range` is an optional inclusive ordered pair of positive JavaScript-safe
integers. Without a range, selection gives eligible global numbers 1–1000
an 80% chance and the remaining numbers 20%, uniformly within each pool;
an empty pool gives its chance to the other. An explicit range instead samples
uniformly within that range and never falls back outside it.

The player owns eligibility: random selection excludes AceStream names and
applies the configured selection restrictions, including during an active local
access session. It checks eligibility and local playback admission again before dispatch.
Manual exact-ID playback retains its
existing policy. An empty eligible set is rejected without starting a channel.

Success is exactly `{dispatched:true,channel:{id,number,name}}`; `id` is a
nonempty string of at most 2,048 UTF-8 bytes, `name` at most 16,384 bytes and
`number` a positive JavaScript-safe integer. IDs and names exclude control
characters. The controller validates this bounded result, including any
requested range. It confirms dispatch, not visible playback. Rejection or an
uncertain response is never automatically replayed. Requests retain ordinary
`play_catalog` ownership, operator grants, queue and expiry rules. Shared wire
fixtures live in [`contracts/random-v1.json`](../contracts/random-v1.json).

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
`capabilities.player.runtime`. Only `play` and `preview` accept `ids`: 1–500
strings matching `^[1-9][0-9]{0,19}$`. Their order and duplicates are preserved.
Unknown/duplicate fields, numeric JSON IDs, leading zeros, URLs and extra options
are rejected. The player uses its saved Plex credentials; no URL/token is accepted
in this request. The existing 16 KiB request limit remains.

Capabilities optionally include
`plex_queue:{version:1,operations:["play","preview","status","next","previous","stop"],max_items:500}`.
Clients also accept smaller advertised limits and reject oversized requests.
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
and the total serialized preview response data is at most 1 MiB. Preview checks availability
without selecting a provider, starting playback or replacing a retained queue.
Readiness does not promise future network availability or successful decoding.

While a queue is retained, `playback` operations `next_channel` and
`previous_channel` route to it atomically. Their successful result is
`{operation,dispatched:true,plex_queue:QUEUE_METADATA}` instead of `channel`.
The controller rejects a result containing both destinations. At either queue
boundary the step is rejected without wrapping or falling back to TV. Outside
the queue, the existing channel behavior remains. Natural end-of-stream advances
the ordered list and stops after its final item unless existing kiosk policy
repeats it. Shuffle is represented by the submitted ID order, not a queue option.

The player checks request expiry, runtime and context before committing prepared
playback and retires stale callbacks. CLI timeout does not cancel a request already
accepted under the server TTL; late execution before that deadline can be uncertain.
Never automatically reissue a mutation on timeout. Read `plex status` to inspect
the current queue, and use a new request only after assessing that state.

### Plex library extension

`plex_library` is a separate administrator-authenticated RPC. It resolves library
sections using the player's saved Plex credentials and reuses its existing queue
implementation. It accepts no URL, token, preset or filesystem path. It neither
changes the legacy command contract nor extends the `plex_queue` request schema.

```json
{"plex_library":{"version":1,"operations":["list","preview","play"],"max_items":500}}
{"action":"plex_library","params":{"runtime":"page-123","op":"list"}}
{"action":"plex_library","params":{"runtime":"page-123","op":"preview","query":"Tom and Jerry"}}
{"action":"plex_library","params":{"runtime":"page-123","op":"play","library_id":"16","shuffle":true}}
{"plex_library_truncation":{"version":1}}
{"action":"plex_library","params":{"runtime":"page-123","op":"play","library_id":"16","shuffle":true,"allow_truncate":true}}
```

The optional capability has exactly `version`, `operations` and `max_items`.
Operations are a distinct subset of `list`, `preview`, `play`; an absent
capability or missing operation is unsupported. `max_items` is an integer from
1 to 500. The runtime format and ownership rules are the same as `plex_queue`.
`list` accepts only `runtime` and `op`. `preview`/`play` require exactly one of
`query` (nonblank, at most 256 UTF-8 bytes without C0/DEL controls) or `library_id`
(a string matching `^[1-9][0-9]{0,19}$`). Optional `shuffle` is a boolean,
default false. Unknown or duplicate fields and malformed UTF-16 escapes fail.

An additional top-level capability `plex_library_truncation` has exactly
`{"version":1}` and requires the sibling `plex_library.max_items` to be 500.
It leaves the existing `plex_library` object unchanged. After discovering this
capability, callers may add `allow_truncate:true` to `preview` or `play`. The
field must be literal `true` when present; `false`, `null`, other types and use
on `list` are rejected. Without this opt-in, oversized libraries retain the
`too_many_items` error and do not start a partial queue. Older controllers reject
the opt-in request before dispatch; deploy the updated controller first.

The player prefers an exact case-insensitive library-title match, then a unique
substring match; an ambiguous match fails. Only `movie` and `show` sections are
eligible. Discovery limits the complete section inventory to 1000, including
unsupported types. Show sections resolve episodes. With truncation enabled,
item discovery retains the first 500 items in library order, validates the
selected set and stops once it can establish that the limit was exceeded.
It does not scan the remainder of a larger library. Shuffle is applied to the
retained set; it is not a random sample from the whole library. Without opt-in,
item discovery still requires complete pagination before committing playback.
It does not recursively search filesystem folders
or treat a series inside another section as a library. Discovery and queue
preparation share request ownership, saved-configuration checks and expiry.

Every response, including rejected/unsupported replies, is bound to the requested
runtime and operation. Exact successful list data is:

```json
{"version":1,"runtime":"page-123","op":"list","state":"ready","libraries":[{"id":"16","title":"Tom and Jerry","type":"show"}]}
```

`libraries` contains 0–1000 rows with unique IDs. A row has exactly `id`, `title`
and `type`; title is nonblank, sanitized single-line text of at most 160 UTF-8
bytes and type is `movie` or `show`. Ready preview/play data has exactly the same
four base fields plus `library` (one row), `shuffled` (the requested boolean)
and `queue` (the existing preview or play result, respectively), with one
optional field: `truncated:true`. That field is accepted only for a request
bound to `allow_truncate:true` and a queue of exactly 500 IDs. It is omitted
when the complete library fits, including exactly 500 items; false, null and
non-boolean markers are rejected. It is never valid on list or error data.
The CLI preserves the marker in JSON and prints a limit warning in text output.
An explicit
`library_id` must match the selected library; the nested queue must have the same
runtime, 1–500 IDs, and play begins at index zero. `queue.state:error` is not a
successful library result. `ready` describes the library operation; a nested
queue in `preparing` is not confirmation that a decoder is playing.

Error data has exactly `version`, `runtime`, `op`, `state:"error"` and `error`.
Allowed errors are `ambiguous_library`, `library_not_found`, `unsupported_library`,
`too_many_libraries`, `too_many_items`, `incomplete_library`, plus the existing
bounded Plex queue error messages. Errors never contain backend text, credentials
or partial library data. Ready data is accepted only with envelope `status:"ok"`.
Serialized data is limited to 1 MiB, allowing full lists and escaped titles;
the existing 2 MiB response-envelope bound remains. Repeated responses remain
runtime/operation-bound after the first result is stored.

The CLI never retries an uncertain mutation or substitutes a local preset lookup.
Play retains parental/kiosk checks and existing repeat behavior. A new shuffle
changes the order once; it does not change repeat policy or reshuffle each loop.
Only play is journaled as `action:"plex_library"`; list/preview are read-only.
Handler completion alone does not prove an observed playback effect. See the
shared [contract fixtures](../contracts/plex-library-v1.json).

### Current-item favorites across profiles

`favorites` is an optional administrator-authenticated RPC; stored entries live
in the player, not the controller queue. It accepts no profile selection, provider
credentials, item reference or URL supplied by the controller:

```json
{"favorites":{"version":1,"operations":["list","add","play","remove"],"max_items":100}}
{"action":"favorites","params":{"runtime":"page-123","op":"list"}}
{"action":"favorites","params":{"runtime":"page-123","op":"add"}}
{"action":"favorites","params":{"runtime":"page-123","op":"add","name":"Evening cartoons"}}
{"action":"favorites","params":{"runtime":"page-123","op":"play","id":"fav-abc123"}}
{"action":"favorites","params":{"runtime":"page-123","op":"remove","query":"Evening cartoons"}}
```

The capability has exactly `version`, `operations` and `max_items`. Operations
are a distinct subset of `list`, `add`, `play`, `remove`; `max_items` is an integer
from 1 to 100. Requests include `runtime`, matching `^[a-z0-9-]{1,64}$`, and `op`.
`list` has no other fields; `add` optionally has `name`; `play`/`remove` require
exactly one of `id` or `query`. IDs match `^fav-[a-z0-9-]{1,60}$`. Name/query
are nonblank text up to 160 UTF-8 bytes, with no C0/DEL controls or malformed
Unicode. The player trims surrounding whitespace and resolves exact names
before unique substrings. Unknown/duplicate fields fail validation.

Every result is bound to the original runtime and operation, including negative
responses and repeated acknowledgements. Successful data has `version:1`,
`runtime`, `op` and these operation-specific fields:

- `list`: `state:"ready"`, `entries:[ENTRY,...]`, at most 100 unique IDs.
- `add`: `state:"ready"`, `entry:ENTRY`, `created:boolean`.
- `remove`: `state:"ready"`, `entry:ENTRY`, `removed:true`.
- `play`: `state:"dispatched"`, `entry:ENTRY`, `dispatched:true`.

A redacted `ENTRY` has exactly these fields:

```json
{"id":"fav-abc123","name":"Evening cartoons","provider":"m3u","profile_number":3,"profile_name":"Home","kind":"vod","title":"Winnie-the-Pooh"}
```

`name` is nonblank and at most 160 UTF-8 bytes; `title` is nonblank and at most
512. `profile_name` may be empty and is at most 256 bytes. These strings exclude
C0/DEL controls. `provider` matches `^[a-z0-9][a-z0-9_./-]{0,63}$`;
`profile_number` is an integer from 1 to 15, or null for a provider without
numbered profiles; `kind` is `live` or `vod`. Internal source fingerprints,
provider item references, URLs and credentials are not exposed. If a request
selected an explicit ID, the returned entry must match it.

A failure has exactly the four common fields with `state:"error"`, plus a stable
`error` code. Codes are `invalid_request`, `unsupported`, `no_current_item`,
`unsupported_item`, `profile_unavailable`, `profile_changed`, `item_unavailable`,
`ambiguous_favorite`, `favorite_not_found`, `too_many_favorites`,
`storage_unavailable`, `policy_denied`, `busy`, `timeout`, `cancelled`,
`context_changed`, `playback_failed`. There is no raw provider error text.
Successful data requires envelope status `ok`; bound error data also supports
`rejected` and `unsupported`. Invalid results do not consume the queued request.

Adding an existing source/profile/item preserves the ID; a supplied name renames
that entry, while no name keeps the existing label. Addition integrates with the
player's channel/media favorites. Removal deletes the cross-profile bookmark,
not the provider-specific favorite. Entries survive ordinary player restarts;
clearing the installation's local data removes them.

The player captures current state atomically when handling `add`; it restores
and loads the saved profile before resolving the saved item for `play`. Existing
parental/kiosk policy, original request lifetime and runtime ownership apply.
A source mismatch fails rather than using a similarly named item or a stale URL.
`dispatched` establishes a playback handoff, not physical display/audio evidence.
Only play is journaled as `action:"favorites"`; handler completion must not be
upgraded to observed playback. The CLI never automatically repeats a request.

Serialized result data is limited to 1 MiB. A complete 100-entry list fits even
with maximum-length HTML-escaped labels; the existing 2 MiB response-envelope
limit and memory limits remain unchanged. See the shared
[contract fixtures](../contracts/favorites-v1.json).

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
integer seconds. No source URLs or credentials are included. Explicit-ID `play_catalog`
accepts exactly `{catalog,id}`; both strings must be nonempty, with at most 128
UTF-8 bytes for `catalog` and 2048 for `id`. The player verifies its current
catalogue still matches before dispatching playback and returns
`{dispatched:true,channel:{id,number,name}}`. A changed catalogue is rejected.
The separate negotiated [random selector](#player-owned-random-selection)
does not alter this legacy request shape.

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

The optional [durable registry API](device-registry.md) adds explicit
administrator registration/revocation/rotation and durable approved claims.
Its guide defines generation checks, operation receipts, static conflicts and
selected HTTPS pairing aliases. Without that configuration, registration
remains static and pending pairing behavior below is unchanged.

Hosted clients may carry the device-side routes through
[`POST /api/device-transport`](device-transport.md). Its fixed allowlist includes
public discovery, pairing creation and claim/cancel; administrator listing and
approval remain available only on their original routes. The same transport
also supports device commands/results, notifications/media and runtime diagnostics.

These routes return 404 unless the optional `discovery` configuration is present.
The following configuration and response examples use illustrative addresses;
replace them with your network domain, resolver and controller URL:

```json
{
  "discovery": {
    "domain": "home.arpa",
    "nameserver": "192.168.1.1:53",
    "public_url": "https://controller.example.com/ott-control"
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
_ottplay-ctrl._tcp.home.arpa. 30 IN PTR home._ottplay-ctrl._tcp.home.arpa.
home._ottplay-ctrl._tcp.home.arpa. 30 IN SRV 0 0 443 controller.example.com.
home._ottplay-ctrl._tcp.home.arpa. 30 IN TXT "txtvers=1" "scheme=https" "path=/ott-control"
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
    "id": "home._ottplay-ctrl._tcp.home.arpa.",
    "domain": "home.arpa",
    "address": "https://controller.example.com/ott-control"
  }],
  "pairing_url": "https://controller.example.com/ott-control/api/pairings"
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
{"device_id":"registered-device-uuid","server_id":"home._ottplay-ctrl._tcp.home.arpa."}
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
  "address":"https://controller.example.com/ott-control",
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

The manifest URL and payload URL must be HTTPS endpoints that return file bytes
with status 200 without a redirect. The request's `sha256` pins the exact signed
envelope bytes. The envelope contains exactly `payload` and `signature`, both
strict base64; the Ed25519 signature covers the decoded UTF-8 manifest bytes.
The schema-1 manifest has exactly `schema`, `kind`, `version`, `package`, `url`,
`sha256`, and `size`. The package is `play.ott.kitkat`; payloads are bounded to
64 MiB; standalone APKs in the 0.1.2 agent are limited to 60 MiB. Existing
agents accept `agent` (raw ARM ELF) and `apk` (wrapper APK). The 0.1.2 APK-only
path requires an enabled, foreground OTT app and a candidate embedded helper
identical to the installed system helper; changed helpers require a bundle.
The 0.1.2 agent additionally accepts `bundle`, a ZIP with exactly `app.apk`
and `ott-touch-guard`; the helper must match the APK's embedded helper bytes.
There are no caller-supplied installation paths or commands. Bundle installation
requires an enabled, foreground, responsive OTT app and a compatible existing
APK/helper pair. An older agent must first receive an ordinary agent update.
See the [native update contract](../native/android-agent/README.md#signed-updates-over-wi-fi)
for component compatibility, coordinated updates and rollout order.

An update response with `accepted`/`completion: inspect_status` records
admission, not successful installation. Self-replacement may interrupt operation
completion recording. Read the operation history and fresh native health/version
evidence before deciding the outcome; do not blindly resend an uncertain update.
The signed version label alone is not evidence of the running binary's version.
The controller does not download, sign or install these artifacts, and upgrading
the controller does not upgrade an already installed agent.

Native health from the 0.1.2 agent optionally includes `updates` with schema
`version: 1` and `agent`, `apk`, `touch_guard` objects. Each has `state:
ready|unavailable`; ready components include fresh lowercase `sha256` evidence.
`agent.version` is the running agent version. The APK object fixes `package` to
`play.ott.kitkat` and, when readable, adds `version_name`, positive integer
`version_code` and `app_state: enabled|disabled|unavailable`. Unavailable evidence
uses a bounded `reason` and must not be interpreted as absence or successful
rollback. This metadata contains no filesystem paths, binaries or package dumps.
`updates.bundle_state` is `absent|pending|committed|rolled_back|unavailable`.
Boolean `updates.recovery_failed` reports a failed automatic pair recovery.
When true, capabilities, health/log reads and a signed agent-only repair update
remain available; app mutations, APK/bundle updates and the watchdog are blocked.

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

Players supporting attached-engine observations retain `debug:{"version":1}`
and additionally advertise `debug_versions:[1,2]`. Explicitly request them with
the same three parameters and `version:2`. This version is supported only for
the `debug` section. Both the response envelope and `data.version` must equal 2;
the controller rejects a downgraded response, including a negative response with
the wrong version. The exact version 2 schema adds one required key to each
media lane: `engine`, limited to `"hls.js"`, `"shaka"`, `"html5"`, or null.
All other fields, bounds, runtime binding and allowlists remain identical.

Default requests still use version 1 and cannot carry `engine`. An updated
player therefore remains compatible with earlier controllers and CLIs.
`ott PLAYER debug --engine` opts in to version 2, or version 3 when the separate
`debug_attempts:{"version":3}` capability is present. Unsupported players are not
queried and unsupported controllers cause a clear failure without a downgraded
retry. An attached engine is not evidence of decoded frames or physical output.

Version 3 keeps the same 16 KiB snapshot limit, envelope/runtime binding and
version 2 fields. Each media lane additionally requires `attempt` and
`lastAttempt`. Both are nullable; a missing owner produces null. A current
attempt contains exactly `attachment`, `engineStarts`, `fatalErrors`,
`nativeFallbacks`, and `streamRetries`, all integers from 0 through 9007199254740991.
A retired attempt adds required `handleId` (the same integer bounds),
`generation` (nullable integer), and `reason` (`ended`, `disposed`, or `suspended`).
Unknown fields, incomplete objects and out-of-range counters are rejected.
Current and retired attempt identities must be considered separately; counter
changes across replacement handles do not measure retries of one stream.

The new marker requires the existing `debug:{"version":1}` and
`debug_versions:[1,2]` capabilities. Keep that legacy array unchanged: existing
CLIs require its exact shape and safely ignore unknown top-level markers.
Deploy controller support before enabling the new player/CLI combination.
The shared [version 3 fixture](../contracts/runtime-debug-v3.json) defines the
bounded extension. Negative replies must also retain requested version 3 and
the exact runtime; no error permits silent retry with another schema.

## Scoped ordinary command credentials

The additive `perf_capture` action has exact `start`, `status`, `read`, and `stop`
operations, runtime/session binding, and independent scoped permissions such as
`perf_capture.read`. Its [wire contract](../contracts/perf-capture-v1.json) defines
the finite event schemas, TTL, ring and response limits; the
[capture runbook](performance-capture.md) covers export and guarded trials.
Advertising the capability does not start recording.

`operators` optionally authorizes exact device/action pairs for ordinary RPC and
legacy command submission. Composite RPCs require an operation-qualified action
such as `playback.next_channel`, `input.channel_up` or `command.set_volume`.
Operators read only their own permitted RPC receipts. See the [scoped command
operator configuration](security-design.md#scoped-command-operators).

## Durable operation outcomes

The optional [`journal` configuration](reliable-control.md#enable-bounded-durable-outcomes)
enables `GET /api/operations?device_id=ID[&key=KEY]`, authorized for the
administrator and ordinary scoped command operators. Device tokens cannot read
this route. Without a key the response describes enabled state, retention,
capacity and command TTL. With a key it contains `id`, `state`, `result_status`,
`expires_at`, `command_expires_at` and `action`. Timestamps are Unix seconds.
Journal deadlines round upward to the next whole second so retention never ends
before a queue entry's precise deadline.
Missing/expired keys return 404, disabled journals 503 and denied scopes 403.

RPC POST accepts an optional `idempotency_key` string (16–80 ASCII letters,
digits, underscores or hyphens). Legacy POST accepts `Idempotency-Key`.
Reusing an owner/key for changed device/action/payload returns 409. An identical
operation returns 202 with `{status:"retained",id,state}` and never enqueues a
second command while retained. This does not return a second copy of the result
body. Original RPC receipts remain in memory for 60 seconds. Pending journal
records become `unknown` on restart and no commands are replayed.

The forward-compatible version-1 journal reader retains bounded unfamiliar
operation names as audit metadata. When the running server does not implement a
saved operation, this endpoint reports `state: "unknown"` and an empty
`result_status`; the original transport audit remains on disk. This does not
enable that action, widen operator permissions or authorize replay. Existing
ownership/device checks, retention and malformed-record rejection remain in
effect. See the [upgrade and rollback procedure](reliable-control.md#upgrade-and-rollback-compatibility).

## Exact settings comparison

`settings_compare` reads saved values without selecting a provider:

- M3U: `{provider:"m3u",number:1,settings:{name,playlist,history_hours}}`;
  all three settings fields are required; saved legacy VPortal links are ignored.
- Plex: `{provider:"plex",settings:{server,token}}`.
- Stalker: `{provider:"stalker",number:1,settings:{name,server,mac}}`.
- VPortal: `{provider:"vportal",number:1,settings:{vportal,name?},runtime}`;
  this compares the dedicated VPortal slot, not retired M3U shadow fields.

The result data is `{provider,number?,matched}` with a boolean `matched` and no
stored values. VPortal additionally returns `runtime`, opaque `context` and
`revision` for a conditional save. Slots range from 1 to 15. Existing player parental/settings policy
still applies. The request carries expected values, so treat its body as private.

## Explicit playback handoff

`playback_handoff_export` accepts `{}` and returns `{handoff}`.
`playback_handoff_import` accepts `{handoff}` and returns
`{dispatched:true,kind:"live"|"archive"}`. The handoff has exact fields:

```json
{"version":1,"provider":"m3u","source_id":"opaque-source-identity","item_id":"channel-v1:1234abcd.5678abcd","kind":"live"}
```

Providers are `m3u`, `xtream`, `stalker`. Source identifiers are nonempty
text bounded to 256 UTF-8 bytes. Item identifiers must match
`channel-v1:[0-9a-f]{8}\.[0-9a-f]{8}`: a player-produced opaque fingerprint,
never a raw provider ID or stream URL. Archive adds `archive_start`, a positive
safe integer Unix timestamp incorporating the source's current seek offset.
Live forbids that field. Additional fields, URLs and credential fields are not
part of the schema. The destination requires exact source identity and a unique
item match, and resolves its own stream address. The source is not stopped.

Negative result data is a single `error` code: `unsupported`, `invalid_request`,
`no_current_item`, `source_mismatch`, `item_unavailable`, `ambiguous_item`,
`policy_denied`, `busy`, `playback_failed`, or `context_changed`.
See the [workflow and limitations](reliable-control.md#continue-on-another-player).


### Local source connection and playback

The authenticated `local_media` RPC selects an exact source on the target player.
It does not interpret a path on the controller host or upload media. The player
advertises the supported operations and native path capability:

```json
{"local_media":{"version":1,"operations":["sources","play","shuffle","connect"],"absolute_paths":true,"max_sources":32,"max_items":50000}}
{"action":"local_media","params":{"runtime":"page-123","op":"sources"}}
{"action":"local_media","params":{"runtime":"page-123","op":"sources","include_paths":true}}
{"action":"local_media","params":{"runtime":"page-123","op":"sources","path":"/var/media/Movies","include_paths":true}}
{"action":"local_media","params":{"runtime":"page-123","op":"shuffle","source":"Movies","session":true}}
{"action":"local_media","params":{"runtime":"page-123","op":"connect","source":"/Volumes/data/Movies/"}}
```

`source` is a connected source ID/name or an absolute native path, up to 4,096
UTF-8 bytes. Names are exact, bounded to 512 bytes, and cannot contain directory
separators or control characters. Literal colons, as in `Kids: Cartoons`, are
accepted; the name is not interpreted as a URI. Relative paths and URL selectors
are rejected. The native player controls filesystem binding; browser clients
retain their existing permission
model. `connect` does not start playback. `play` and `shuffle` select only the
resolved source, with no fallback to the whole local library. `shuffle` creates
one randomized order, subject to the player's ordinary repeat policy.

For `sources` only, an optional absolute `path` (up to 4,096 UTF-8 bytes) filters
the result using the native player's canonical filesystem identity. It requires
`absolute_paths:true`, does not create a binding, and does not imply
`include_paths:true`. A readable unconnected directory returns `sources:[]`;
invalid or unreadable paths return a correlated error. A returned canonical
source path may differ from the requested spelling, including OS path aliases.

Successful responses contain `version:1`, the requested `runtime` and `op`, and
`state:"ready"`. `sources` adds an array of at most 32 entries with `id`, `name`,
`kind` (`folder` or `file`) and boolean `available`. An optional absolute `path`
is allowed only when the request explicitly sets `include_paths:true`; players
expose this only with `absolute_paths:true`. Other successful operations return
one `source` without its private path, integer `count`, boolean `shuffled`,
`accepted:true` and `playback_confirmed:false`. `count` is 1–50,000 for playback
or 0–50,000 for connection. `shuffled` is true only for the `shuffle` operation.
Negotiated session playback additionally requires `session:true`, as described
below. Connection responses retain their existing shape. An accepted response
is not playback evidence.

#### Session-only Local playback negotiation

The existing `local_media` capability keeps exactly its five fields. Updated
players advertise session-only playback in a separate sibling capability;
progressive support is independent:

```json
{"local_media_session":{"version":1},"local_media_progressive":{"version":1}}
```

The new CLI sends this read-only combined controller probe before every Local
`play` or `shuffle`, including the `offline` alias and each target in a group:

```json
{"action":"capabilities","params":{"local_media_session":true,"local_media_progressive":true}}
```

A supporting controller accepts exactly `{}`, `{"local_media_session":true}`,
`{"local_media_progressive":true}`, or both flags set to literal `true`.
It computes idempotency identity from the original parameters before consuming
the probe flags and forwarding `capabilities` with `{}` to the player. False,
null, numeric substitutes and additional fields are invalid.

A successful probe proves controller support; the returned player capability
must separately include `local_media_session:{"version":1}`. Controller beta22
predates the session probe and rejects it with HTTP 400 before allocating or
queuing a request. The new CLI stops on that rejection or a missing player
session marker; it never falls back to an unflagged Local mutation. Timeouts,
receipt failures, HTTP 5xx and player rejections also do not establish support.
The probe and command share one per-target timeout budget. `sources`, `connect`
and preset folder bindings continue using their existing capability checks.

After the handshake, playback requests require `session:true`:

```json
{"action":"local_media","params":{"runtime":"page-123","op":"shuffle","source":"Movies","session":true}}
{"version":1,"runtime":"page-123","op":"shuffle","state":"ready","source":{"id":"source-123","name":"Movies","kind":"folder","available":true},"count":3,"shuffled":true,"accepted":true,"playback_confirmed":false,"session":true}
```

`session` is allowed only as literal `true` on `play`/`shuffle`; it is invalid
on `sources`/`connect`. A successful response must echo `session:true`: the
complete result has the original nine fields plus `session` (ten fields).
A missing, false, null or unsolicited session acknowledgement is rejected;
the client does not resend playback or downgrade an uncertain result. Error
results keep the original five fields, without `session` or `scanning`.

Session playback keeps the selected provider/source, order, shuffle seed,
cursor and resume position in memory. It does not replace saved provider,
profile or startup settings, including through deferred saves, autosave or
export. Reloading ends that session and follows the saved startup configuration.
Explicit provider/Local/kiosk selection in the player UI can create persistent
configuration. Native source connections and OS/browser access grants are
separate from playback persistence and are not undone when a session ends.

Updated players use the transient implementation for all remote Local play and
shuffle, including requests from older CLIs. Without the `session` flag they
return the legacy acknowledgement shape, preserving strict older validators.
An older CLI/player combination has no session guarantee. The new CLI requires
the handshake so it can establish this behavior instead of relying on version
labels. See [the session contract](../contracts/local-media-session-v1.json) for
complete, progressive and error examples. Update CLI, controller and player
together; documentation or a CLI installation alone does not update a player.

#### Progressive Local playback negotiation

For the new CLI, session support is required and progressive support is optional.
Only a successful combined probe **and** a valid player
`local_media_progressive:{"version":1}` marker allow `progressive:true` in
addition to `session:true`:

```json
{"action":"local_media","params":{"runtime":"page-123","op":"shuffle","source":"Movies","session":true,"progressive":true}}
```

If the selected source is still scanning, an accepted result may add
`"scanning":true` to the ten session-result fields (eleven fields total).
Its `count` is the number of playable
items discovered so far, from 1 through the advertised limit; it is not a final
total. The response still uses `state:"ready"`, `accepted:true` and
`playback_confirmed:false`. A completed scan returns the ten-field session result
without `scanning`, even when the request opted in. The request does not stream
later counts through the controller. A session-capable player without the
progressive marker uses a complete selected-source scan.

`progressive` is allowed only as the literal boolean `true` on `play` or
`shuffle`; it is invalid on `sources` and `connect`. `scanning` is allowed only
as `true` on an opted-in successful playback result. `false`, `null`, numeric
substitutes and unsolicited fields are rejected. Error results retain their
original five fields. Legacy clients can still use the progressive-only probe
and request: without `session:true`, their complete acknowledgement has nine
fields and their partial acknowledgement adds only `scanning:true` (ten).
This preserves the legacy schema; it does not prove session support on an older
player. Negotiation is independent for each player in a comma-separated group.

Rejected responses retain `version`, `runtime` and `op`, with `state:"error"` and
a fixed error identifier. See [the shared contract](../contracts/local-media-v1.json)
for the allowlist and examples. Runtime mismatches, extra fields, malformed source
metadata and unsolicited path disclosures are rejected without consuming the
pending request. Operator credentials need an exact `local_media.sources`,
`local_media.connect`, `local_media.play` or `local_media.shuffle` scope. There is
no family-wide scope or automatic retry of uncertain mutations.

### Local videos profile transfer

The `offline` provider uses the same `profiles {}` listing and `profile {number}`
selector, with 15 slots. Its profile metadata is
`{number,name,active,configured,binding_required,source_count,selected_name}`;
`selected_name` is empty when no file is selected. A profile load returns
`{provider:"offline",profile:METADATA,dispatched:true,binding_required:BOOL}`.
Dispatch does not confirm playback. Missing local access requires interactive
binding/permission on the player, and a missing selected file must not select a
substitute.

New requests (exact keys, integer slot 1–15):

```json
{"action":"profile_save","params":{"number":1,"name":"Family movies"}}
{"action":"profile_export","params":{"number":1}}
{"action":"profile_import","params":{"number":2,"profile":{"version":1,"provider":"offline","name":"Family movies","binding_id":"c98c115d-1234-4567-8abc-123456789abc","sources":[{"key":"s0","name":"Movies","kind":"folder"}],"selected":{"source_key":"s0","name":"Cartoons/movie.mp4"}}}}
```

`profile_save` captures current local sources/selection; `name` is optional.
`profile_import` saves into an inactive slot. Neither starts playback. Both return
`{provider:"offline",profile:METADATA,saved:true,binding_required:BOOL}`.
`profile_export` returns `{provider:"offline",number:N,profile:DOCUMENT}`.
Negative replies contain only `{error:TEXT}` (nonempty, at most 1024 UTF-8 bytes).
New action replies are bounded to 16 KiB, validated against their request slot,
and rejected before queue consumption if invalid. Operator scopes are the exact
independent action names `profile_save`, `profile_export`, `profile_import`;
export permission does not grant import/save permission.

DOCUMENT accepts exactly `version`, `provider`, `name`, `binding_id`, `sources`,
`selected`: version 1, provider `offline`, name up to 256 UTF-8 bytes, canonical
lowercase UUIDv4 binding ID. Sources are 0–32 exact `{key,name,kind}` records with
consecutive keys `s0`…`s31`, a nonempty basename up to 512 UTF-8 bytes, and kind
`file` or `folder`. `selected` is null or exact `{source_key,name}` referencing an
included source and a nonempty relative filename up to 1024 UTF-8 bytes. Strings
exclude control characters; source/selection names exclude backslashes, URI
schemes, absolute paths and `.`/`..` path segments. The compact document is at most
12288 bytes and the full request at most 16 KiB. Duplicate and unknown fields are
rejected recursively. Empty sources require null selection.

The opaque binding ID resolves only an exact configuration in an installation's
private binding map. It is not an access credential. Cross-device import transfers
configuration only; it never carries media bytes, paths, native source IDs,
bookmarks, grants or browser handles. Reusing local access requires the matching
local binding; otherwise the player reports `binding_required` and provides a
local binding flow.

While Local videos is active and installed, capabilities may include
`offline_profiles:{version:1,operations:["list","load","save","export","import"],max_profiles:15,max_sources:32}`.
Operations are filtered by current player policy (a locked configuration may
advertise only `list`). Absence means the extension is unavailable. This
extension is separate from the provider-specific M3U/VPortal settings contracts
described above.
