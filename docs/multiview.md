# Multi-view control

The controller and CLI implement the version 1 multi-view control contract.
Usable operations depend on the selected player's advertised capabilities and
its backend admission checks. Source integration tests are separate from LG
hardware qualification. A nine-window layout or a finite composition test does
not establish nine simultaneous native decoders.

This change does not install a player, enable a backend or publish a capability.
Older players continue using their existing single-player commands. The named
`input pip` key is a separate legacy UI action.

## Discover support

```sh
ott l mv --help
ott l caps
ott l mv status
ott --json l mv status
```

`mv --help` works without a controller or credentials. Ordinary player help shows
multi-view controls only when the player advertises them. A missing marker stops
the command before a multi-view request. A zero-capacity marker permits status
only. The CLI first negotiates the controller's support with a capability probe;
there is no fallback to global provider, profile or playback commands.

## Select a layout and sources

The following examples require the corresponding advertised operations:

```sh
ott l mv layout pip
ott l mv layout grid4
ott l mv layout grid9
ott l mv layout perimeter --satellites 8
ott l mv layout single

ott l mv sources 1
ott l mv sources 2 --provider m3u --profile profile-home --search News
ott l mv sources 2 --cursor CURSOR_FROM_THE_PREVIOUS_PAGE
ott l mv window 2 play TICKET_FROM_THIS_WINDOWS_SOURCE_LIST
```

Windows are numbered **1–9** in the CLI. The protocol uses `slot_id` **0–8**;
conversion happens once at the CLI boundary. Perimeter mode has window 1 in the
center and 0–8 satellites, defaulting to eight. Native code owns the geometry;
CLI requests cannot supply arbitrary rectangles.

Source tickets identify choices in a player-authorized snapshot. Provider and
profile filters only narrow that read. A profile is an opaque saved-authority ID,
not a numbered global profile slot. The current contract does not imply support
for every inactive provider catalog, arbitrary URLs, local paths or Plex rating
keys. Native source discovery and its broker must authorize each choice.

A source ticket or cursor belongs to its player, scene and window. Refreshing
status does not renew an expired ticket. Re-list the intended window after an
explicit stale-source rejection; do not move its ticket to another window.
Source lists are bounded to 128 choices per page and return a cursor when more
choices remain.

## Focus, audio and stopping

```sh
ott l mv focus 2
ott l mv window 2 focus
ott l mv audio 2
ott l mv audio follows-focus
ott l mv audio off
ott l mv window 2 stop
ott l mv off
ott l mv off restore_previous
```

Focus selects the input owner. Audio has an explicit fixed-window, follows-focus
or muted policy. Audio ownership is not evidence that decoded sound is audible,
and TV master volume remains device-wide.

Stopping a window and leaving multi-view are distinct operations. The default
exit mode requests promotion of the active window; restoring the prior playback
requires separate advertised support. A backend may refuse shrinking a layout
with occupied windows until it can preserve their playback state.

Each mutation reads a fresh scene snapshot and carries runtime, scene, layout,
focus, audio and, where relevant, slot/source revisions. Native must enforce
these bindings and local kiosk/parental policy before effects, including after
asynchronous source resolution. A result reports an operation as requested or
applied; it does not prove visible playback. Inspect `mv status` for per-window
progress and closed failure reasons.

Per-window states are `empty`, `starting`, `playing`, `paused`, `stalled`,
`ended`, `failed` and `stopped`. `paused` means intentional suspension while
retaining the selected source; it is distinct from a stalled stream or a stopped
window. Starting, playing, paused and stalled windows must identify their source.
The configured audio owner can remain selected while paused; that selection does
not establish audible sound.

## Groups and uncertain outcomes

```sh
ott t1,t2 mv status
ott tt mv layout grid4
ott tt mv focus 1
```

Group operations use the existing parallel dispatcher. Each player obtains its
own runtime and scene bindings. Raw source tickets and pagination cursors cannot
be broadcast to a group; obtain and use each player's choices separately.

The CLI does not retry mutations, refresh a rejected context automatically or
fall back to a different source. A lost acknowledgement leaves the outcome
uncertain. Read status or the original operation receipt before deciding what
to do next. Native reuses its existing request-delivery deduplication: pending
duplicates do not start another operation, and cached terminal replies remain
historical receipts rather than fresh snapshots.

