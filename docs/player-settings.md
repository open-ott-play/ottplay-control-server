# Typed player settings and VPortal presets

The player publishes its supported settings, types and constraints. The CLI
validates against that schema and saves through the same settings store as the
player UI. It never evaluates JavaScript, writes an arbitrary storage key,
selects a provider to make a setting available, or wakes a sleeping player.

Install compatible controller/player versions and **all `cli/*.py` files**,
including `player_settings.py`. Copying only `ott.py` is insufficient. Existing
remote-control authorization remains the authority; no new temporary permission
is required. Existing player policies, PIN restrictions and platform limits apply.

## Discover, read and save

```sh
ott f settings
ott f settings list
ott -j f settings schema
ott f setting bufSize
ott f setting bufSize 3
ott f setting bufSize 0
ott f setting players
ott f setting players hls.js
ott f setting players auto
```

The three discovery forms use the same live catalog. `-j` preserves structured
descriptors. Each descriptor includes a canonical ID, type, scope, limits,
choice aliases, sensitivity and current read/write access. Use the IDs and
choices actually listed by the target player. Choice aliases come from the
schema, not translated labels. `hls.js` and `hls` name the same integer choice
where the platform offers it. Output includes the readable label beside the
stored number.

`setting KEY` reads one preference. `setting KEY VALUE` reads its descriptor and
revision, then sends one conditional save. Types remain exact: many toggles use
integer `0`/`1`, not JSON booleans. String-array values use quoted JSON arrays.
For a private or longer value, use a bounded UTF-8 file:

```sh
ott f setting KEY --value-file /absolute/private/value.txt
```

The file contains the value, or a JSON array for a string-array setting. One
trailing newline is removed. Values supplied directly on the command line may
enter shell history/process listings. Secret snapshots omit raw current,
stored, effective and default values; `configured` reports presence only.
Some connection, identity and protected settings require their dedicated
workflow and advertise that restriction.

`bufSize` is an application integer from **0 to 3600**. The playback engine uses
**1–120 seconds** as an explicit buffer; **0 and values above 120 select automatic
buffering**. Saving does not restart the current stream. `players` is a
provider/profile preference; LG may force automatic mode. These changes apply
to the next stream. Stored preference and effective platform behavior are
reported separately. A successful save does not assert that current playback
has switched engine or buffering.

Application preferences work without a mounted provider or working catalog;
the context can contain `provider: null, profile: null`. Provider preferences
bind to the current provider/profile storage source. They never silently fall
back to another provider. When `uiFontScale` is listed, use its advertised range
and application behavior.

## Conditional saves and recovery

Capability `player_settings` version 1 advertises `schema/get/set`. Discovery
pages contain at most 20 descriptors. Every request binds to the current player
runtime. A `get` returns its descriptor and an opaque revision; `set` uses that
revision and the source context:

```json
{"action":"player_settings","params":{"op":"set","runtime":"page-123","id":"bufSize","value":3,"context":"context-1","revision":"revision-1"}}
```

The player checks runtime, source, persisted bytes and setting revision before
committing. Receipts expire after 120 seconds. A source change, concurrent edit
or stale revision rejects the write. Local UI drafts share the settings store
and its conflict handling. One CLI invocation shares one timeout across
discovery, reading and saving. The CLI never refreshes a revision and retries a
failed write automatically.

Success requires verified persistence. Application is separate: `completed`
has no reason; `deferred` names `next_stream`, `manual`, or `after_ack`; `failed` names
`effect_failed`. A saved value whose application failed is printed as saved
with a failure and exits nonzero. After an uncertain response, read `setting KEY`
before deciding whether another change is needed. A queued/accepted transport
receipt is not proof of a save.

`after_ack` means the desired value is saved and its runtime transition is
scheduled after the response reaches the controller. It does not claim that a
connection restart has already completed. Read the setting/transition status
afterward to verify the active configuration.

