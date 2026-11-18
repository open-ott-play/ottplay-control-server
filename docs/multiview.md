# Multi-view control

The controller and CLI implement the backend-neutral version 1 multi-view
control contract. Usable operations depend on the selected player's advertised
capabilities and actual decoder/resource admission. Local on-device decoding
requires no external compositor, external-service pairing, service bearer or
enrolled service catalog.

The merged LG native implementation selects local decoding by default. Publication,
installation and sustained physical LG playback remain separate qualification
steps. Hosted concurrent-decoder and graphics/audio checks do not establish nine
simultaneous HD streams on a television. See the native
[integration and qualification guide](https://github.com/open-ott-play/ottplay-lg/blob/main/docs/native-multiview.md)
for source and transport limits.

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
the command before a multi-view request. A zero-capacity legacy marker permits status
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
ott l mv sources 2 --provider m3u --profile PROFILE_TOKEN_FROM_PLAYER --search News
ott l mv sources 2 --cursor CURSOR_FROM_THE_PREVIOUS_PAGE
ott l mv window 2 play TICKET_FROM_THIS_WINDOWS_SOURCE_LIST
```

Windows are numbered **1–9** in the CLI. The protocol uses `slot_id` **0–8**;
conversion happens once at the CLI boundary. Perimeter mode has window 1 in the
center and 0–8 satellites, defaulting to eight. Native code owns the geometry;
CLI requests cannot supply arbitrary rectangles.

Source tickets identify choices in a player-authorized snapshot. Each window
can select its own authorized M3U, VPortal, Plex or Local source when native
discovery and admission support it. Provider and profile filters only narrow
that read. A profile is an opaque saved-authority ID, not a numbered global
profile slot. The current contract does not imply support for every inactive
provider catalog, arbitrary URLs, local paths or Plex rating keys. The native
source resolver must authorize each choice.

### Select a channel or file by its exact name

The CLI can obtain a fresh window-bound ticket for you. First choose an
advertised layout, then address each window separately:

```sh
ott l_ mv layout grid4
ott l_ mv 1 m3u "РЕН ТВ HD"
ott l_ mv 2 m3u "СТС HD"
ott l_ mv 3 local "Sample clip.mp4"
ott l_ mv 4 local "Another clip.mkv"

# The longer form has the same meaning.
ott l_ mv window 2 m3u "СТС HD"
ott l_ mv window 3 local "Sample clip.mp4"
```

Replace the example names with exact labels available from `mv sources WINDOW`.
Matching is case-sensitive and preserves Unicode; there is no substring match,
random choice or implicit first result. A file label is not a filesystem path.
The window must already exist in the current layout, and the player must
advertise both source discovery and playback.

If several sources share a label, the command lists their provider, profile
token and source ID without starting playback. Choose the intended source:

```sh
ott l_ mv 2 m3u "СТС HD" --profile PROFILE_TOKEN
ott l_ mv 3 local "Sample clip.mp4" --profile PROFILE_TOKEN --source SOURCE_ID

# A literal label beginning with -- follows the delimiter; filters come first.
ott l_ mv 3 local --profile PROFILE_TOKEN -- "--Clip.mp4"
```

Use the returned opaque values exactly. Profile tokens are not numbered global
profile slots. The CLI reads all bounded result pages before choosing a unique
match, refreshes its source ticket, checks the scene/window binding again, then
sends one play request. An incomplete, changed, expired or rejected lookup does
not trigger a playback retry. The original `mv window N play TICKET` form remains
available.

These shortcuts search only choices the player currently authorizes. Native LG
discovery includes eligible Favorites and retained/current catalogue entries;
it does not promise every saved inactive provider or folder. If a file is absent,
make it available through the player's supported source/Favorites workflow and
list the intended window again. The shortcut does not load a global profile or
change the ordinary provider to manufacture a match.

A source ticket or cursor belongs to its player, scene and window. Refreshing
status does not renew an expired ticket. Re-list the intended window after an
explicit stale-source rejection; do not move its ticket to another window.
Source lists are bounded to 128 choices per page and return a cursor when more
choices remain. Apply each selected ticket before refreshing discovery. Local LG
choices combine the captured current catalog and saved Favorites with their
original provider/profile authority, bounded to 1152 entries. This is not a
browser for every inactive provider catalog.

For example, after selecting an advertised layout, assign independent sources:

```sh
ott l mv sources 1 --provider m3u
ott l mv window 1 play TICKET_FROM_WINDOW_1
ott l mv sources 2 --provider plex
ott l mv window 2 play TICKET_FROM_WINDOW_2
```

Use `--provider vportal` or `--provider local` for other authorized choices.
Tickets are returned by the player; the uppercase names above are placeholders.

## Focus, audio and stopping

```sh
ott l mv focus 2
ott l mv window 2 focus
ott l mv audio 2
ott l mv audio follows-focus
ott l mv audio off
ott l mv window 2 stop
ott l mv off restore_previous
```

Focus selects the input owner. Audio has an explicit fixed-window, follows-focus
or muted policy. Audio ownership is not evidence that decoded sound is audible,
and TV master volume remains device-wide. Local LG player volume/mute applies
to the software audio owner; it does not change the television hardware volume.

Players that advertise the integer setting `multiviewBackgroundVolume` accept
background volume from 0 to 100 percent through the existing settings workflow:

```sh
ott l_ settings schema
ott l_ setting multiviewBackgroundVolume
ott l_ setting multiviewBackgroundVolume 15
```

The singular `setting KEY [VALUE]` reads or writes a value; `settings` lists the
schema. The player supplies the allowed range and applies its existing policy.
An older player that does not register this setting cannot gain it from a CLI
update alone.

Stopping a window and leaving multi-view are distinct operations. The default
exit mode requests promotion of the active window; restoring the prior playback
requires separate advertised support. Use explicit `ott l mv off restore_previous`
for the initial local LG implementation: it does not advertise the CLI default
`promote_active`. Restoration reauthorizes the captured ordinary source after
scene retirement. A backend may refuse shrinking a layout
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

## Pause and seek in local LG scenes

On a compatible build, ordinary playback commands target the focused window:

```sh
ott l mv focus 2
ott l pause
ott l +10s
ott l -1m
ott l seek 1m30s
ott l resume
```

Seek requires the opened reader to advertise seek support. Authorized local files
and eligible progressive HTTP responses with byte ranges can support it; HLS
windows do not. A seek while paused publishes a landing picture and remains
paused. These examples require current advertised playback operations and local
policy permission; they do not add a new multi-view wire operation. Bare `+10`
or `-10` remains channel navigation rather than a time offset.

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
credentials, private filesystem paths, favorite payloads or HLS lease secrets
belong in this API.

### Backend selection

The public v1 schema is unchanged and has no backend selector field. The CLI
does not require service enrollment or fall back automatically to an external
service when local admission is unavailable.

Only if the player's typed settings schema advertises `nativeMultiviewBackend`
with the corresponding values, select the implementation through that separate
settings API:

```sh
ott l setting nativeMultiviewBackend local
ott l setting nativeMultiviewBackend service
```

Change the backend only while all scene work is idle; busy requests are rejected.
`service` selects the optional legacy external implementation. A saved setting
does not establish playback support; use the player's current capabilities.

### Optional legacy external-service binding

For the external service only, native builds may advertise
`nativeMultiviewService` through the existing typed settings API. Local decoding
does not require this setting. It is a sensitive application string containing
a service binding provisioned for that specific installation. Use the
operator-provided, compact single-line JSON file; the current settings contract
permits at most 4096 UTF-8 bytes. The native player must validate the binding
against its persisted device identity before using the service.

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
  Local discovery uses authorized saved sources and native decoder/resource
  admission. Service enrollment and a qualified service binding apply only to
  the optional external implementation. Neither switches the global provider
  or profile.
- The effect driver retains the original invocation and deadline across async
  work, rechecks generation, policy and revisions before effects, and connects
  session lifecycle, source grants, playback and cleanup. The optional external
  service also manages its output renewal.
- Capability capacity and each operation reflect the player's qualified decoder
  and resource support for the selected implementation.
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
  Local mode needs native access and resolution for that source. The optional
  external service can use only sources its broker is authorized to resolve;
  configuring it does not grant access to native-only USB or protected streams.
- **Requested but not playing:** use per-window status. One failed or stalled
  source must not be reported as a successful complete scene.
- **Invalid metadata:** keep the matching controller, CLI and player contract
  versions together. The command is not repeated after a malformed reply.


## Negotiated scene cleanup

The recovery extension preserves the closed legacy multiview v1 marker. It does
not enable a backend or grant playback capacity. A controller and CLI that
support this extension must be deployed before a native build starts advertising
it. Native implementation, backend qualification and device rollout remain
separate gates; these protocol checks alone are not playback qualification.

A new CLI requests `capabilities` with `multiview: true` and
`multiview_recovery_v1: true`. The controller consumes these relay-negotiation
flags and forwards the existing empty capability params to the player. It
retains the request's opt-in for receipt validation and removes the additive
`multiview_recovery` marker from replies to older probes. It never invents native
support. The legacy `multiview` marker keeps its existing exact schema and
truthful admission capacity; zero still means status only.

The separate closed recovery marker contains version 1, the same runtime as the
player capability, supported cleanup operations and off modes. It contains no
admission capacity or retained-slot count. `status` is required; `stop` and `off`
may be advertised only when executable. Recovery Off permits only `close_only`.
An unknown version, malformed marker or runtime mismatch fails closed.

```sh
ott l mv status
ott l mv window 9 stop
ott l mv off close_only
```

These examples require the corresponding negotiated native support. A retained
window can remain addressable when new admission is zero or has shrunk. The CLI
reads a fresh scene snapshot and uses its actual slot identity, generation and
source revision; it does not increase the advertised admission capacity. Stop
and close-only Off carry `recovery_version: 1` plus the original runtime and full
scene/layout/focus/audio CAS. Replies, including rejections and retained replies,
must echo that complete binding. Legacy receipts cannot confirm recovery work.
The exact existing `multiview.stop` and `multiview.off` permissions still apply.

`mv off` retains its legacy default mode. `promote_active` and
`restore_previous` are not recovery modes and are never silently substituted for
close-only cleanup. They must not be advertised by a native implementation that
only closes the backend. Recovery cannot create a scene, resolve or play a source,
renew admission, switch an ordinary provider, promote playback or restore it.
It addresses only the original retained session/client custody, with current
policy checks; a newly configured backend must not receive another backend's
cleanup operation.

Only an explicit HTTP 400 rejection of the opt-in probe may trigger one read-only
legacy probe. A recovery marker leaking through that unnegotiated fallback is
rejected rather than used as authority. A timeout, malformed marker, uncertain mutation or stale CAS never
causes a mutation retry, source fallback or automatic rebinding. Read fresh
status to inspect current state. The same rules apply independently to each
player in a group.

For the optional external service, provisioning remains installation-specific:
use a dedicated service bearer and the exact durable remote device identity
through the sensitive setting API. Read redacted storage/application metadata
after writing; a stored binding with `apply: next_stream` is not positive
qualification. Do not rewrite or toggle service credentials to refresh a cached
zero/failed probe. Native must provide a bounded safe requalification mechanism.
Local mode requires no such service provisioning; its capabilities must reflect
the available native decoders and resources.

The additive fixture is [multiview-recovery-v1.json](../contracts/multiview-recovery-v1.json).
Favorites remain separate: there is no multiview favorites command in this
contract. A future favorite resolver must authorize the current provider/profile
and issue a fresh ticket for the selected window; global `fav play` does not
select a multiview window.
