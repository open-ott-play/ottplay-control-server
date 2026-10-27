# Diagnose a player or a group

Use this runbook when a player disappears, playback stops progressing, or a
problem returns after a restart. Start with observations before changing the
state that explains the failure. These commands use the existing private CLI
configuration and registered aliases; see [installation and pairing](cli.md#installation-and-connection).

## Observe several players

```sh
ott -j devices
ott d1 watch
ott t1,t2,t3,t4 watch --duration 60 --interval 5
ott d1,t1 watch --duration 30 --interval 5 --request-timeout 5 --workers 2 --json
```

`watch` is a finite, read-only observation. It does not change the provider,
playback, notification preferences, standby state, or recovery policy. Duplicate
aliases for one device are sampled once. Names are resolved before requests, so
a typo cannot cause a partially selected group to run. Each target has its own
request budget; a slow player does not consume another player's entire budget.

Defaults are 30 seconds, a 5-second interval, a 5-second per-player request
budget and four workers. Duration accepts 2–300 seconds, interval 1–60 seconds
and less than the duration, request timeout 1–30 seconds, and workers 1–4.
At most 64 unique targets and 256 planned observations are allowed:
`ceil(duration / interval) × targets` must not exceed 256. The global `-t` option
also caps each player's request budget; it does not replace `--duration`.
Slow rounds can produce fewer samples than planned. JSON output is limited to
1 MiB and is a single document printed when collection finishes.

The controller inventory read requires administrator access. A scoped command
operator can still collect permitted player observations, but an unauthorized
inventory read remains visible and makes the overall collection partial.

Keep the three kinds of evidence separate:

- **Controller contact:** whether its inventory could be read. `last_seen` is
  the most recent recorded contact, not an online guarantee or decoder heartbeat.
- **Player observation:** whether a valid, current runtime snapshot arrived.
  Missing or rejected observations remain unknown. A successful fraction has
  an explicit number of observations; it is not an uptime percentage.
- **Media progress:** whether consecutive observations show a comparable,
  advancing decoder. A changed runtime, media generation or handle breaks the
  comparison. Paused, stopped, loading, hidden or insufficiently identified
  media must not be treated as a confirmed playback stall.

`health` and `watch` answer different questions. A responsive player in standby
can pass `health` while providing no evidence of playing video. Likewise,
decoder progress does not prove that a physical screen is on or sound is audible.
For detailed evidence rules see [workbench scenarios](workbench.md#fixed-scenarios).

Watch exit codes are `0` for a completed collection, `2` when an observed decoder
progress check fails, `3` for partial collection, `1` for invalid arguments or
an output/validation limit, and `130` for interruption. A decoder failure takes
precedence over a partial collection. Exit `0` can still include unknown progress
for paused or standby players; inspect each sample and the summary. Saved watch
JSON is useful evidence but has no manifest and is not accepted by `report verify`.

## Preserve the failure before repair

Use a new directory for every bundle or capture. The parent directory must exist.

```sh
ott d1 doctor --json
ott d1 bundle --out ./d1-before
ott report verify ./d1-before --json
ott -t 45 d1 test run media-progress --duration 5 --report ./d1-progress --json
ott report verify ./d1-progress --json
```

A bundle automatically negotiates the strongest supported debug schema. Current
players can include actual engine ownership and current/retired attempt counters;
older players still provide their supported schema. It does not start performance
capture. A failed or unsupported debug observation stays visible in the bundle
alongside any successful snapshot.

For an intermittent problem, explicitly start [performance capture](performance-capture.md),
reproduce it, then export within the player's retention window:

```sh
ott d1 perf start --duration 120
# Replace SESSION with the ID printed above.
ott d1 perf stop SESSION
ott -t 90 d1 perf export SESSION --out ./d1-capture
ott report verify ./d1-capture --json
```

Use `perf start --hls-details` when the advertised HLS v2 capability is available
and per-track buffer/fragment evidence is needed. The v3 extension
adds `perf start --hls-policy-details` for initial/current buffer policy,
observed cap changes and separately labelled retry statistics. It requires a
compatible controller, CLI and the player's `perf_capture_hls_policy` v3
capability. CLI and controller support is included in the
[reviewed source revision](cli.md#choose-the-cli-source-revision). The two flags
are alternatives, and `--hls-details` retains v2 compatibility. See the
[HLS policy definitions and examples](performance-capture.md#hls-buffer-policy-and-retry-evidence)
before interpreting retry counts or buffer-cap causes.

Export completeness and file integrity are separate results: a faithfully saved
partial capture remains partial. Offline verification never contacts a player or restarts a capture.
Checksums establish internal integrity, not authenticity or physical playback.
For perf exports, `report verify` returns `0` for complete capture evidence,
`3` for valid partial/unavailable evidence and `1` for invalid files or
contradictory evidence. It does not verify the notification A–B–A trial format.

## Choose the next action from the evidence

**The controller cannot be reached.** Fix access to the configured server first.
A transport failure or repeated gateway errors while reading a receipt do not
establish that the player is offline. Inspect the configured address, local
network/VPN and controller service; use `ott server debug --json` when reachable.
Do not rotate device credentials merely because an HTTP request timed out.

**The controller answers, but a request remains pending.** Check that the intended
player is running with remote control enabled and that another installation is
not competing for the same device ID. Compare `ott -j devices`, `doctor` and the
player's own remote-control settings. A pending timeout is still not evidence
that a previously submitted mutation did not run.

**Authorization or rate limiting fails.** Use the configured role's permitted
device/action scope, or reduce observation frequency and group size. Repeated
restarts do not repair permissions or server capacity. An unsupported inspection
requires compatible player/controller builds; it must not be replaced with an
unrequested playback command.

**The player responds but media does not advance.** First check standby, pause,
loading, document visibility and media identity. Preserve a bundle, then perform
the smallest intended repair, explicitly:

```sh
ott d1 restart stream
ott d1 watch --duration 30 --interval 5
# If a player reload is actually required:
ott --receipt d1 restart
ott d1 doctor --json
```

Read the acknowledged request's outcome with `ott d1 operation REQUEST_ID --json`
when available. `accepted` or `handler_completed` alone is not proof that a
restart succeeded. A changed runtime identifies a different page instance;
correlate it with the requested reload and expected build. Another installation
sharing an ID can also answer with a different runtime. Stable media identity
and advancing position establish only decoder progress. Never automatically
replay a mutation after a missing response.

## Coverage and platform limits

The same web inspection protocol is usable by browser, LG webOS, Tauri and
Capacitor players that advertise it. The independent native lane requires a
separately bound Android maintenance agent. Unsupported platforms report missing
evidence; installing this CLI does not add OS permissions or native counters.

HTML media counters cannot by themselves establish progress of a separate native
decoder. Native process memory is not GPU or whole-device memory. A browser
visibility event is not a measurement of physical display power. Background
quiescence is application-reported and does not prove zero network traffic.
Keep these unknowns explicit when diagnosing sleep, native PiP and device reboot.

Observations can contain device identifiers, aliases and build identity. Keep
saved JSON private even though bounded diagnostics exclude media URLs,
credentials, notification text and arbitrary logs. For redirected watch output,
use a private directory and restrictive permissions, for example `umask 077`
before `ott d1,t1 watch --json > watch.json`. A Ctrl-C result is partial evidence,
not a successful full-duration run.
