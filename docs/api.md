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

The current player polls about once per second, with timeouts and bounded backoff after errors. It deduplicates repeated IDs before dispatch, retries failed acknowledgements, and invalidates responses when connection settings change. Channel commands can wait for the current channel list to load. Acknowledgement indicates that the client handled or rejected the command; it is not confirmation of picture, audio, PIN approval, or completed provider loading. The server does not currently expose execution-result receipts.

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
