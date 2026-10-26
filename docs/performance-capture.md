# Temporary performance capture

Performance capture is off by default. It records bounded numerical evidence for
playback freezes and notification load, using the player's enabled remote-control
connection. It does not enable the local debug HUD, upload arbitrary logs, or
change playback recovery. Install all `cli/*.py` modules from one revision,
including `performance.py`; update the controller before the player and CLI.

```sh
ott f perf --help
ott f perf status
ott f perf start
```

`start` prints a session ID. The default duration is 120 seconds; `--duration`
accepts 10–600 seconds. `--raf` optionally collects animation-frame aggregates;
`--no-long-tasks` disables optional Long Tasks observations. Unsupported APIs stay
explicitly unavailable. Starting capture is an explicit operation; capability,
status, read, export and ordinary debug snapshots never start it.

```sh
ott f perf start --duration 180 --raf
ott f perf status --session aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ott f perf read aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --after 0 --limit 64 --json
ott f perf stop aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ott -t 90 f perf export aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --out ./capture-01
```

Replace the example ID with the printed session ID. `list` is an alias for
`status`: the player retains at most one session. Use an explicit `--session` on
`start` to supply a fresh 32-character lowercase hexadecimal ID. Repeating that
ID with identical normalized options returns the same session; it never extends
the lease or restarts a stopped session. A different active session or changed
options returns `busy`. The CLI does not retry writes after an uncertain result.

## Bounds and lifecycle

Capture samples at 1 Hz and accepts at most 20 events/second with a burst of 40.
The ring retains at most 1,024 events and 512 KiB; each event is at most 2,048
bytes. It reports rate, ring and oversized-record drops. Reads return at most 64
events, dynamically packed into 60 KiB of data (64 KiB including the outer
response). These bounds are shared by player, controller and CLI.

Expiry, page hiding, runtime or connection retirement, and explicit stop remove
the capture's listeners, observers, animation callbacks and sampling timers.
Ordinary stream/engine replacement continues the session with new local source
labels and a confounder event. There is no automatic restart. Stopped evidence is
retained for 300 seconds; connection/runtime retirement can discard it sooner.
The optional retention cleanup timer does not sample.

The pre-existing diagnostic-only 1 Hz loop runs only during this explicit lease.
Outside capture, ordinary debug snapshots omit unavailable loop metrics. Existing
event history and mandatory playback watchdog/recovery logic remain independent.

## What is measured

- Main/PiP media identity, position, playback rate, contiguous playable buffer,
  up to eight adjacent buffer ranges, dimensions and decoder state.
- Supported frame counters with their source, current/retired engine-attempt
  counters, HLS load timing and status, completed buffering timing, and ABR changes.
- Event-loop delay, optionally animation-frame and Long Tasks aggregates.
- Supported notification lifecycle stages and measured fetch, validation,
  base64, blob and combined image-load/decode times, image sizes and active count.

Missing counters are null, while a measured zero stays zero. Zero frame growth is
not evidence of smooth playback. Counter deltas are only compared within the same
runtime, lane, generation, handle, source label and attachment. Loader durations
come from observed loader timestamps, not event-receipt timing. Unsupported queue
or isolated image-decode timings remain null.

The kiosk progress check can report unavailable when the contiguous buffer is
below two seconds, or focus, seeking or readiness checks fail, before reading the
frame API. Runtime debug can still report an API counter of zero in that state.
Those observations do not conflict and do not prove a stubbed counter; assess
actual counter and playback-position advancement over time.

Events contain no notification text, pictures, DOM content, provider/stream URLs,
credentials, entity/camera names or raw HLS errors. Source and notification IDs
are session-local labels, not hashes of those values. A `shown` event means DOM
attachment/visibility, not proof that pixels appeared on the physical display.

## Bounded export

Export freezes the first status response's sequence high-water mark. It reads at
most 32 pages and never follows a continuously growing live tail. The private
directory is new and non-overwriting, with mode `0700` and `0600` files; parent
symlinks are refused. Total output is limited to 1 MiB. `result.json` contains
validated evidence and request receipts; `manifest.json` hashes the saved files.
Private export and trials currently require a POSIX system supporting directory
file descriptors and these permissions; unsupported systems fail before requests.

Sequence gaps, dropped events, timeout, a replaced session, or a page/size limit
produce an explicitly partial report and nonzero exit status. A complete manifest
means the export files were committed; consult its `capture_complete` and the
result's `capture.complete` / `capture.partial_reasons` for capture completeness.
Complete means that the frozen sequence range was exported without recorded
loss; it does not mean the configured capture duration has elapsed. Keep an
incomplete directory for inspection rather than deleting it and blindly repeating
a write.

