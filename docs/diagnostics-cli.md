# Diagnostic operator CLI and MCP

`cli/diagnostics.py` is a Python 3 standard-library client for the additive
diagnostics protocol 2. It uses a separately configured scoped operator
credential; an existing administrator or device token does not grant these
permissions. The server must have diagnostics enabled for the device. On updated
players, the enabled Remote control connection supplies local authorization;
there is no separate diagnostics permission or trust toggle. Existing `ott`
playback commands are unchanged.

## Install and prepare access

Install the complete CLI from a source checkout using the
[installation guide](cli.md#installation-and-connection). The native server
release archives contain the server, not these Python clients. Keep all five
`cli/*.py` files together; Python 3 is the only client dependency. Check the
installation without contacting a server:

```sh
ott diagnostics --help
python3 /absolute/path/to/ottplay-control-server/cli/diagnostics_mcp.py --help
```

The same commands are available as `ott diagnostics ...`, before any legacy CLI
configuration is read. Install `diagnostics.py` beside the real `ott.py` file;
symlinks such as `/usr/local/bin/ott` resolve to that directory. Install
`diagnostics_mcp.py` beside `diagnostics.py` to use the MCP adapter. This client
does not read `OTT_CONFIG`, player aliases or the administrator credential from
`cli.json`. Pass the exact configured device ID, not an `ott` nickname such as
`a1`. All connection options go **before** the subcommand. For example:

```sh
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN runtimes --device living-room
```

Use the exact HTTPS controller **base**, including any deployment prefix.
Credentials come from a named environment variable or `--token-file`, never a
token argument. On POSIX a token file must be an owned regular file with no
group/other permissions, for example mode `0600`; symlinks are rejected. Windows
uses `--token-env`: its file ACLs cannot be verified with these POSIX checks, so
`--token-file` fails before reading the file and returns `token_file_unsupported`
with environment-option guidance. Changing a Windows file to mode `0600` does
not bypass that restriction. Supply the secret through your normal private
credential mechanism. Do not save it in an MCP tool argument or checked-in config.

The server administrator must first enable `diagnostics.enabled` on the existing
device entry and create a separate operator with exact device/action scopes in
the [diagnostic server configuration](remote-diagnostics.md#enablement-and-credentials).
Validate the server configuration and restart the server through its normal
[deployment procedure](deployment.md) to apply it. A server restart invalidates
existing runtimes, sessions, repair receipts and epochs.

On macOS/Linux, this example creates a new private operator token without printing
it or overwriting an existing token. It prints only the digest to put in the
operator's `credential_sha256` field:

```sh
python3 - <<'PY'
import hashlib
import os
from pathlib import Path
import secrets

directory = Path.home() / ".config" / "ottplay-control"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
token = secrets.token_urlsafe(32)
path = directory / "diagnostics-token"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="ascii") as stream:
    stream.write(token + "\n")
print(hashlib.sha256(token.encode("ascii")).hexdigest())
PY
ott diagnostics --server https://controller.example/ott-control \
  --token-file "$HOME/.config/ottplay-control/diagnostics-token" \
  runtimes --device living-room
```

For the remaining examples, have your credential mechanism provide
`OTT_DIAGNOSTICS_TOKEN`, or replace `--token-env OTT_DIAGNOSTICS_TOKEN` with the
POSIX `--token-file` option above. Do not configure both options.

## Connect the player and register a runtime

Registration is performed by the player; there is no operator CLI `register`
command. In **Settings → Remote control**, connect the player to the configured
HTTPS command server with its **device** access code. Enabling that connection
authorizes player diagnostics, supported screenshots and controls for that
controller, including on later startups. Connect only to a controller you trust:
screenshots may include settings, PIN screens and visible credentials. Browser
screenshots additionally need their local source picker; see
[remote screenshots](cli.md#remote-screenshots).

The player automatically registers a diagnostic runtime without another local
prompt. Reconnects and reloads create a new runtime and consent epoch; captures
and repairs still require a new operator request. Consent fields remain in the
protocol to bind operations to this connection and exact runtime. Server-side
device enablement and operator scopes are unchanged. Older players may still
need their temporary or saved local grant; update them to use connection-based
authorization. Each telemetry session still has a maximum ten-minute lease;
this bounds resource use and does not expire connection authority. See the player's
[support lifecycle and platform guide](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/remote-diagnostics.md)
for browser, LG webOS, Tauri and Capacitor behavior.

Now discover the exact registered runtime:

```sh
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN runtimes --device living-room
```

This returns JSON with `server_epoch`, exact runtime handles, capabilities and
consent metadata. Choose the exact target and use its current consent epoch.
`reported_uuid`/instance labels do not select a runtime. Record a new operation
key before the first start; keep that key and epoch if the response is lost.

## Capture, inspect and stop

```sh
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN start --device living-room \
  --runtime RUNTIME_ID --consent-epoch CONSENT_EPOCH --server-epoch SERVER_EPOCH \
  --lease-ms 600000 --idempotency-key capture-20261004-1
```

`lease-ms` is 1000–600000. The server can impose stricter limits. Start acceptance
does not establish that capture is active. Read the returned exact session ID:

```sh
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN status --session SESSION_ID
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN events --session SESSION_ID --after-seq 0 --limit 32
```

Each events call returns one bounded page. Preserve `next_seq`,
`truncated_before_seq` and `dropped_total`; a page is not a promise of complete
history. Use the returned cursor on the next call. The first implementation does
not keep polling in the background or accumulate an unbounded tail.

```sh
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN stop --session SESSION_ID \
  --server-epoch SERVER_EPOCH --idempotency-key stop-20261004-1
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN revoke --runtime RUNTIME_ID --server-epoch SERVER_EPOCH
```

Stop immediately denies new server ingestion, but queued stop is not confirmation
that the device stopped. Inspect `device_stop_confirmed`. Revoke retires the
runtime credential; the device stops on rejection or its connectivity lease.

### Deregister, disconnect or remove operator access

These actions have different lifetimes:

- `stop --session SESSION_ID` ends that capture. It preserves the runtime and
  connection authorization; it does **not** revoke an independent pending
  repair. On the player, **Stop current capture** or clicking the active capture
  indicator retires the current runtime and cancels its work, then registers
  a fresh ready runtime. It keeps the controller connection enabled. A new
  capture still requires a new operator request.
- `revoke --runtime RUNTIME_ID --server-epoch SERVER_EPOCH` retires that exact
  runtime credential and revokes pending repairs. It does not remove the saved
  device access code or another tab's runtime. This is not a permanent ban
  on device registration: an enabled connection can register a new runtime on
  reconnection. Disconnect the player or disable the device's
  diagnostics for a lasting block.
- On the player, **Disconnect** stops diagnostics and screenshots and invalidates
  pending callbacks for that connection. Changing its address/access code also
  retires the old runtime. **Connect** restores authority for the configured
  controller; there is no separate saved trust flag to remove.
- To deny diagnostics for a device at the server, set its `diagnostics.enabled`
  to `false`, validate configuration and restart the service. To remove an
  operator, remove its entry from `diagnostics.operators`; to rotate its token,
  generate a new separate token and replace `credential_sha256`. Validate and
  restart before distributing the new credential. Removing a scope narrows
  authority, but an operator entry must retain at least one device and one action;
  remove the whole entry to revoke all its access.

Closing this CLI or an MCP host is not a stop or deregistration request. Captures
remain governed by their session/connectivity leases. Runtime retirement and
diagnostic permission changes do not unregister the player's ordinary command
queue; for that separate operation see
[disconnecting or deregistering a player](cli.md#disconnect-deregister-or-revoke-a-player).

### Timeouts and uncertain outcomes

The client never retries requests automatically. A timeout or malformed/lost
mutation reply is an `unknown_outcome`, not proof of failure. Inspect session
state, or deliberately replay the **same key, fields and epoch** within the
server's idempotency retention. Do not substitute a new epoch/key after a server
restart. A missing session is not proof that the earlier operation never ran.

Requests have a total time budget (`--timeout`, default 10 seconds, maximum 30),
one worker per client and a 64 KiB response limit. HTTP, URL credentials,
redirects, ambient proxy settings, arbitrary headers and arbitrary API routes
are unavailable. Response bodies are strict JSON; output retains only allowlisted
public fields and validate the required result for each action. Event pages
preserve both the `seq` cursor and nested `event` payload. Errors expose a fixed
allowlist of controller `server_code` values, such as `consent_required` or
`runtime_busy`, alongside the HTTP status. They never print raw server bodies,
token values or exception text. A changed epoch in an HTTP error also reports
`epoch_changed`; it never triggers a retry.
Success JSON goes to stdout; error JSON to stderr with exit code 1. The client
does not persist credentials or diagnostic events.

## MCP stdio

`cli/diagnostics_mcp.py` exposes eight tools using the same client and scoped
credential: `diagnostics_runtimes`, `diagnostics_start`, `diagnostics_status`,
`diagnostics_events`, `diagnostics_stop`, `diagnostics_revoke`,
`diagnostics_repair`, `diagnostics_repair_status`. The original six tools keep
their names and argument schemas. There is no
generic HTTP/command/eval/shell tool and no credential argument in any tool.
Server scopes remain authoritative regardless of tool annotations.

## Exact-runtime repair

Repair is a separate permission from diagnostic capture. The operator needs
`repairs.start` to request an action and `repairs.read` to inspect its receipt,
scoped to the exact configured device. The selected live runtime must advertise
the `repairs` capability and report current connection consent. Discover its runtime
and consent epoch with `runtimes`; never substitute another runtime based on a
matching instance label or device token.

```sh
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN repair --device living-room \
  --runtime RUNTIME_ID --consent-epoch CONSENT_EPOCH \
  --action restart_stream --deadline-ms 10000 \
  --server-epoch SERVER_EPOCH --idempotency-key restart-20261004-1
ott diagnostics --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN repair-status --repair REPAIR_ID
```

The only actions are `restart_stream` and `reload_player`. They interrupt
playback. `--deadline-ms` is the server's bounded delivery/effect window:
1000–30000 milliseconds, default 10000. `--timeout` independently bounds each
CLI HTTP request. The create reply contains `repair_id`, `state:pending`, and
`idempotency_retention_ms`; it confirms queue acceptance, not execution.

Read `repair-status` for that exact ID. `applied` means the player invoked the
stream restart effect. `accepted` means it acknowledged reload intent; the
player waits for the result acknowledgment before invoking reload. Neither
state proves recovered playback or an observed completed reload. Other states
are `pending`, `rejected`, `unsupported`, `expired`, and `revoked`.

Record the idempotency key and epoch before sending a repair. A timeout or lost
reply has an unknown outcome, so the client never retries automatically. Read
the known receipt, or deliberately replay the same fields/key/epoch within the
reported retention horizon. Do not change the key, epoch, runtime, or action
to work around an uncertain result. A missing or expired receipt is not proof
that the effect did not run. Disconnecting the player, withdrawing connection consent, or
retiring a runtime prevents pending repair work from continuing.

The MCP repair tool requires all seven arguments: `device_id`, `runtime_id`,
`consent_epoch`, `action`, `deadline_ms`, `idempotency_key`, and `server_epoch`.
It advertises `readOnlyHint:false`, `destructiveHint:true`, and
`idempotentHint:false`. `diagnostics_repair_status` takes only `repair_id` and
is read-only. These hints do not grant server permissions or establish that
an effect completed.

## MCP configuration

Configure your MCP host to launch:

```json
{
  "command": "python3",
  "args": [
    "/absolute/path/to/ottplay-control-server/cli/diagnostics_mcp.py",
    "--server", "https://controller.example/ott-control",
    "--token-env", "OTT_DIAGNOSTICS_TOKEN"
  ]
}
```

The host must inject the named environment variable privately. On POSIX,
alternatively use `--token-file` with an absolute private file path. Windows
hosts must use the environment option. This is a stdio process, not a
public MCP HTTP endpoint, and requires no Python packages.

The adapter pins MCP **2025-11-25**: initialize, then
`notifications/initialized`, `tools/list`, `tools/call`, and ping. Newer clients
must accept that negotiated version or disconnect. It supports newline-delimited
JSON-RPC, bounded 16 KiB input frames, structured results plus a matching text
representation, protocol errors for malformed/unknown calls and `isError:true`
for execution failures. It advertises no tasks, resources, prompts, streaming or
background work. Closing stdin ends the adapter; it does not revoke an existing
capture session. Its duration remains governed by the device/server leases.

### All CLI commands and MCP arguments

The examples above cover the eight commands below. Diagnostics has no short
command aliases. Use `ott diagnostics --help` for the command list. For a
subcommand's help, include the required connection-option positions, for example
`ott diagnostics --server https://controller.example --token-env OTT_DIAGNOSTICS_TOKEN start --help`;
help itself does not read the credential or make a request.

- `runtimes --device DEVICE_ID` → `diagnostics_runtimes(device_id)`;
  requires `runtimes.read`.
- `start --device DEVICE_ID --runtime RUNTIME_ID --consent-epoch CONSENT_EPOCH
  --lease-ms 600000 --idempotency-key OPERATION_KEY --server-epoch SERVER_EPOCH`
  → `diagnostics_start(device_id, runtime_id, consent_epoch, lease_ms,
  idempotency_key, server_epoch)`; requires `sessions.start`.
- `status --session SESSION_ID` → `diagnostics_status(session_id)`;
  requires `sessions.read`.
- `events --session SESSION_ID --after-seq 0 --limit 32`
  → `diagnostics_events(session_id, after_seq=0, limit=32)`; requires `sessions.read`.
- `stop --session SESSION_ID --idempotency-key OPERATION_KEY
  --server-epoch SERVER_EPOCH`
  → `diagnostics_stop(session_id, idempotency_key, server_epoch)`;
  requires `sessions.stop`.
- `revoke --runtime RUNTIME_ID --server-epoch SERVER_EPOCH`
  → `diagnostics_revoke(runtime_id, server_epoch)`; requires `runtimes.revoke`.
- `repair --device DEVICE_ID --runtime RUNTIME_ID --consent-epoch CONSENT_EPOCH
  --action restart_stream --deadline-ms 10000 --idempotency-key OPERATION_KEY
  --server-epoch SERVER_EPOCH`
  → `diagnostics_repair(device_id, runtime_id, consent_epoch, action,
  deadline_ms, idempotency_key, server_epoch)`; requires `repairs.start`.
  The other allowed action is `reload_player`.
- `repair-status --repair REPAIR_ID` → `diagnostics_repair_status(repair_id)`;
  requires `repairs.read`.

MCP event arguments `after_seq` and `limit` are optional and default to 0 and 32.
All other MCP arguments listed here are required, including `lease_ms` and
`deadline_ms`. CLI defaults are `--lease-ms 600000`, `--after-seq 0`, `--limit 32`,
and `--deadline-ms 10000`. Event limits are 1–32; cursors are integers from 0
through 9007199254740991. Device IDs allow `[A-Za-z0-9_.:-]`, 1–128 characters;
runtime/session/repair IDs, epochs and operation keys use the same characters
with an 80-character maximum. Preserve the server-issued IDs exactly.

An MCP host sends `tools/call` with the tool name and its arguments, for example:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "diagnostics_events",
    "arguments": {"session_id": "SESSION_ID", "after_seq": 0, "limit": 32}
  }
}
```

Send this only after the initialization handshake. Tool arguments never contain
the controller address or token: the MCP process receives those at startup.

References: official MCP [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio),
and [tools/error handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Troubleshooting

- **`ott diagnostics` cannot import its module:** locate the real `ott.py`
  behind the command's symlink and reinstall all five sibling Python files from
  one checkout. The server binary alone does not install the CLI.
- **`invalid_arguments` or `invalid_input`:** put `--server`, one credential
  option and optional `--timeout` before the command. Check exact identifiers,
  numeric limits and the command reference above. Legacy `ott a1 ...` aliases
  and `--json` are not diagnostic options; diagnostics always emits JSON.
- **`invalid_token`, `token_unavailable` or `unsafe_token_file`:** use the scoped
  operator token, not the device/admin token or its digest. Check that the named
  environment variable reaches this process. On POSIX, inspect ownership and
  remove group/other permissions from a regular token file; do not use a symlink.
  For `token_file_unsupported` on Windows, use `--token-env` instead.
- **`invalid_server`, `redirect_refused` or `transport_error`:** use the final
  HTTPS base address and correct deployment prefix; omit `/api/v2/diagnostics`
  from `--server`. Check certificate hostname, expiry and trusted CA chain on
  the machine running Python. Check connectivity and the reverse proxy route;
  this client ignores ambient proxy settings and provides no TLS-verification
  bypass. Browser registration additionally needs the player's exact allowed
  origin in server configuration.
- **HTTP 401/403, `credential_role_denied` or `diagnostics_disabled`:** verify
  the operator's digest, exact device scope, action scope and per-device
  diagnostics flag, then validate/restart after configuration changes. A valid
  administrator token grants no diagnostic access.
- **HTTP 404 / `not_found`:** verify the exact device/runtime/session/repair ID
  and the operator's device and action scopes. An out-of-scope request deliberately
  returns the same response as an absent target. Check the controller base path
  and server version too. A missing receipt after restart or expiry does not
  prove that the earlier operation never executed.
- **No runtimes, `runtime_expired`, or `consent_required`:** confirm the player's
  Remote control connection is enabled over HTTPS and server-side diagnostics
  are enabled for that device. Updated players register automatically.
  Discover again after reconnect/reload and select its new runtime and consent
  epoch. Do not infer identity from a tab label. Older players may still require
  their local diagnostic grant until updated.
- **`runtime_busy` or `repair_busy`:** read the existing exact session/repair
  state; `active_session_id` identifies an occupied capture. Finish that work or
  wait for expiry before creating another operation. Do not send new keys to
  evade an uncertain mutation or a capacity conflict.
- **`capability_required`, `unsupported` or `rejected`:** inspect the runtime's
  capabilities and update compatible player/server builds. Repairs require
  `repairs`; ordinary lifecycle commands such as native app exit or OS reboot
  are not tools in this MCP adapter. Consult [player command support](cli.md).
- **`start_pending` or empty events:** acceptance only queued the action. Read
  status until `active` or a terminal state and check the remaining lease.
  Background visibility alone does not stop capture. OS suspension/pagehide or
  a lost connectivity lease can retire its runtime; resume/reconnect registers
  a new runtime but does not restart that capture. Advance event
  reads using `next_seq`; inspect truncation and drop counts for missing history.
- **`epoch_changed`, `server_epoch_mismatch`, `idempotency_conflict`, timeout or
  `unknown_outcome: true`:** inspect current state first. Reuse the original
  fields/key/epoch only within the recorded retention horizon. A server restart
  clears in-memory state; a new epoch or a missing receipt does not prove the
  earlier effect never ran. Never automatically replace the operation key.
- **HTTP 429, `rate_limited`, `runtime_limit` or `state_limit`:** reduce polling
  and concurrent work, inspect stale runtimes and wait for bounded records to
  expire. The CLI does not automatically retry; review uncertain mutations
  before taking any further action.
- **MCP connects but shows no tools or exits:** use the absolute Python/script
  paths, keep `diagnostics.py` beside the adapter, pass the credential privately
  into the launched process, and check the host's stderr. The host must negotiate
  MCP `2025-11-25` and send `notifications/initialized` before listing/calling
  tools. Stdout must contain only the adapter's newline-delimited JSON-RPC.

## Local verification

```sh
python3 -m unittest discover -s tests -p 'test_diagnostics_*.py'
```

Tests use synthetic credentials and mocked HTTPS responses. When `openssl` is
available, a localhost TLS fixture also checks certificate verification, routing
and redirect refusal with an ephemeral test certificate. Subprocess tests cover
the `ott diagnostics` alias and symlink installation without legacy configuration.
The subprocess MCP handshake/list test performs no controller request. Device acceptance is a
separate integration check; these tests do not claim a physical TV stopped.
