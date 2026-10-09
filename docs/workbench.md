# Read-only diagnostic workbench

The workbench collects one player's web observations and, when bound, its separate
Android maintenance agent. It does not start playback, reload a player, change
settings, capture the screen, or run arbitrary commands. Install the
[reviewed CLI source revision](cli.md#choose-the-cli-source-revision) using the
macOS/Linux or Windows instructions; stable CLI v0.1.0 does not include these
commands. Keep all seven CLI Python files from the same revision together.

## Quick start

Use an existing registered player alias (`a1` below) and the same private CLI
configuration as ordinary controls. If using the direct source invocation,
replace `ott` with `python3 "$ott_script"` on macOS/Linux or `py -3 $ottScript`
on Windows. A new setup first needs [configuration and pairing](cli.md#configure-the-administrator-client).
The player and controller need the inspection protocol for web observations;
CLI installation alone does not add that support. Native examples additionally
require a separately installed and bound [Android maintenance agent](../native/android-agent/README.md#bootstrap).

Choose a new output directory for each bundle or test; the parent must already
exist. These examples leave playback unchanged and can be run individually:

```sh
ott a1 doctor --json                                   # identity, capabilities and readiness
ott a1 debug --json                                    # runtime, events and native metrics; alias dbg
ott --json a1 inspect --view ui,media
ott a1 inspect --view media --lane web --json
ott a1 inspect --lane native --json
ott a1 bundle --out ./a1-case
ott a1 test list
ott a1 test run health --report ./a1-health
ott -t 45 a1 test run media-progress --lane web --duration 5 --report ./a1-progress --json
ott -t 45 a1 test run media-progress --lane native --duration 5 --report ./a1-native-progress --json
ott report verify ./a1-progress --json
ott server debug --json                                # controller process and stored queue counts
```

`doctor`, `inspect` and `debug` explain what could be observed; `health` tests management
responsiveness; `media-progress` checks an already playing decoder. Neither
scenario proves a visible picture or audible sound. See [lanes](#observations-and-lanes),
[scenario verdicts](#fixed-scenarios), [bundle contents and exit codes](#bundles-and-machine-output),
[offline verification](#verify-saved-evidence-offline) and
[troubleshooting](#troubleshooting) before interpreting a result.

`-j`/`--json` works before the player name or after a workbench command. Global
`-t`/`--timeout` and `-c`/`--config` still precede the player name. Commands use
the existing CLI connection, aliases and credentials; no second registration is
needed. The workbench does not print those credentials or the server URL.

`ott a1` lists diagnostic commands supported by the connected player's advertised
inspection sections, alongside its controls. `ott a1 test list` always lists the
local scenarios; that list does not claim the player supports their observations.

## Runtime debug snapshots

`ott a1 debug` (alias `dbg`) reads the player's bounded runtime snapshot. It
collects event-loop delay, available browser memory counters, connectivity and
focus, command backlog/failure counters, media counters, and a bounded ring of
typed lifecycle/error events. Missing metrics mean unavailable, never zero.
Events have sequence numbers and elapsed times, without messages, URLs, stack
traces, filenames, DOM content or console text. The command does not enable a HUD,
start a capture, change playback, or probe a media source.

The snapshot can include a native producer for Capacitor Android/iOS or Tauri.
Its explicit state is `available`, `unsupported`, `unavailable`, `timeout`, or
`invalid`; only `available` carries data. Android PSS, iOS physical footprint and
process RSS are different metrics. Tauri process memory excludes its separate
WebView processes. Native `uptimeMs` is the producer's age, not a claim about
process creation time. Native package versions are separate from the loaded JS
build identity in `doctor`.

`debug` defaults to `--lane web` and supports only that lane: the native producer
is read through the running player, not the independent Android maintenance
agent. Use existing `doctor --lane native` or `android status` for that agent.
If the player JS or its command channel is blocked, `debug` can also time out;
successful inspection does not establish that a physical display is working.

Support is advertised separately as `controls.debug: {"version":1}`. The existing
`inspect.sections` list remains unchanged so earlier CLIs keep accepting player
capabilities. The new CLI sends no debug request to a player without the
capability. An older controller reports `unsupported_controller`, without retry
or a fallback to playback commands. `ott a1 play debug` still searches that title.

Bundles use evaluator `workbench-v3` and keep exactly `manifest.json` and
`result.json`. The latter contains separate `debug_observations`; a native-only
bundle has an empty array. The ordinary observations retain three quarters of
the total timeout and debug gets the remaining time. Each snapshot keeps its own
runtime and request receipts; snapshots are not assumed simultaneous. Debug data
does not promote the bundle verdict or prove an operation's effect. The current
verifier accepts legacy, `workbench-v2`, and `workbench-v3` reports; old verifiers
explicitly reject the new evaluator instead of silently skipping debug evidence.

`ott server debug` is a single administrator read of the controller's own
process, queue, result-storage and diagnostics-service counters. Sections are
sampled independently (`consistent:false`). Stored counts may include expired
entries until normal request handling cleans them up; this read does not expire
or mutate them. It contains no device IDs, tokens, queued payloads or server URL.
See the [API schema](api.md#controller-debug-snapshot).

## Request receipts and operation lookup

Use global `--receipt` to obtain a command's request ID without changing its
ordinary stdout, for example `ott --receipt a1 restart` for a requested player
restart. Unlike workbench reads, that command changes the player. It emits a JSON line on
stderr such as `{"action":"restart","request_id":"0123456789abcdef0123456789abcdef","status":"ok"}`.
Then inspect the emitted ID (replace the example below with that exact value):

```sh
ott a1 operation 0123456789abcdef0123456789abcdef --json
# For an operation submitted through the independent Android agent:
ott a1 operation 0123456789abcdef0123456789abcdef --lane native --json
```

The default lookup lane is web. `ok` records the RPC's
reply, not a completed restart or playback outcome. If the POST reply is lost,
the line contains `request_id:null` and `status:"unknown"`; do not repeat the
command to obtain an ID. Multi-request commands produce multiple receipt lines.
Only action, ID, and status are included; params, response data, credentials, and
server URLs are omitted. Existing stderr diagnostics may appear alongside JSON.

## Observations and lanes

`doctor` reads capabilities, build identity and a bounded snapshot. `inspect`
selects the web `ui`, `media`, or both sections, retaining identity, capability
reasons and collection metadata. The snapshot reports application panes, focus,
CSS visibility, media generation, backend handle and decoder state. Unknown
fields are omitted and malformed observations are rejected. Source identities
reported by the device are labelled `runtime_reported`; they are not independent
verification that a release artifact was installed.

`sourceRevision` is partial/unavailable for a dirty checkout or a source archive
without Git metadata. `buildId` hashes the compiled input before build-identity
substitution and minification. It identifies that input; it is not the digest
of the final distributed bytes or a release attestation.

`--lane web` observes the player only. `--lane native` uses the separately
provisioned `native_devices` binding, the same binding used by
`ott a1 android status`. `--lane auto`, the default, observes both within one
timeout budget. An offline web page cannot consume the entire native budget.
An unbound native agent appears as `not_bound`; an invalid or shared binding is
not used. Native results remain a separate shape, never a fabricated web snapshot.
Here, `native` specifically means that independent Android agent. A packaged
Tauri player uses the web inspection lane; installing a native desktop app does
not create an Android binding or enable `--lane native`.

New players advertise the optional `inspect` capability. If that capability or
the requested section is absent, the workbench reports `unsupported` without
sending an inspection probe. A new player connected to an older controller can
advertise the capability while the controller rejects the action; that becomes
`unsupported_controller`. Native observations remain available independently.
There is no automatic retry of the POST or fallback to a repair. Successful and
negative inspection responses must match the requested runtime and section;
malformed or mismatched envelopes are `invalid_response`.

Updated native agents expose the same web runtime, media generation and main
handle as the web inspection hook when that hook provides a consistent identity.
`media_identity_available` stays false for older builds or missing handles.
Native event runtime is compared with the preceding native health runtime:
`runtime_correlation` is `matched`, `mismatch`, or `unavailable`. This correlates
native observations, not every historical event with the current web media.
Free-text titles, queue contents, source strings and unknown event codes are not
exported. Optional decoder counters and scoped Android surface/audio evidence
remain separate from physical display or audible-output verification.

`operation REQUEST_ID` reads one exact 32-character lowercase hexadecimal request
ID from the current web runtime's bounded operation history. The lookup itself
gets a different request ID. It never repeats the original effect. The player
holds at most 128 request IDs in memory. A retained receipt reports `expired`
after ten minutes; eviction or reload loses the history. An absent receipt is
`unknown`, not proof that the operation never ran. Never replay a mutation
automatically after losing its response.

`accepted` means an after-ACK effect was queued; it does not establish that the
effect is still pending. A disconnect, server rejection or ACK deadline can
discard the effect without updating that receipt. `invoked` means the handler
ran; it can still decline the effect if policy or ownership changed.
`handler_completed` means the handler returned, including an effect callback run
after its ACK; it does not establish the effect's outcome. The current journal
never emits `observed`: that state and its `media_progress`/`runtime_changed`
evidence are reserved for future independently verified outcomes. Use the separate
`media-progress` scenario to observe decoder movement; even that does not
confirm presentation on the physical screen.

The runtime in an inspection response fences that response. It does not make
legacy protocol-1 mutations exclusively routed to that runtime, nor establish a
new authoritative device binding. `operation ID --lane native` reads the updated native agent's durable history
through health, without replaying the command. Up to 64 metadata-only receipts
are retained for 24 hours across web reloads and agent restarts. They retain the
original executor runtime. `handler_completed` is handler return, never proof
of the physical result; an interrupted claim is `unknown`. Older agents return
unknown with `history_available: false`. The default operation lane remains web.
See the [native agent](../native/android-agent/README.md) for exact evidence limits.

## Fixed scenarios

`health` checks that the requested observation lanes respond consistently, and
that a bound native agent reports a responsive WebView. An absent optional native
binding does not fail web-only health. An unreachable lane is `unknown`, while an
observed unresponsive WebView is `fail`. This is a management/collection check;
passing it does not certify active playback or the physical screen.

`media-progress` takes two observations separated by `--duration` (1–30 seconds).
The global timeout must leave time for both collections. PASS requires the same
web runtime, non-null media generation, main backend handle and media kind, fresh
consistent samples, a playing/ready decoder, and advancing position without a
large discontinuity. A changed identity, missing observation or paused media is
`unknown`, not a claimed stall. Capture clocks must be positive, collection times
known, and samples free of `invalid_sample`. Device clock intervals must agree
with the host's monotonic collection windows, with one second of tolerance for
coarse clocks. The host interval also bounds plausible position movement, so a
forward clock jump cannot turn a large seek into a pass. Reports include those
host interval bounds in `sample_interval_seconds`. A backward position jump is
`unknown`; a stable playing decoder with unchanged position fails the scenario.
Native counters alone cannot pass this test.

`--lane native` uses the Android agent's two health observations instead. PASS
requires the same agent runtime, boot ID, app PID, web runtime, media generation,
main handle and media kind. Both samples must report a responsive WebView, ready
player and playing decoder, with advancing position. Device capture and uptime
intervals must agree with the host's monotonic collection windows. Missing
identity or a process restart is `unknown`; increasing frame counters alone is
insufficient. Older agents without this evidence remain supported for inspection
but cannot pass this scenario. `--lane auto` continues to evaluate web progress;
it does not silently substitute a native result when web evidence is missing.

The scenario only observes. It does not prevent user input or reserve a device;
it cannot exclude every seek or transient action between its two samples. Its
evidence level is `decoder_progress_only`. Visibility, sound, hardware surfaces
and the physical display require additional evidence. `physical_display_verified`
remains false for these observations.

There are no custom scripts, arbitrary predicates, eval, shell actions or automatic
restoration in this runner. Mutating scenarios and exclusive device leases require
the later operation/ownership protocol.

## Bundles and machine output

Every live collection result contains a version, command, device ID, times, verdict,
`read_only`, observation lanes, request IDs when available, and hashes of the local
CLI/workbench files. A request receipt means its response was received; it does
not prove decoder recovery or a completed reboot. Missing replies remain unknown.

`bundle --out DIR` and `test run ... --report DIR` require a **new** directory
inside an existing parent directory. Existing directories and symlinked ancestors
are refused. On POSIX, directories are created with mode 0700 and files with 0600.
Files are published atomically without replacing existing names. An export is
complete only when `manifest.json` exists and its declared size/SHA-256 matches
`result.json`. A failure can leave incomplete evidence; choose a new directory
instead of overwriting it.

The bundle currently contains the projected observations and, for a bound native
agent, bounded event codes. It does not contain historical web logs, screenshots,
private source maps, provider URLs, settings or arbitrary exception text. Local
tool hashes identify the collector; they are not release attestations.

Exit codes: `0` = observations collected or scenario PASS; `1` = invalid command
or export failure; `2` = scenario FAIL; `3` = unavailable/insufficient evidence.
For `doctor`, `inspect`, and `bundle`, exit 0 can include an unavailable lane:
check each observation's `status` and `reason`. JSON is the authoritative result.

## Verify saved evidence offline

```sh
ott report verify ./a1-progress
ott report verify ./a1-native-progress --json
ott --json report verify ./a1-case
```

Verification works without CLI configuration, credentials, a controller or a
running player. It reads only the selected directory's `manifest.json` and
`result.json`; keep other files outside that export directory. It rejects
incomplete exports, extra files, size or hash mismatches, symlinks,
special files, duplicate JSON keys and unsupported report formats. It never
executes commands found in a report, contacts a device or repeats an operation.
On Windows, use a local drive path; UNC shares and device paths are rejected.

For `health` and `media-progress`, it validates the saved observations and
recomputes the result using the supported evaluator. A recorded PASS is not
accepted when the observations disagree. Exit codes retain their meaning:
`0` for a valid collection or PASS, `2` for FAIL, `3` for insufficient evidence,
and `1` for an invalid export or inconsistent result. Read the JSON verdict as
well as the integrity field; intact files can still contain invalid evidence.

New bundle reports identify their evaluator as `workbench-v3`; test reports use
`workbench-v2`. Both are supported. Reports without that field use the supported
legacy web evaluator; old native-only progress reports
are not reinterpreted as passing native tests. Unknown evaluators are rejected.
Keep all seven CLI Python files together when updating.

A matching SHA-256 proves that the files agree with their manifest. It does not
prove their origin: anyone who can change both can recalculate the hash. The
result therefore reports `authenticated: false`. Neither verification nor
decoder progress certifies the physical screen or audible sound.

## Troubleshooting

- Missing workbench commands or Python modules: follow the
  [pinned installation/update steps](cli.md#choose-the-cli-source-revision), verify
  the checkout SHA and use its direct Python invocation. An older `ott` may still
  be first on PATH; keep all seven modules together.
- `unsupported`: the player does not advertise the requested inspection section.
  Check the target UUID/build with ordinary `caps`/`status`, then update the
  [installation that target actually runs](cli.md#update-the-installation-used-by-the-target-player).
- `unsupported_controller`: update the controller/player to a revision with the
  read-only inspection protocol. Existing Android maintenance observations can
  still be obtained with `--lane native`.
- `not_bound`: expected when there is no Android agent. Use `--lane web` for a
  web/Tauri-only setup. If native observations are needed on the supported Android
  device, [provision a separate agent](../native/android-agent/README.md#bootstrap),
  then use `ott a1 android bind a1-native`. Never share the web player's queue/token.
- `invalid_response`: the device returned an incompatible, oversized or malformed
  snapshot; the raw response is intentionally not copied into a bundle.
- `media_identity_changed_or_unavailable`: playback changed during collection, or
  the adapter cannot identify its backend handle. Do not infer a frozen screen.
- `output_exists`: keep the previous evidence and select a new output directory.
- `insufficient_timeout`: increase `ott -t SECONDS ...` or shorten `--duration`.
- Offline verification exits `1`: check that the original `manifest.json` and
  `result.json` are present, unmodified and alone in the export directory. Read
  [verification requirements](#verify-saved-evidence-offline); do not edit a verdict
  or regenerate its hash to turn failed evidence into a pass.
- Scenario exits `2` or `3`: inspect its JSON `verdict`, `reason` and lane statuses.
  A FAIL or unknown result does not automatically trigger a restart, replay or
  configuration change; decide what to do from the evidence and existing authorization.

See [CLI installation](cli.md), [scoped diagnostic sessions](diagnostics-cli.md),
and the [Android maintenance agent](../native/android-agent/README.md).