Plain output explicitly states whether the capture is complete and lists partial
reasons. A local output path is printed for convenience but is not embedded in
new portable `result.json` files. Offline verification checks exported v1/v2/v3
captures without credentials or a player connection:

```sh
ott report verify ./capture-01 --json
```

The verifier checks the bounded file inventory, hashes, request journal, protocol
data and capture completeness. It accepts at most 1 MiB across the saved files,
with at most 32 read pages, 1,024 events and 512 KiB of encoded events. A valid
partial capture remains partial; matching hashes do not authenticate its origin.
For perf exports, verification returns `0` for a complete capture, `3` for valid
partial or unavailable capture evidence, and `1` for an invalid export or
inconsistent evidence. A late response or a deadline before the first request
can therefore produce a valid report with insufficient evidence. A complete
capture does not establish smooth playback or a working physical display.
Notification trial verification is not supported by this evaluator: preserve
its plan and restoration evidence and
inspect the trial result separately. See the [monitoring runbook](monitoring-runbook.md)
for diagnosis before repair.

## A–B–A notification trial

This command temporarily changes the notification policy. It uses one capture
session for all three phases so source labels remain comparable:

```sh
ott -t 240 f perf trial notifications --out ./notifications-trial-01
ott -t 180 f perf trial notifications --seconds-per-phase 10 --out ./notifications-trial-02
```

A1 uses the original policy, B uses the opposite policy, and A2 restores the
original. The default phase duration is 30 seconds with 15 seconds of settling
before each phase. `--seconds-per-phase` accepts 10–60 and `--settle` accepts 0–15.
Preflight requires the three phase durations, three settling periods and 60 seconds
of shared control/export budget within `-t` (195 seconds by default, plus the time
already spent on initial requests); it reserves final cleanup time and never
silently increases the user's deadline. Replies arriving after the shared deadline
are recorded as confirmed late, not successful within the deadline. A validated
late policy write can be restored only while the reserved cleanup budget remains.

The trial requires the typed `notificationsEnabled` setting and its real-owner
revision checks. Before starting, it saves a private plan with the initial value,
runtime, context and revision. Each change uses compare-and-set. Restoration uses
only the revision returned by the trial's own confirmed write; local interruption
also attempts that guarded restoration. Legacy/UI writes, including same-value
changes, invalidate the revision. An external change, unconfirmed write or expired
deadline produces an honest uncertain/unrestored result, never a blind overwrite.
An older player without the typed setting cannot run a partial trial.
After interruption, the report may contain only the plan and request receipts.
The printed session ID can be exported separately within its retention window;
the interrupted command does not silently restart the experiment.

`policy_restored` is separate from `storage_representation_changed`: a default-on
preference can be restored as explicitly stored-on. This restores policy but does
not claim a byte-for-byte restoration of absent storage.

The report records phase boundaries, host request/acknowledgement windows,
runtime/build, source/engine identity, focus and visibility, sampling options,
counter resets and stream/ABR changes. Summaries associate shown banners with
nearby loop delay/HLS errors and compare measured phase distributions. The first
15 seconds after capture start or a policy change, observed seek intervals and
sample pairs spanning a seek are excluded from these comparisons; raw events stay
complete. Shorter `--settle` values therefore reduce the usable phase interval.
Excluded intervals and source changes are reported as confounders. Summaries report
temporal association, not causation. Network contention, OS scheduling, standby
and the physical display are not independently proven by these observations.

## Scoped access

Use an ordinary command operator restricted to the device ID behind alias `f`.
Raw capture needs `capabilities`, `perf_capture.start`, `perf_capture.status`,
`perf_capture.read`, and `perf_capture.stop`. Trials additionally need `inspect`,
`player_settings.get`, and `player_settings.set`. The latter is an operation-wide
setting-write permission, not a notification-only permission. Provisioning is a
separate administrator operation; these commands do not grant themselves scope.
The protocol-2 diagnostics operator/consent system is separate and is not bypassed.

## Troubleshooting

- `unsupported`: update the controller/player and install all CLI modules; check
  for the exact `perf_capture` capability. No speculative request is sent without it.
- `busy`: inspect the existing session; do not stop somebody else's session merely
  to start a test. Use its ID only when that action is intended.
- `not_found` or `expired`: the retained session is gone. Export sooner; starting
  a new capture cannot recover an older one.
- `capture_stopped`: inspect its reason (for example `page_hidden`); reopen the
  player and explicitly start a new capture when appropriate.
