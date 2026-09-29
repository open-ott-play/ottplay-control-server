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
the shortest record TTL. Failures have a two-second discovery cache.

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

An empty DNS service set returns `servers: []`. DNS/configuration failures return
503. Bootstrap responses have `Cache-Control: no-store`. Existing exact CORS
origins apply; `allow_null_origin` additionally permits null origins on device
bootstrap routes, but never on the administrator listing or approval route.
CORS preflights permit only Authorization and Content-Type headers. Public
bootstrap traffic and pairing creation have separate bounded rate limits.

### Creating a pairing request

`POST /api/pairings`, Content-Type application/json:

```json
{"device_id":"registered-device-uuid","server_id":"home._ottplay-ctrl._tcp.alvit.cf."}
```

`server_id` is optional only when exactly one valid server is discovered. The
selected server must match a freshly discovered descriptor and its normalized
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
A fresh DNS check precedes approval; a changed or unavailable descriptor
invalidates the request and returns 409.

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
credentials return 401; changed or unavailable DNS invalidates the request and
returns 409. The client stores the device credential locally, discards the
pairing secret, and enables normal outbound command polling only after the user
completes pairing. Existing manually configured players require no re-pairing.

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
