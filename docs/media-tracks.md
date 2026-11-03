# Current-playback audio and subtitles

`media_tracks` version 1 lets a compatible player list and select tracks for its
current playback. It does not save language preferences or change the separate
`key audio` input command. Selection is available only when the player advertises
`"media_tracks":{"version":1}`. Installing the controller or CLI does not add a
track-selection implementation to the player.

The initial native producer integration is still pending. In particular, the
reviewed native external Demux component is source evidence, not a shipping
capability. Existing native Plex selection paths can write persistent server
preferences, and external HLS cannot yet switch its tracks safely. Such routes
must report unsupported or non-selectable until an appropriate implementation
is qualified. No capability is enabled on a deployed player by this change.

## Commands

Install all CLI Python modules from the same revision, including `media_tracks.py`.
Use the player's registered alias:

```sh
ott lg-native caps
ott lg-native tracks
ott lg-native tracks list
ott lg-native tracks audio a-example
ott lg-native tracks subtitle s-example
ott lg-native tracks subtitle off
ott -j lg-native tracks
ott --receipt -t 20 lg-native tracks audio a-example
ott tt tracks
```

Copy an exact ID from the current list. IDs are case-sensitive opaque handles,
not track numbers, language names, Plex stream IDs, or URLs. `tracks subtitles`
is an alias of `tracks subtitle`. Use `tracks subtitle id:off` if the actual track
ID is literally `off`; bare `off` disables subtitles. `tracks audio off` refers
to the literal audio ID `off`, never to muting audio.

Selection first discovers capabilities and reads a fresh complete catalog. It
then submits the requested ID only if the ID, kind and selectability still match.
The request carries the exact runtime, logical playback, concrete source
generation and catalog receipt. A changed or expired context is rejected without
falling back to another track, language, provider or playback item.

Groups retain the ordinary parallel dispatcher. Each player has its own catalog,
deadline and outcome. A group selection passes the same ID unchanged to each
player; it does not map one player's handle to another player's track. Use
individual commands when their IDs differ.

## Understanding the result

The list reports audio and subtitles independently. `NOT OBSERVED` means the
player lacks authoritative current-backend evidence. It does not mean track zero,
muted audio, or disabled subtitles. A known subtitle-off state is reported as
`off`; absence of subtitle cues alone does not establish it.

`Selection requested; backend selection has not been confirmed` means the player
accepted the current-playback request. It may still fail, expire, or be superseded
by another manual or remote selection. `Backend selection confirmed` requires a
fresh authoritative observation matching the target and context. Neither result
claims that sound reached speakers or subtitle pixels appeared on the screen.

JSON preserves these distinctions. For example, an admitted audio selection has
`"accepted":true`, `"outcome":"requested"` and `"observed":null`. A subsequent
`tracks` call reports present facts; it is not causal proof that an earlier
request produced them. `--receipt` writes the ordinary request IDs and transport
outcomes to stderr without mixing them into JSON stdout.

## Protocol and bounds

Use the existing request/response transport with action `media_tracks`. There is
no new endpoint or job protocol. The [shared fixtures](../contracts/media-tracks-v1.json)
define the request, response and negative cases.

```json
{"action":"media_tracks","params":{"op":"list","runtime":"native-example"}}
```

```json
{"action":"media_tracks","params":{"op":"select","runtime":"native-example","playback":"p-example","source_generation":"s-example","catalog":"c-example","target":{"kind":"subtitle","mode":"off"}}}
```

A track target instead contains exactly `kind`, `mode:"track"` and `id`. Subtitle
Off contains exactly `kind:"subtitle"` and `mode:"off"`; it has no ID. Operators
can be granted `media_tracks.list` and `media_tracks.select` separately at the API
level. CLI listing requires `capabilities` and `media_tracks.list`. CLI selection
requires all three grants: `capabilities`, `media_tracks.list` and
`media_tracks.select`, because every selection first reads a fresh catalog.
A `media_tracks.select`-only grant is usable through the API with an already
obtained, still-valid catalog receipt and its exact runtime, playback and source
bindings; it is insufficient for the CLI selection workflow.

Requests are bounded to 2 KiB for the complete action/params object. Result data
is bounded to 64 KiB. A complete list contains at most 128 tracks, at most 64 per
kind. IDs are 1–64 ASCII letters, digits, underscores or hyphens; runtime uses
1–64 lowercase letters, digits or hyphens. Labels are at most 128 UTF-8 bytes;
language and codec metadata are nullable bounded ASCII strings. Duplicate IDs,
duplicate JSON keys, unknown fields, invalid types and non-finite numbers are
rejected. Unsafe URL-, path- or credential-shaped labels must be replaced by the
producer; obvious unsafe wire labels fail validation.

An incomplete or oversized inventory fails as a whole. A successful empty list
is reserved for a known complete inventory with no tracks. Passive listing must
not trigger probes, provider HTTP requests, subtitle downloads, playback or
persistent writes. A catalog receipt has a 60-second lease, renewed by a successful
unchanged list; renewal never extends an already admitted selection's deadline.

The player owns the final fences. Manual and remote audio/subtitle changes share
one selection-intent epoch, including accepted no-op choices. New accepted intent
supersedes old pending work; rejected requests do not. The original request and
catalog deadlines are captured at admission and checked at each effect boundary.
An audio rebuild may continue only through its own explicitly owned handoff,
while logical playback, intent, policy and deadline remain valid. Old IDs are
never resolved against an unrelated new pipeline.

Current selection must not write profile defaults, local per-show track memory,
or Plex's persistent part selection, including through retries and derived
transcode/subtitle paths. Subtitle appearance belongs in separately supported
saved settings. A route that cannot honor temporary selection must refuse it.

## Troubleshooting

- **Capability missing:** update to a player that actually implements the
  extension. The CLI sends no selection without the exact marker.
- **Catalog incomplete:** wait for the player's ordinary source preparation.
  The CLI does not force a scan or silently use a partial list.
- **Catalog or playback changed:** list again and choose the current ID. A
  replay of the same media can still have a new source generation.
- **Not selectable or unsupported:** the current transport may lack temporary
  selection. Do not substitute a persistent Plex/menu operation automatically.
- **Requested, not confirmed:** inspect a fresh list and the player. Desired
  indices and generic video progress are not selected-track observations.
- **Malformed track list:** no selection was sent, including when the list was
  the preflight for a selection command. Check player/controller compatibility.
- **Selection timeout or malformed selection acknowledgement:** keep the request
  ID and inspect current state. The command may have executed; it is never
  repeated automatically.

Before a producer advertises version 1, qualify real audio selection, subtitles
and explicit Off on controlled media. Include stale IDs, manual/remote races,
no-op supersession, deadline expiry during rebuild, full/empty/unknown inventories,
and proof that local preferences and Plex selection state remain unchanged.