- `revision_conflict` or unconfirmed restoration: inspect the current typed
  setting and saved receipts. Do not repeat the old restore against a fresh revision.
- Partial export: inspect drop counters and limits. A quieter, shorter new capture
  may provide complete evidence; the existing report remains useful as partial data.
- `controller_unavailable`, `controller_http_error`, `request_pending_timeout`,
  `receipt_missing`, `unauthorized` or `rate_limited`: inspect the saved bounded
  failure reason. The CLI preserves these distinctions when the request client
  supplies them and does not repeat a capture or policy mutation. A transport
  failure alone does not establish that the player is offline.
- `evidence_incomplete_preserve_directory`: file export did not finish. Keep the
  directory for inspection; a printed destination is not proof that a valid
  manifest was committed. Choose a new directory for a later export.

The exact wire schema and semantics are in
[`contracts/perf-capture-v1.json`](../contracts/perf-capture-v1.json).

## HLS gaps and live catch-up

For periodic freezes at segment boundaries, explicitly request the additional
HLS measurements. Install the updated controller first, then the player and CLI.
The player must advertise `perf_capture_hls: {"version": 2}` in addition to the
unchanged `perf_capture` version 1 capability.

```sh
ott f caps
ott f perf start --duration 120 --hls-details
ott f perf status --session aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ott f perf stop aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ott -t 90 f perf export aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --out ./hls-freeze-01
```

Use the session ID returned by `start`. The flag is accepted only by `start`;
status, read, stop and export negotiate the reader automatically. Ordinary
`perf start` and notification trials keep their original version 1 behavior.
Neither a capability lookup nor a reader enables HLS instrumentation. A missing
marker prevents dispatch of the capture request. Do not run simultaneous trials
while measuring the original fault: collect the baseline before changing policy,
buffer settings, channel or playback engine.

Version 2 keeps the same duration, rate, event, ring, export and cleanup limits.
Its session metadata reports `capture_version`. A version 2 reader can read or
stop either session version without filtering events or changing sequence counts.
An older version 1 reader cannot access or stop a version 2 session; the player
returns `unsupported` before changing it. Once a version 2 session has stopped,
a fresh version 1 start with a different ID may replace the retained report.
Reusing an existing ID with a different capture version never upgrades or
downgrades that capture. Export the evidence before starting its replacement.

The additional events contain only bounded numerical observations:

- `hls_buffer`: actual per-track `audio`, `video` or `audiovideo` ranges from
  public HLS `BUFFER_APPENDED` callbacks, with the observed media position.
  Each event preserves up to eight adjacent ranges and the truncation count.
  Before a track callback arrives there is no observation; a missing event
  must not be interpreted as an empty buffer.
- `hls_fragment`: fragment sequence, level, part index, discontinuity counter,
  fragment interval and observed PTS bounds, plus available elementary-stream
  PTS for at most three distinct tracks. Join these with the existing `hls`
  load and append timing events by source, lane and fragment identity.
- `hls_config`: the effective HLS buffer lengths and byte budget, buffer-hole
  tolerance, watchdog and nudge settings, and live synchronization/maximum
  latency settings. It records attachment and changes observed during the
  existing one-second sampling loop. It does not change any setting.
- `hls_live`: observed media position, latest level target duration and edge,
  live synchronization position, actual/target/maximum latency, effective
  buffer-length target and current/loading level indices and
  advertised bitrates. These observations help separate
  a hole in track buffers from a jump toward the live edge. Negative finite
  latency is retained: it can occur after overshoot or with a stale playlist.
  Advertised bitrate is playlist metadata, not measured network throughput;
  compare it with actual fragment bytes and load durations before drawing a
  conclusion about buffer sizing. The diagnostic does not query the next ABR
  choice: that getter can change the ABR state.

Unavailable values, including non-finite HLS configuration values, are null.
These events do not include source URLs, codec strings, media bytes or arbitrary
configuration. Correlate actual position and frame advancement with raw ranges;
an accepted capture, a nearby error, or a missing observation does not establish
the cause of a freeze. An HLS `stalled`/nudge error also does not by itself prove
that a particular subsequent seek came from that recovery path.

An updated player with an old controller can still advertise the new marker,
but that controller rejects version 2 requests before dispatch. The CLI reports
that the controller needs an update and does not retry a start using version 1.
In this mixed deployment an ordinary version 1 start may work while an automatic
version 2 reader fails; finish the controller-first upgrade before measuring.
Do not confuse the CLI's conservative `unknown_outcome` for an HTTP error with
proof that a rejected request ran on the player.