Scoped operators need `capabilities` and exact permissions
`player_settings.schema`, `player_settings.get`, and, for writers,
`player_settings.set`. Administrator credentials work unchanged. Existing HA
and read-only operator permissions are not widened. The operation journal
contains a digest and outcome, never setting values. Diagnostic operation
evidence contains action/status, not request values. Direct authorized
nonsecret reads remain available; diagnostic exports are not settings backups.

## Change a connection as one transaction

Use `player_settings_patch` version 1 for related connection fields. Inspect the
saved group without revealing addresses or credentials:

```sh
ott d1 settings patch command_server
ott -j d1 settings patch local_http
```

A private JSON file contains **only the fields to change**. `command_server`
accepts `commandServerAddress`, `commandServerToken`, and integer
`commandServerEnabled` (0 or 1). `local_http` accepts `localCmdUrl`,
`localHttpDeviceCode`, and integer `localHttpEnabled` (0 or 1). At least one field
is required. The remaining fields come from the captured group revision.
Do not pass tokens as command-line arguments.

For example, create `/absolute/private/connection.json` with mode 600:

```json
{
  "commandServerAddress": "https://control.example/api/webhook/commands?device_id=YOUR_DEVICE_ID",
  "commandServerToken": "YOUR_DEVICE_TOKEN",
  "commandServerEnabled": 1
}
```

Then save the tuple:

```sh
ott -t 45 d1 settings patch command_server --value-file /absolute/private/connection.json
ott d1 settings patch command_server
```

The file and complete request must each fit within 16 KiB. URLs are bounded to
4096 UTF-8 bytes and credentials to 256. Duplicate JSON keys, mixed-group fields,
booleans in integer toggles, and unrecognized fields are rejected before sending
any request. The CLI discovers support, reads the complete group, and sends at
most one conditional write under one timeout. All three fields share the source
context and revision. Local edits invalidate the snapshot.

A changed tuple is persisted and read back before the acknowledgement. Its
initial state is `pending`, with application `deferred/after_ack`; this confirms
saving, **not a working new connection**. After acknowledgement, the player uses
the normal connection owner to test the new tuple. A bounded failed handshake
restores the previous tuple if that transition still owns the fields. A newer
local edit is never overwritten by an older rollback. A private recovery journal
also handles an interrupted transition on player restart. An unchanged tuple
reports `applied` without reconnecting. Disabling the active control connection
intentionally disconnects it; subsequent status must use a remaining connection
or local UI.

Read the group again through the active controller to see `idle`, `pending`,
`applied`, `rolled_back`, or `failed`. Failed outcomes include bounded reasons
such as `handshake_timeout`, `authorization_failed`, `network_error`,
`storage_failed`, or `ack_timeout`. A timeout or lost response is not permission
to replay the write automatically. Verify the saved group on the old/new
controller or locally before retrying. Changing an address or token individually
while its connection is enabled returns `atomic_patch_required` with guidance
to this workflow.

Scoped operators need `capabilities` and `player_settings_patch.get`; add
`player_settings_patch.set` only for writers. Secrets are redacted in both plain
text and JSON output. Existing player settings policies apply.

## Plex folder selection

When `plex_folders` is advertised and Plex is the current provider, `settings`
also lists `plexFolderPaths` as a collection. It has a dedicated paginated API
so all **1000 supported folder roots** remain editable without enlarging scalar
setting requests. This is independent of the **500-item playback queue limit**.

```sh
ott d1 provider plex
ott d1 plex libraries
ott d1 setting plexFolderPaths
ott -j d1 setting plexFolderPaths list
ott d1 setting plexFolderPaths add /library/sections/16/all "Children's films"
ott d1 setting plexFolderPaths remove /library/sections/16/all
ott d1 setting plexFolderPaths clear
```

Use the library ID shown by `plex libraries` in `/library/sections/ID/all`.
Canonical Plex folder roots and metadata children paths are also accepted;
filesystem paths and arbitrary URLs are not. Adding an existing path preserves
its title. Removing an absent path changes nothing. **An empty selection,
including `clear`, means all libraries**, matching the player UI.

