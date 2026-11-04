# Managed Plex server selection

This extension lets a compatible native player expose one retained managed Plex
server and activate it temporarily. The controller and CLI validate the protocol;
the native producer must complete its separate implementation and qualification
before advertising `plex_servers`. Existing installed players may return an
unsupported-capability error. This document does not certify a native build or
device.

## List and select

On a player advertising the complete capability:

```sh
ott t1 plex servers
ott t1 plex servers select PMS-1 --context CONTEXT_FROM_LIST
```

Use the exact server ID and context returned by that player's list operation.
The server's display name is a label; selecting by name, a profile number, a URL
or a token is not supported by this command family. Selection accepts one player.
A comma-separated player list or a group resolving to multiple players is
rejected before selection is sent.

Listing reads the player's retained candidate while Local playback can continue.
It performs no network discovery, starts no media and saves no profile. An empty
list means there is no eligible retained candidate. An eligible candidate may
remain available after the context expires: list again to obtain a fresh context.
Each context has an absolute lifetime of 120 seconds. Repeated listings during
that lifetime do not extend it.

Selection can replace current Local playback after the player verifies the exact
candidate and applies its temporary managed runtime. Success confirms that this
runtime and its profile have been applied. It does not confirm that a library page
has mounted or that video is playing. The reply has `activated:true`,
`persisted:false`, and `playback_confirmed:false`.

The selection does not save a profile, change the saved active-profile slot or
write the managed server into the durable source allowlist. Ordinary preference
updates can still be saved. Managed resume and subsequent profile transitions
must preserve the temporary status of the managed credentials.

Existing commands retain their meanings:

- `plex server URL` updates the player's saved Plex connection settings.
- `profile NUMBER` selects a saved numbered profile.
- `plex libraries` lists sections of the configured Plex library.
- `plex play ...` and `plex shuffle ...` control media through their existing
  queue/library protocols.

## Troubleshooting

- **Missing or malformed capability:** no managed-server command is sent. Use a
  player and controller qualified for this extension. Updating only the CLI
  cannot create native support.
- **Expired, stale or replaced context:** list again after resolving the reported
  condition, then deliberately choose the current candidate. The CLI does not
  obtain a replacement context and repeat a selection automatically.
- **Network changed:** the player may retain the previously verified candidate
  when ownership, consent, configuration and physical identity remain unchanged.
  A new explicit selection can re-verify that exact server. Listing never does
  that network work. Revoked authorization is not repaired this way.
- **Restricted:** check the player's Providers PIN, kiosk rules and local source
  replacement policy. Remote selection uses the same admission rules as its UI.
- **Authorization conflict:** the same server ID has incompatible credentials or
  an origin conflict. Resolve the native configuration; the command does not
  retoken an existing connection.
- **Timeout or `outcome_unknown`:** the selection may have taken effect. Inspect
  the player's current state before deciding what to do. Neither a lost reply
  nor an expired client deadline proves that playback remained unchanged. The
  CLI does not repeat the mutation or fall back to a profile/provider command.

The CLI uses one timeout budget for capability discovery and selection. Increasing
`-t` changes that wait budget; it does not extend a context's lifetime. Keep
credentials in the player's existing configuration. Requests and replies for
this family contain no server URL, token, token hash or raw disk identity.

## Protocol

The [frozen v1 contract](../contracts/managed-plex-server-selection-v1.md)
defines the full schema and producer requirements. Its SHA256 is
`96630fdc691b94bcf4b6e26324636dbbcccb5798a988eb34af5e01d3082d3464`.
The [source manifest](../contracts/managed-plex-server-selection-v1.sources.json)
records the agreed contract, fixture and review-receipt hashes.

The complete capability is:

```json
{"plex_servers":{"version":1,"operations":["list","select"],"max_servers":1}}
```

Both unique operations are required; their order does not matter. No additional
capability probe or implicit profile/library permission grants this action.
Authenticated operators need `plex_servers.list` or `plex_servers.select` for
the corresponding operation, plus access to the target device. CLI use also
requires `capabilities`, because every command negotiates the current runtime
and support before dispatch.

Requests use the existing authenticated `/api/requests` transport:

```json
{"action":"plex_servers","params":{"version":1,"runtime":"native-qa-1","op":"list"}}
{"action":"plex_servers","params":{"version":1,"runtime":"native-qa-1","op":"select","server_id":"PMS-1","context":"ctx-0123456789abcdef0123456789abcdef"}}
```

Successful response data is either a list of zero or one server, or the correlated
activation receipt:

```json
{"version":1,"runtime":"native-qa-1","op":"list","state":"ready","servers":[{"server_id":"PMS-1","name":"Family Plex","context":"ctx-0123456789abcdef0123456789abcdef","expires_in_ms":120000}]}
{"version":1,"runtime":"native-qa-1","op":"select","server_id":"PMS-1","context":"ctx-0123456789abcdef0123456789abcdef","state":"ready","activated":true,"persisted":false,"playback_confirmed":false}
```

The complete original HTTP request body is limited to 16 KiB. The complete player
response envelope, including `id`, `status`, `data`, JSON escaping and whitespace,
is limited to 64 KiB. Duplicate fields are rejected before projection. Native
players receive the controller's transformed poll item, so reserializing that
item cannot demonstrate the original HTTP request-size check.

Replies are bound to the pending request ID, original runtime and operation.
Selection also echoes the exact server ID and context. A typed error has exactly
`version`, `runtime`, `op`, `state:"error"`, and an allowlisted `error`. A valid
stale runtime is echoed unchanged. Missing, invalid or ambiguous runtime/operation
is rejected by the outer validation path without inventing typed fields.

## Verification and delivery gate

The shared [wire fixtures](../testdata/plex_servers_v1.json) contain 178 independent
cases: 23 capability shapes, 50 requests and 105 responses. Their SHA256 is
`f4e5d75babe9313e99139115d483e46cfb19763f3aea751361d8c75069ec20a2`.
The retained `PROPOSED` metadata describes the original reviewed bytes; subsequent
producer/consumer agreement froze those exact bytes. The fixtures exercise raw
JSON, Unicode byte limits, fixed booleans, correlation and boundary sizes. They
are not evidence that a player executed a selection.

Producer qualification must additionally verify list purity, absolute context
expiry, network-only re-verification, consent/configuration/owner invalidation,
policy parity, single-use admission, completion after applied runtime publication,
and temporary credentials through `StartSwitch`, `BackAtRoot` and `TakeReady`.
The coordinated controller/CLI tests must cover early expiry and duplicate-request
handling, operator isolation, multi-player selection refusal and lost-reply
uncertainty. Native advertisement and installation remain gated on that work.