The separate exact schema is
[`contracts/perf-capture-v2.json`](../contracts/perf-capture-v2.json); the original
version 1 schema remains unchanged.

## HLS buffer policy and retry evidence

Use the explicitly selected version 3 capture when an effective buffer limit has
shrunk, or a fragment's retry counter changes between loading and buffering:

```sh
ott d1 caps
ott d1 perf start --duration 120 --hls-policy-details
# Replace SESSION with the returned ID.
ott d1 perf stop SESSION
ott -t 90 d1 perf export SESSION --out ./hls-policy-01
ott report verify ./hls-policy-01 --json
```

This requires `perf_capture_hls_policy: {"version": 3}` and a controller that
accepts version 3. Upgrade the controller before the player and CLI. The flag
cannot be combined with `--hls-details`, which continues to select version 2.
Ordinary starts and notification trials continue to select version 1. Nothing
starts automatically and a rejected start is not repeated with fewer features.
A version 3 reader can preserve and export sessions of versions 1, 2 or 3; older
readers reject a version 3 session without modifying it. Offline verification
checks the saved session's actual version, including historical exports.

Version 3 distinguishes observations that must not be combined into one claim:

- `hls.stats_retry` is the raw, mutable hls.js statistics counter at that callback.
  It replaces the ambiguous version 1/2 name `retries` only in version 3 captures.
  It can increase during gap recovery without another network request.
- `hls_error_action` copies the public error-action snapshot at this listener's
  callback: a bounded action, flags, `retry_budget_count` and `resolved`, with
  error category/detail and fragment identity. `retry_request` describes an
  observed decision, not a completed or even started request. Other listeners
  may still change the action. The budget count can be shared across levels or
  tracks. `resolved` means the error was handled; it does not prove network
  success. Missing or unrecognized fields are null. Actual network retries are
  not measured by this API and no inferred total is exported.
- `hls_fragment` adds observed `fragment_gap`, `part_gap`, `part_independent`
  and per-elementary-stream `partial` flags. Gap flags can be changed by recovery;
  they are not exclusively playlist tags. The part's independent marker does
  not prove independent decoding. The stream partial flag does not establish
  whole-fragment or current SourceBuffer completeness.
- `hls_buffer_policy` separates four initial merged configuration values from
  the current values: maximum buffer length, `maxMaxBufferLength` limit,
  byte budget and back-buffer length. Core main/PiP engines copy these four
  numbers immediately after construction, before loading. This fixed metadata
  adds no timer or listener while capture is off. It is bound to that engine;
  unsupported engines report `initial_source: "unavailable"` and null initial
  values instead of reconstructing history from mutable configuration.

The first policy event is a capture `snapshot`, not proof that the engine was
just created. Later `cap_observation_changed` events report differences in the
observed `maxMaxBufferLength` limit, using existing HLS callbacks and the
existing one-second loop. They include the previous observation and the
observation source. A transition involving null changes availability and does
not prove a numeric limit change. These are samples: changes between observations
can be missed. `cause` is always `unknown`, because hls.js exposes no public
buffer-cap change event with a reliable cause. Nearby errors are context, not
causal attribution. Version 3 additionally preserves the bounded error details
`buffer_full_error`, `buffer_append_no_progress`, `buffer_add_codec_error` and
`internal_exception`; arbitrary error text is excluded.

For example, an initial maximum of 60 seconds and a first observed maximum of
5 seconds establish that the values differ, not when or why they changed. A
counter moving from 0 at `fragment` to 1 at `fragment_buffered` establishes only
that the mutable counter changed. Start the next capture before reproducing the
fault to improve temporal evidence; do not replay a start or restart playback
merely to fill a missing observation.

Duration, event/ring/byte limits, drop accounting and cleanup are unchanged.
The additional events share the same budget, so inspect completeness before
interpreting absent events. There is no internal-method patch, extra poller,
automatic repair or capture of media URLs, payloads or error messages. The exact
schema is [`contracts/perf-capture-v3.json`](../contracts/perf-capture-v3.json).

The interpretation follows the pinned hls.js 1.7.3 implementation: its
[gap recovery mutates the statistics counter](https://github.com/video-dev/hls.js/blob/v1.7.3/src/controller/stream-controller.ts#L978-L986),
[error actions have shared retry budgets](https://github.com/video-dev/hls.js/blob/v1.7.3/src/controller/error-controller.ts#L320-L350),
and [buffer reduction can happen without a public error event](https://github.com/video-dev/hls.js/blob/v1.7.3/src/controller/base-stream-controller.ts#L611-L634).