## Integration and troubleshooting

The shared [contract fixture](../contracts/multiview-v1.json) defines exact fields,
conditional shapes, limits and synthetic examples. Requests use the existing
authenticated `multiview` RPC action with exact operator permissions:

```text
multiview.status
multiview.sources
multiview.layout
multiview.play
multiview.focus
multiview.audio
multiview.stop
multiview.off
```

Observers need only the applicable read permissions. Device scope and result
ownership still apply. Responses are closed and bound to the original request;
negative responses cannot mint fresh context or tickets. No raw media URLs,
credentials, private filesystem paths or HLS lease secrets belong in this API.

### Installation-specific service binding

Native builds may advertise `nativeMultiviewService` through the existing typed
settings API. It is a sensitive application string containing a service binding
provisioned for that specific installation. Use the operator-provided, compact
single-line JSON file; the current settings contract permits at most 4096 UTF-8
bytes. The native player must validate the binding against its persisted device
identity before using the service.

```sh
ott l setting nativeMultiviewService --value-file /path/to/private-binding.json
ott l setting nativeMultiviewService
ott l setting nativeMultiviewService ""
```

Reads and successful writes show a redacted value. The empty string disables the
binding. A binding is not a portable provider profile: CLI presets and Local
profile import/export formats do not accept this field. Binding writes require a
single player; grouped reads remain available. Provision each player separately.
Existing provider credentials in presets retain their existing behavior.

### Synthetic endpoint qualification

Run the isolated public-protocol check from the controller checkout:

```sh
python3 -B tests/test_multiview_endpoint_cli.py -v
go test -race ./internal/control -run TestMultiview -count=1
```

The Python check runs the real CLI as a subprocess against an ephemeral loopback
HTTP fixture with synthetic credentials. Its native adapter accepts an
`action`/`params` request and returns `status`/`data`; this is the seam to replace
with a compiled native handler during integration. The fixture advertises only
synthetic support, reports empty windows and returns `outcome: requested` for
mutations. It has no media, decoder, TV connection or playback-success evidence.
The separate Go tests exercise controller admission and receipt validation.

The shared contract includes `native_delivery_rejections`: exact original
requests paired with typed `unavailable` and `limit_exceeded` replies. Native
tests should compare generated replies with these vectors before adding the
original request ID. `public_text_cases` fixes the common label/query domain,
including credential syntax hidden by Unicode characters. Do not weaken the
native display filter to accept a controller query.

Before a native endpoint advertises usable operations, integration must prove:

- The dispatcher consumes the original duplicate-checked request projection and
  routes multi-view before generic error paths. Every correlated negative has
  the original runtime, operation, binding and a closed reason. Missing original
  correlation remains an unknown outcome, not a reconstructed receipt.
- A dedicated adapter projects one consistent coordinator snapshot into the
  closed wire schema. Internal slot/status names, source identity hashes and
  pipeline-admission fields are not serialized into public replies.
- Source tickets and cursors belong to the authorized player, scene and window.
  Discovery intersects native saved-source authority with a qualified backend
  binding; it does not switch the global provider or profile.
- The effect driver retains the original invocation and deadline across async
  work, rechecks generation, policy and revisions before effects, and connects
  session lifecycle, source grants, output renewal, playback and cleanup.
- Capability capacity and each operation reflect qualified backend support.
  Frame progression, isolated source failure, audible-owner changes, cancellation
  and teardown require their own native/backend tests. A synthetic command
  receipt cannot substitute for those results or for LG hardware qualification.

### Troubleshooting

- **Unsupported:** check both controller negotiation and the selected player's
  marker. Installing CLI code alone does not enable a media backend.
- **Window not in the layout:** read status and choose an existing window.
- **Context changed:** a local pointer action, another controller or source
  replacement can invalidate a prepared command. Read the current state before
  deliberately issuing another change.
- **Source expired/unavailable:** re-list authorized choices for that window.
  Native-only USB, protected streams or unresolved Plex sources may require a
  broker the backend does not provide.
- **Requested but not playing:** use per-window status. One failed or stalled
  source must not be reported as a successful complete scene.
- **Invalid metadata:** keep the matching controller, CLI and player contract
  versions together. The command is not repeated after a malformed reply.
