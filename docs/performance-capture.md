# Temporary performance capture

Performance capture is off by default. It records bounded numerical evidence for
playback freezes and notification load, using the player's enabled remote-control
connection. It does not enable the local debug HUD, upload arbitrary logs, or
change playback recovery. Install all twelve `cli/*.py` modules from one revision,
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
means the export files were committed; consult `capture_complete` and the report's
`partial_reasons` for the actual capture completeness. Keep an incomplete
directory for inspection rather than deleting it and blindly repeating a write.

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

The exact wire schema and semantics are in
[`contracts/perf-capture-v1.json`](../contracts/perf-capture-v1.json).
