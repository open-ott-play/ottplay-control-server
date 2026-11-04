# Native Mac acceptance through the CLI

This runbook exercises a qualified **normal macOS player** through the real CLI and controller. It is not satisfied by unit tests, a libtest executable, a Linux renderer, or a queued-command acknowledgement. One runtime operator owns the player and executes one case at a time; source owners supply the matching contracts and build evidence.

## Prerequisites and evidence

Pin the complete CLI module set, controller executable, normal player executable, composed source revision, enabled features, assets and host libraries. Reconcile the queue-v2, Local-browser and managed-Plex changes before using one combined CLI. A version label alone does not establish that those producers are present. The isolated HostFixture route requires the supported macOS build and its authenticated storage adapter.

Use a dedicated private configuration, fixture alias, evidence directory and source/profile inputs supplied by the runtime operator. Do not point the fixture at household aliases or copy live credentials into reports. Synthetic PMS and media remain isolated. On real players, playback checks are restricted to **`~/v/data/Movies/`**; USB/SSD discovery does not authorize playing another directory.

The templates below show the underlying CLI, not a process supervisor:

```sh
NATIVE_CLI=/absolute/path/to/pinned/cli/ott.py
NATIVE_CFG=/absolute/private/fixture-cli.json
NATIVE_PLAYER=fixture-native
native_case() {
  python3 "$NATIVE_CLI" --config "$NATIVE_CFG" --json --receipt \
    --timeout 45 "$NATIVE_PLAYER" "$@"
}
```

Run each case through the reviewed acceptance harness with an agreed whole-run deadline and bounded stdout/stderr capture. Do not start a second Session or server. Keep raw output private; stop capture on its limit instead of treating truncated JSON as evidence. The CLI deadline is not a substitute for process cleanup and output limits.

The entry point is [native_cli_case.py](../scripts/native_cli_case.py). The runtime owner supplies a private handoff with the pinned CLI module set, configuration, credentials, CA, owner receipt, exact loopback HTTPS origin, one alias/device and an unexpired monotonic deadline from this Mac. Validate the plan before executing it:

```sh
python3 scripts/native_cli_case.py --handoff "$HANDOFF" --case caps
python3 scripts/native_cli_case.py --handoff "$HANDOFF" --case caps \
  --execute --output "$NEW_PRIVATE_CASE_DIR" --seconds 45
```

Each case needs its own new output directory. Parameterized cases take `--params-file "$PRIVATE_PARAMS"`; mutation cases additionally require `--allow-mutation`. Use the harness's closed case names and parameter validation, not arbitrary shell arguments. `response_received` means a validated CLI response was captured, never that Mac acceptance passed. Retain its binding, stdout, stderr and response files privately; export only the public `result.json` projection. A stale handoff must be replaced by the runtime owner, not extended by the caller. Existing Session-based acceptance may import the helper rather than start another session.

After a successful response and reaped child the helper removes its private staging directory. Unknown, rejected and unsupported outcomes retain staging, including any Favorites recovery journal. Preserve that evidence until the runtime owner reconciles the result; cleanup must not erase a possibly pending operation.

`--receipt` writes safe per-RPC receipts to stderr; discovery can produce several receipts for one command. Stderr can also contain progress and errors, so it is not a JSON-only stream. `--json` does not make all stdout public: names, paths and context tokens can still be present. Export only an allowlisted summary: case, action, request ID, status, runtime, schema version, counts/index, closed reason codes and relevant booleans. Never export tokens, URLs, file paths, arbitrary errors, settings values or source titles.

On timeout, missing/malformed receipt or cancellation, record an **unknown outcome** and stop dependent mutations. Do not repeat the command, refresh a context and retry, or automatically run a compensating write. Read current state separately. A receipt ID is correlation evidence, not proof of playback or persistence.

## Baseline and settings

```sh
native_case caps
native_case status
native_case settings schema
native_case profiles
native_case fav
native_case setting interfaceMode
```

These are reads. Check the actual capability and live descriptor before each feature case; unsupported capability is an unmet prerequisite, not a pass. Capture the original provider/profile, setting and kiosk state privately.

When the descriptor permits writing `interfaceMode`, exercise each layout sequentially:

```sh
native_case setting interfaceMode modern
native_case setting interfaceMode
native_case setting interfaceMode classic
native_case setting interfaceMode
```

These writes use the current context/revision. Check persisted and effective values separately; a saved result with `view_loading` does not prove that Modern is displayed. The runtime operator observes the actual layout. Other settings use only IDs, types and choices returned by the live schema; private values use `setting KEY --value-file FILE`. See [typed settings](player-settings.md).

## M3U, profiles and flat catalogs

Use only a fixture-owned profile slot and its reviewed private JSON file. `M3U_PROFILE` and `M3U_FILE` below must already identify those inputs.

```sh
native_case provider m3u
native_case profile-config "$M3U_PROFILE" "$M3U_FILE"
native_case profile "$M3U_PROFILE"
native_case profiles
native_case categories
native_case s --limit 20 --offset 0
native_case categories select "id:$CATEGORY_ID"
native_case s --limit 20 --offset 0
```

Provider/profile/configuration/category selection mutates state; listing does not. Obtain `CATEGORY_ID` from this player's current category response. Verify provider, active slot and catalog consistency; a successful profile selection does not itself prove that channels loaded. Do not reuse another player's IDs or a stale catalog. See the [CLI reference](cli.md).

## Plex: saved configuration and managed activation

These are separate cases. For saved fixture configuration, use the operator's private PMS origin/token file; never place the token directly on the command line:

