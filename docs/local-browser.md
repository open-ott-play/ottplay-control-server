# Browse USB folders on a native player

This protocol requires matching CLI, controller and native-player support. It is
an additive feature, not proof that a particular LG build can access a USB disk.
Check `ott lg2 caps`: browsing requires `local_browser`; using a selected source
requires `local_media_handles`. Older players keep their existing Local commands.
The examples use `lg2`; replace it with your registered player alias.

## Find and select a folder

```sh
ott lg2 local roots
ott lg2 local browse ROOT_ID --generation GENERATION
ott lg2 local browse DIRECTORY_ID --generation GENERATION
ott lg2 local select SELECT_ID --generation GENERATION
```

Copy the tokens from the preceding response. They identify a current view on
that player; they are not drive paths, stable disk identifiers or profile names.
Each successful navigation returns a new generation. Old view tokens stop
working. A parent entry uses the same `local browse` command as a child.

Large directories distinguish two operations:

```sh
# Read another page of the already retained folder list:
ott lg2 local browse --cursor CURSOR --generation GENERATION
# Explicitly increase the bounded scan allowance for an incomplete directory:
ott lg2 local more SELECT_ID --generation GENERATION
```

A page contains at most 64 rows. `partial` means the underlying directory scan
is incomplete, even if there is no next page. `limit_reached` means the scan has
reached its hard limit; more expansion is unavailable. Paging does not silently
rescan the disk. `more` performs a new bounded read and invalidates the old view.
Live directory contents can change between reads.

Roots, navigation and selection do not save a profile, switch provider, start
video or alter kiosk. Selection returns a separately held source handle. It
survives navigation to another browser view but becomes invalid when its source
is detached, replaced or otherwise loses its verified access. A busy in-player
USB picker is not interrupted by remote browsing.

## Play temporarily or remember the source

```sh
# Temporary playback, preserving the permanent provider/profile:
ott lg2 local shuffle --handle HANDLE
ott lg2 local play --handle HANDLE
# Explicitly remember the verified source without starting playback:
ott lg2 local connect --handle HANDLE
```

Use the handle returned by `local select`. Handle-based connect retains the
player's verified USB binding through its normal settings writer. It does not
activate a profile. Selecting a folder alone does not remember it, and playing
it does not save the temporary queue, cursor or resume position.

An accepted playback request is not confirmation that video frames are visible.
A progressive response can describe only the playable prefix discovered so far.
`stable_identity: true` reports an observed source identity; it does not mean a
profile was saved or that cold-start recovery has been tested on this TV.

Tokens are local to one player. Run token-based commands for one alias at a
time. `ott l,lg2 local roots` can obtain independent views; it does not make a
token from one TV valid on the other. Existing commands by connected source name,
such as `ott l,lg2 local shuffle Movies`, continue to resolve each player
independently.

For permanent Local profiles and folder kiosk, follow the prerequisites in the
[Local profile guide](cli.md#local-videos-profiles) and
[native USB workflow](cli.md#native-lg-usb-and-ssd-folders). Connecting a source
does not itself select the catalogue required for a folder kiosk target.

## Troubleshooting

- Missing capability: update the matching components or use the player's own
  Local picker. No browser/handle command is sent after a failed capability check.
- Busy: finish the in-player picker or other source operation, then start again.
- Stale view: run `local roots` again and use the new tokens.
- Unavailable or changed source: reconnect the intended disk and select it
  again. A matching mount path is not evidence of the same disk or access grant.
- Partial scan: use a returned cursor for retained rows, or explicit `local more`
  when expansion is available. A successful empty partial page is not proof that
  the directory is empty.
- Timeout after dispatch: the outcome can be uncertain. Read `local sources`
  and `status`; do not blindly repeat connect, select or shuffle.

## Wire contract

The capabilities are closed version-1 objects. Their operation arrays contain
each listed operation once; order is immaterial.

```json
{
  "local_browser": {"version": 1, "operations": ["roots", "list", "more", "select"], "max_entries": 64},
  "local_media_handles": {"version": 1, "operations": ["connect", "play", "shuffle"]}
}
```

The `local_browser` action accepts closed parameter objects with `version: 1`,
the current `runtime`, and `op`. Roots accepts no further fields. List takes
`generation` and exactly one of `entry_id` or `cursor`. More and select take
`generation` and `select_id`. Tokens match `[A-Za-z0-9_.:-]{1,256}`.
Operator permissions are separate: `local_browser.roots`, `.list`, `.more` and
`.select`. Existing handle commands use their `local_media` operation permission.

Ready browser replies include `version`, `runtime`, `op` and `state: "ready"`.
Roots/list/more add `generation`, `kind`, `entries`, `parent_id`, `select_id`,
`next_cursor`, `partial` and `limit_reached`. Root views have only root entries
and null parent/select IDs; directory views have only directory entries and a
non-null select ID. Entry IDs are unique. Names are nonempty display names of
at most 512 UTF-8 bytes, without control characters. A name is never interpreted
as a path; navigation uses only the returned tokens. The complete
encoded response, including its envelope, must fit within 64 KiB; a player may
return fewer rows to meet that bound. A hard limit implies a partial scan.

Select echoes the requested generation and adds
`source: {id, name, kind: "folder", handle, stable_identity}`,
`persisted: false` and `activated: false`. List/more return a new generation.
An error reply contains only `version`, `runtime`, `op`, `state: "error"` and
`error`. Codes are `invalid_request`, `unsupported`, `busy`, `stale`,
`source_changed`, `unavailable`, `restricted`, `expired`, `cancelled` and
`limit_exceeded`. Failed reads must not become successful empty lists.

For handle operations, the capability probe adds `local_media_handles: true`
alongside the existing session/progressive flags when applicable. False, null,
non-boolean values and unknown flags are rejected. The `local_media` request
contains exactly one of legacy `source` or `source_handle`. Successful
handle-based replies add an exact `source_handle` echo; source-based replies and
five-field error replies retain their existing shape. Session-only playback
negotiation remains mandatory in the CLI.

The [shared wire examples](../testdata/local_browser_v1.json) are synthetic
cross-language fixtures, not evidence of installed-device support.
