# Diagnostic operator CLI and MCP

`cli/diagnostics.py` is a Python 3 standard-library client for the additive
diagnostics protocol 2. It uses a separately configured scoped operator
credential; an existing administrator or device token does not grant these
permissions. The device must also have diagnostics enabled and current local
consent. Existing `ott` playback commands are unchanged.

The same commands are available as `ott diagnostics ...`, before any legacy CLI
configuration is read. Install `diagnostics.py` beside the real `ott.py` file;
symlinks such as `/usr/local/bin/ott` resolve to that directory. Install
`diagnostics_mcp.py` beside `diagnostics.py` to use the MCP adapter. For example:

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

```sh
python3 cli/diagnostics.py --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN runtimes --device living-room
```

This returns JSON with `server_epoch`, exact runtime handles, capabilities and
consent metadata. Choose the exact target and use its current consent epoch.
`reported_uuid`/instance labels do not select a runtime. Record a new operation
key before the first start; keep that key and epoch if the response is lost.

```sh
python3 cli/diagnostics.py --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN start --device living-room \
  --runtime RUNTIME_ID --consent-epoch CONSENT_EPOCH --server-epoch SERVER_EPOCH \
  --lease-ms 600000 --idempotency-key capture-20261004-1
```

`lease-ms` is 1000–600000. The server can impose stricter limits. Start acceptance
does not establish that capture is active. Read the returned exact session ID:

```sh
python3 cli/diagnostics.py --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN status --session SESSION_ID
python3 cli/diagnostics.py --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN events --session SESSION_ID --after-seq 0 --limit 32
```

Each events call returns one bounded page. Preserve `next_seq`,
`truncated_before_seq` and `dropped_total`; a page is not a promise of complete
history. Use the returned cursor on the next call. The first implementation does
not keep polling in the background or accumulate an unbounded tail.

```sh
python3 cli/diagnostics.py --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN stop --session SESSION_ID \
  --server-epoch SERVER_EPOCH --idempotency-key stop-20261004-1
python3 cli/diagnostics.py --server https://controller.example/ott-control \
  --token-env OTT_DIAGNOSTICS_TOKEN revoke --runtime RUNTIME_ID --server-epoch SERVER_EPOCH
```

Stop immediately denies new server ingestion, but queued stop is not confirmation
that the device stopped. Inspect `device_stop_confirmed`. Revoke retires the
runtime credential; the device stops on rejection or its connectivity lease.

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
the `repairs` capability and report current local consent. Discover its runtime
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
that the effect did not run. Stopping local support, revoking consent, or
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

References: official MCP [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio),
and [tools/error handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

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
