# Hosted device transport

`POST /api/device-transport` carries an existing device request inside a JSON
object. Hosted bridges can use it when they accept JSON POST requests but cannot
forward arbitrary methods or an Authorization header. Direct clients can keep
using the original routes. The bridge must point to one fixed, trusted controller.
Dispatch stays inside that controller process; no client-supplied URL is fetched.
This feature does not register devices, enable diagnostics or approve pairing.

## Request

Send Content-Type `application/json` with one UTF-8 object:

```json
{
  "version": 1,
  "method": "GET",
  "path": "/api/webhook/commands?delivery=ack",
  "token": "THE_EXISTING_DEVICE_TOKEN"
}
```

For POST, add an object-valued `body`, not a JSON string:

```json
{
  "version": 1,
  "method": "POST",
  "path": "/api/webhook/commands/ack",
  "token": "THE_EXISTING_DEVICE_TOKEN",
  "body": {"ids": ["0123456789abcdef0123456789abcdef"]}
}
```

- `version` is the integer 1; `method` is uppercase GET, POST or DELETE.
- `path` is an exact allowed path with optional query, at most 2,048 UTF-8 bytes.
  Schemes, hosts, fragments, trailing slashes, encoded paths and traversal fail.
- Optional `token` is the existing 32–256 character URL-safe credential without
  `Bearer `. Omit it for public discovery/pairing creation. Protected routes still
  reject missing credentials.
- `body` is required for POST and forbidden for GET/DELETE. Original nested JSON
  bytes pass to the destination validator; duplicate nested keys are not erased.
- Optional `headers` contains exactly one `X-OTT-Diagnostics-Epoch` string using
  the existing 1–80 character diagnostic-label grammar, on diagnostic routes only.

Unknown/duplicate envelope fields, explicit nulls and other headers are rejected.
Keep credentials out of URLs, outer Authorization headers, logs and analytics.
The outer URL has no query. Transfer-Encoding and Expect framing are rejected.
Known Content-Length must match the bytes read.

## Allowed routes

All paths are exact. Other method/path combinations fail before dispatch.

| Method | Path | Credential |
| --- | --- | --- |
| GET | `/api/webhook/commands`, `/webhook/poll` | Device |
| POST | `/api/webhook/commands/ack`, `/api/responses` | Device |
| GET | `/api/notifications`, `/api/notifications/media` | Device |
| GET | `/api/discovery` | Public; discovery configuration required |
| POST | `/api/pairings` | Public; discovery configuration required |
| GET, DELETE | `/api/pairings?id=PAIRING_ID` | Pairing claim secret |
| POST | `/api/v2/diagnostics/runtimes` | Device with diagnostics enabled |
| POST | `/api/v2/diagnostics/poll`, `/api/v2/diagnostics/results`, `/api/v2/diagnostics/events` | Runtime |
| POST | `/api/v2/diagnostics/repairs/poll`, `/api/v2/diagnostics/repairs/results` | Runtime with repair capability |

Pairing ID is one 32-character lowercase hexadecimal value, with no other query
keys. Existing handlers validate the other routes' query keys and values.
Administrator listing/approval, command submission and operator diagnostics
are not transport routes. Credential roles stay separate; wrapping a request
cannot make an administrator token act as a device token.

## Responses, origins and cancellation

The destination writes its original HTTP status, body and headers directly.
There is no response wrapper, base64 conversion or redirect. Failures, Retry-After,
WWW-Authenticate, cache policy and the diagnostic epoch header are preserved.
Notification media remains bounded application/json with data_base64 and JPEG
metadata.

The real incoming Origin is retained. Existing exact-origin CORS rules apply,
including allow_null_origin on device/bootstrap routes. Outer preflight is OPTIONS
for POST. Inner authentication, CORS and logical rate accounting execute once.
The original request context is retained; a request cancelled before dispatch
does not consume a command.

The hosted adapter must preserve status, body and required headers, and retain
the original logical controller URL for client URL checks. The physical POST URL
is a transport endpoint, not a new media origin. Timeouts remain uncertain
outcomes: follow the original acknowledgement/result protocol instead of
automatically replaying writes or draining polls.

## Bounds and deployment

The full envelope is limited to **2 MiB + 8 KiB**. Inner limits remain 16 KiB for
command acknowledgements and pairing; 2 MiB for RPC/screenshot results; 16 KiB for
diagnostic event batches; and 4 KiB for other diagnostic POST bodies. Notification
JPEG data stays limited to 160 KiB before its existing base64 JSON encoding.

Physical ingress has a separate ceiling of **1,600 × configured device count +
300 requests/minute**, above the combined normal device, four-runtime and public
bootstrap ceilings. It bounds malformed traffic without charging a logical route
twice. At most 16 small envelopes (up to 64 KiB) and one larger or unknown-length
envelope are processed concurrently. Full slots return 429 immediately; there is
no waiting queue. These limits keep large results from blocking ordinary polls.

Update the controller before enabling the hosted adapter. Configure one fixed
bridge endpoint, for example
`https://controller.example/ott-control/api/device-transport`; the envelope path
still starts at `/api/...`. Keep the existing proxy-prefix rewrite. Proxy request
limits must admit 2 MiB + 8 KiB. Disable body logging: envelopes contain credentials
and may contain private settings or screenshots. No PVC/configuration changes
are required for this transport-only feature.

## Troubleshooting

- **404 on outer URL:** update the controller and check the proxy prefix.
- **400:** check exact path/method, object-valued body, field names and absence of
  an outer Authorization header.
- **401/403 from destination:** use the correct device/runtime/claim credential
  and retain existing origin and per-device diagnostic policies.
- **413:** check both envelope and destination limits; a larger proxy limit
  cannot raise the controller's limits.
- **429:** respect Retry-After when present; avoid duplicate concurrent polls.
- **Unknown UUID pairing rejected:** configure the optional
  [durable registry](device-registry.md) to permit pending unknown devices.
  An envelope alone does not grant registration rights or bypass approval.
- **Diagnostic epoch missing:** compare direct and bridged responses; correct
  header preservation before relying on runtime recovery.
- **Notification image rejected:** retain its JSON content type, original
  payload, runtime binding and expiry. Do not convert it into raw JPEG bytes.