```sh
native_case provider plex
native_case plex setup "$PMS_ORIGIN" --token-file "$PMS_TOKEN_FILE"
native_case profiles
```

For a prepared managed candidate, list and then use the **exact current** server ID/context. Do not run setup merely to make a managed case pass.

```sh
native_case plex servers
native_case plex servers select "$SERVER_ID" --context "$SERVER_CONTEXT"
```

List reads the retained candidate. Select changes the temporary runtime and requires one player. Confirm exact response correlation and `activated:true`, `persisted:false`, `playback_confirmed:false`; then observe the current runtime separately. An empty candidate list is an unmet fixture prerequisite. No expired context is automatically replaced. See [managed Plex](managed-plex-servers.md) and its [frozen contract](../contracts/managed-plex-server-selection-v1.md).

## Plex catalog, transport, tracks and queue

Use only IDs from the isolated fixture or current catalog. Preview/listing is read-only; play, seek, track selection, kiosk and queue navigation mutate state.

```sh
native_case plex libraries
native_case plex library --preview "id:$SECTION_ID"
native_case plex preview "$ITEM_A" "$ITEM_B"
native_case plex play "$ITEM_A" "$ITEM_B"
native_case plex status
native_case pause
native_case +10s
native_case -10s
native_case resume
native_case tracks
native_case tracks audio "$AUDIO_ID"
native_case tracks subtitle "$SUBTITLE_ID"
native_case tracks subtitle off
native_case plex next
native_case plex status
native_case plex prev
native_case plex status
native_case plex play --shuffle "$ITEM_A" "$ITEM_B"
native_case plex status
native_case plex repeat
native_case plex repeat all
native_case plex repeat
native_case plex repeat off
```

Read readiness before transport operations. Relative seek requires its advertised capability; bare `+10` changes channels, whereas `+10s` seeks. Track IDs must come from a fresh list and be selectable. `requested` is not backend confirmation; inspect the resulting observation. See [media tracks](media-tracks.md) and [native playback diagnostics](native-playback-diagnostics.md).

A `preparing` queue result is not playback confirmation. Verify settled state and advancing native media evidence. For shuffle, verify the occurrence multiset, not that a particular item must move.

Bare `plex repeat` reads queue status. Changing ordinary repeat requires a nonempty active queue and the separate `plex_queue_repeat` capability, confirmed by the CLI's second capability probe; general Plex queue support alone is insufficient. `plex repeat all` repeats the queue order, while `plex repeat off` selects `none`. Observe actual EOF transition after each policy change; a returned repeat value does not prove EOF handling or restoration after restart. These normal-Mac end-to-end observations remain required even when fixture tests pass.

Test kiosk policy separately with `native_case kiosk on` after the intended queue is active. `native_case kiosk off` releases that policy; it does not by itself prove any universal stop-at-end behavior. Finish with `native_case plex stop` and a fresh `plex status`.

Queue-v2 acceptance must capture both actual capability reads, relay behavior, the bounded v2 response and the resulting queue. Public writes remain limited to 500 occurrences. The 1,000-occurrence cold-restore case additionally requires the separately qualified authenticated initializer, normal-player restart and actual PMS/queue/Saved capture. This short command sequence does **not** close that gate. See [queue v1](../contracts/plex-queue-v1.json); use the reconciled v2 producer/consumer evidence for its extension.

## Favorites

With a known fixture item playing, use a unique fixture name and retain the returned exact ID privately:

```sh
native_case fav global current
native_case fav add "$FAVORITE_NAME"
native_case fav
native_case fav play "id:$FAVORITE_ID"
native_case status
native_case fav remove "id:$FAVORITE_ID"
native_case fav
```

Current/list are reads. Add/remove persist changes; play can load the saved source/profile. Global support must be advertised for the explicit global case; do not accept legacy fallback as global coverage. Dispatch is not visible playback. Remove only the confirmed case-owned item, and only after an unambiguous result; do not clear the collection to simplify cleanup. See [global favorites](design/global-favorites.md).

## Local and USB/SSD

The browser/handle case requires the Local252 producer and matching controller/CLI. Roots/browse read storage but update the current browser view; select creates an ephemeral handle. They do not prove playback or a saved profile.

```sh
native_case local roots
native_case local browse "$ROOT_ID" --generation "$GENERATION"
native_case local browse "$DIRECTORY_ID" --generation "$GENERATION"
native_case local select "$SELECT_ID" --generation "$GENERATION"
native_case local play --handle "$SOURCE_HANDLE"
native_case status
```

Update `GENERATION` and the relevant ID from **each preceding response**, including retained cursor pages; these variables are not constants for the whole script. Select must refer to the permitted Movies directory on real players. A handle belongs to one runtime/source; matching mount paths do not prove matching disks.

Test `local connect --handle "$SOURCE_HANDLE"` separately when durable connection is intended, then read `local sources` and authenticated Saved state. Connect does not activate a profile; temporary play does not save the queue/resume position. Read-only discovery alone cannot qualify detach/reattach or cold restoration. See [native USB and SSD folders](cli.md#native-lg-usb-and-ssd-folders).

## Completion

Record each case as passed, failed, unsupported or inconclusive with its actual evidence. Keep Native/UI observations separate from CLI acknowledgements. Include source/binary identities, safe receipts, cleanup outcome and remaining gates; incomplete native transport/PMS/Saved capture remains inconclusive. The runtime operator performs any deliberate restoration from the private baseline only after inspecting current state.