Reads collect pages of at most 10 rows, with one context, revision and total for
the entire saved snapshot. A changed snapshot or duplicate/incomplete page
fails instead of returning a partial selection. Each edit first captures a
revision and then sends one conditional operation. The player verifies saved
bytes before invalidating the catalogue. The saved selection applies on the
next catalogue load; the operation does not switch providers, wake playback or
start a new stream. A full selection refuses an additional new path; removal
and clearing remain available. After a timeout, read the collection before
retrying an edit.

Scoped operators need `plex_folders.list` to read the selection and the exact
`plex_folders.add`, `.remove` or `.clear` permission for each intended edit,
along with `capabilities`. Existing read-only/HA scopes are not expanded.

## Independent VPortal configuration

VPortal has 15 dedicated slots. Its links are provider configuration, outside
the generic preference registry. Capability `vportal_settings` identifies the
bound API. A private file can configure an inactive slot while M3U or another
provider stays selected:

```json
{"provider":"vportal","settings":{"profile":1,"vportal":"portal::[key:YOUR_KEY]https://portal.example/api/v1/","name":"Family"}}
```

```sh
ott f provider-config /absolute/private/vportal.json
```

Omit `name` to preserve the VPortal slot's name. The CLI compares the dedicated
slot, then saves with its runtime/context/revision. Matching settings are not
rewritten. An inactive save never mounts VPortal or opens its UI. Saving during
standby never wakes playback. The result distinguishes `reloaded` from
`reload_required`; an active slot may need its normal reload when playback
resumes. Both flags cannot be true.

A named preset can declare VPortal slots explicitly, beside its existing
`m3u`, `plex`, `active_profile` and optional `stalker` fields:

```json
"vportal": [
  {"number":1,"vportal":"portal::[key:YOUR_KEY]https://portal.example/api/v1/"}
]
```

A nonempty legacy `m3u[].vportal` field is migrated **only in the normalized
plan** to the same numbered VPortal slot. It is never sent as an M3U setting,
and the M3U name is not copied. Equal explicit and legacy links deduplicate;
different links for one slot fail before any writes with a redacted error.
The local file is not rewritten. An explicit empty VPortal link can clear a
slot; an empty legacy field does not request a clear.

```sh
ott f load Local plan
ott f load Local apply
ott f load Local resume
ott f load Local verify
```

VPortal participates in the plan fingerprint, preflight comparison, conditional
save and final exact verification. An older player fails before the first
preset mutation when a VPortal slot is required. No provider switch or UI
automation is attempted as a VPortal fallback. Existing preset selection of
Plex, Stalker and the final M3U profile is otherwise unchanged. A checkpoint
whose old plan omitted migrated VPortal settings is rejected as a revision
mismatch. Inspect the device and create a new checkpoint for the corrected plan.

## Coverage and troubleshooting

The runtime catalog covers registered application/provider preferences, not
every object visible in Settings. Other configuration uses explicit workflows:

| Configuration | Workflow |
| --- | --- |
| M3U profile names, playlists, archive hours | `profiles`, `profile N`, `profile-config N FILE` |
| Dedicated VPortal link/name | Bound `provider-config FILE`; preset `vportal` block |
| Plex server/token | `plex setup`, `plex server`, `plex token-file`; `provider-config FILE` |
| Xtream, Stalker, OTTClub configuration | Existing `provider-config FILE` for a selected, supported driver |
| Local video source grants and portable profiles | Local OS/browser picker; `profile save/load/export/import` |
| Plex folder selection | `setting plexFolderPaths [list\|add PATH TITLE\|remove PATH\|clear]` when advertised |
| Kiosk, lifecycle, notifications | `kiosk`, `standby`, `go`, `restart`, `notifications on/off` |
| Remote connection, pairing, native permissions, identity | Existing connection/pairing/native workflow; schema reports generic access restrictions |

Missing capability: update the controller, player and complete CLI installation.
Source/revision changed: read again and review the current provider/profile.
Policy restricted: use the existing authorized unlock or dedicated settings
workflow; the CLI does not bypass it. Storage failed: check player storage and
read before retrying. `next_stream`: allow normal playback to start its next
stream or explicitly request a stream restart when appropriate; saving a
preference never issues one automatically.
