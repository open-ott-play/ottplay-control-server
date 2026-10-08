# Read-only diagnostic workbench

The workbench collects one player's web observations and, when bound, its separate
Android maintenance agent. It does not start playback, reload a player, change
settings, capture the screen, or run arbitrary commands. These commands require
the source revision containing `cli/workbench.py`; they are not part of stable
CLI v0.1.0. Keep all six CLI Python files from the same revision together.

```sh
ott a1 doctor
ott a1 doctor --json
ott --json a1 inspect --view ui,media
ott a1 inspect --view media --lane web --json
ott a1 inspect --lane native --json
ott a1 operation 0123456789abcdef0123456789abcdef --json
ott a1 bundle --out ./a1-case
ott a1 test list
ott a1 test run health --report ./a1-health
ott -t 45 a1 test run media-progress --duration 5 --report ./a1-progress --json
```

`-j`/`--json` works before the player name or after a workbench command. Global
`-t`/`--timeout` and `-c`/`--config` still precede the player name. Commands use
the existing CLI connection, aliases and credentials; no second registration is
needed. The workbench does not print those credentials or the server URL.

`ott a1` lists diagnostic commands supported by the connected player's advertised
inspection sections, alongside its controls. `ott a1 test list` always lists the
local scenarios; that list does not claim the player supports their observations.

Use global `--receipt` to obtain a command's request ID without changing its
ordinary stdout, for example `ott --receipt a1 restart` for a requested player
restart. Unlike workbench reads, that command changes the player. It emits a JSON line on
stderr such as `{"action":"restart","request_id":"0123456789abcdef0123456789abcdef","status":"ok"}`.
Then inspect that ID with `ott a1 operation ID --json`. `ok` records the RPC's
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

The scenario only observes. It does not prevent user input or reserve a device;
it cannot exclude every seek or transient action between its two samples. Its
evidence level is `decoder_progress_only`. Visibility, sound, hardware surfaces
and the physical display require additional evidence. `physical_display_verified`
is always false in this first workbench version.

There are no custom scripts, arbitrary predicates, eval, shell actions or automatic
restoration in this runner. Mutating scenarios and exclusive device leases require
the later operation/ownership protocol.

## Bundles and machine output

Every JSON result contains a version, command, device ID, times, verdict,
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

## Troubleshooting

- `unsupported_controller`: update the controller/player to a revision with the
  read-only inspection protocol. Existing Android maintenance observations can
  still be obtained with `--lane native`.
- `not_bound`: provision a separate native agent, then use
  `ott a1 android bind a1-native`. Never share the web player's queue/token.
- `invalid_response`: the device returned an incompatible, oversized or malformed
  snapshot; the raw response is intentionally not copied into a bundle.
- `media_identity_changed_or_unavailable`: playback changed during collection, or
  the adapter cannot identify its backend handle. Do not infer a frozen screen.
- `output_exists`: keep the previous evidence and select a new output directory.
- `insufficient_timeout`: increase `ott -t SECONDS ...` or shorten `--duration`.

See [CLI installation](cli.md), [scoped diagnostic sessions](diagnostics-cli.md),
and the [Android maintenance agent](../native/android-agent/README.md).
