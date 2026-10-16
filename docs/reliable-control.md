# Reliable control and playback handoff

[English](reliable-control.md) · [Русский](reliable-control.ru.md) · [Documentation index](wiki/Home.md)

These features require the controller and CLI from a revision containing this
guide. Exact settings comparison and handoff also require an updated player.
Older release archives and the historical CLI revision in the installation
guide do not acquire these features automatically. Install **all `cli/*.py`
files together**, including `preset_session.py`; preserve private configuration.

![Control, checkpoint and handoff flow](assets/reliable-control.svg)

## Enable bounded durable outcomes

Add this optional block to the private server configuration, then validate and
restart the server. Choose an absolute path on a local persistent filesystem
writable by the service account. On Unix its directory must be private (0700);
existing journal and lock files must be regular private files (0600). On Windows
restrict the directory ACL to the service account. One process owns a journal.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

The server creates missing directories with mode 0700. Limits are 1–4096 records,
60–86400 seconds and 4 MiB on disk. Retention must cover `command_ttl_seconds`.
A full journal rejects new keyed operations instead of evicting live records.
Corrupt files, an insecure Unix directory, another writer or a failed durable
write fail closed. The default remains the existing in-memory command path.

The packaged systemd unit creates `/var/lib/ottplay-control` with mode 0700.
For Compose, provision a host directory owned by UID 65532, set
`CONTROL_SERVER_STATE` to its absolute path and combine `deploy/compose.yml`
with `deploy/compose.journal.yml`. The latter adds only the writable state mount;
the root filesystem stays read-only. For Helm, provision a persistent claim
separately and set `journalExistingClaim=CLAIM_NAME`. The claim mounts at
`/var/lib/ottplay-control`; use the private subdirectory shown above. The chart
keeps one replica, `Recreate`, and `fsGroupChangePolicy: OnRootMismatch` so a
remount does not recursively widen already-created private directories. Verify
that the selected storage driver supports these ownership semantics. The chart
does not create/delete the claim, and enabling a mount does not enable the
journal: the private server config still controls that.

For `POST /api/requests`, add `idempotency_key` beside `action` and `params`.
Legacy command POSTs accept `Idempotency-Key` instead. Keys contain 16–80 ASCII
letters, digits, underscores or hyphens; generate a fresh random key for each
intent. The same owner/key with the same device, action and normalized JSON
payload returns the original ID without enqueueing again. Changing its payload
returns 409. Keys are scoped by operator ID (or the administrator), not by device.
After retention expires, the key may be accepted as a new intent: clients must
not retry an old intent outside that window.

`GET /api/operations?device_id=DEVICE` reports whether the journal is enabled,
its limits and the command TTL. Add `&key=KEY` to read an operation:

- `pending`: accepted; no retained response yet.
- `responded`: a validated player response arrived; `result_status` is `ok`,
  `rejected` or `unsupported`. The original result body still expires normally.
- `acknowledged`: a legacy consumer acknowledged the command. This does not
  prove visible playback or that a settings change was saved.
- `unknown`: the deadline passed or the server restarted before a response was
  retained. The server **never replays** this command after restart.

A repeated keyed POST returns `status: retained`, the original ID and state.
Ordinary new POSTs retain `status: queued`. Journal records contain only owner,
device, action, random IDs, a payload digest, state and deadlines. They contain
no command payloads, provider URLs, tokens, screenshots or response bodies.
Pending commands are not persisted. This is bounded deduplication and outcome
tracking, not exactly-once execution, a durable work queue or multi-server HA.

## Plan, apply, resume and verify Local/Home

Existing `ott PLAYER load PRESET` behavior is preserved. For the new workflow:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` is offline: it validates the local preset and lists the settings slots and
desired active profile without printing their contents. It does not inspect the
player. `verify` performs exact read-only comparisons and checks the selected
M3U profile. It returns nonzero if anything differs or comparison is unsupported.

`apply` requires the server journal. It creates a private checkpoint and compares
every configured provider before the first mutation. Each save compares stored
bytes to the expected values through `settings_compare`; a match skips the
write. A final verification covers Plex, all M3U slots, optional Stalker slots
and the selected M3U profile. Comparisons return a boolean, never stored secrets.

The default checkpoint is below `~/.config/ottplay-control/checkpoints/`.
Use `--checkpoint /absolute/private/path/session.json` after the mode to choose
another file. Checkpoints contain the device, preset name, hashed server/preset
revision, completed step names and one pending random operation key/deadline.
They contain no settings values. Files are atomically replaced after fsync and
locked against concurrent CLI writers. Keep the matching local preset available.

`resume` validates the same server, device and preset revision, then reads the
pending outcome. A retained response allows immediate rechecking. An uncertain
outcome stops before sending mutations until the prior command deadline, request
timeout and a small margin have elapsed. Run `resume` again after that window.
It rechecks stored settings even for completed steps, so user changes are not
mistaken for successful earlier writes. It never blindly replays a relative
command. Absolute provider/profile selection may be repeated after the guard.

An existing checkpoint is never overwritten by a new `apply`. Reuse it with
`resume`, or choose a new file for a changed preset. Separate devices have
separate checkpoints. There is no transaction or rollback spanning all players;
successful earlier writes remain when a later step fails. `verified` confirms
the player's stored values at the time of comparison, not successful network
loading or physical video/audio output.

## Give integrations only their required authority

Use [ordinary command operators](security-design.md#scoped-command-operators)
for HA, an RF bridge or an agent instead of distributing `admin_token`.
Allow exact device IDs and operations such as `input.channel_up` and
`input.channel_down`. Settings operations require explicit `settings_compare`,
`provider`, `profiles`, `status`, `profile`, `profile_settings` and
`provider_settings` permissions for this preset workflow. Handoff needs
`playback_handoff_export` on the source and `playback_handoff_import` on the
target. An operator entry combines its listed devices and actions; use separate
operator identities when the same operation must not apply to every listed
device. Tokens and diagnostics consent remain separate authorities.

Operators cannot poll player queues, submit player responses, list all devices,
read admin debug output or read another operator's result. Narrowing an action
scope also prevents reading its retained operation. Removing an operator and
restarting revokes its credential. Do not reuse an operator ID for another actor
while its old journal records remain.

## Continue on another player

```sh
ott handoff living-room bedroom
```

The source exports a version-1 live/archive reference: provider, opaque source
identity, opaque `channel-v1:<8 hex>.<8 hex>` item fingerprint and, for archive,
the current programme time. Raw provider IDs and stream URLs stay local. The target
must have the **same configured source/account**, resolve a unique matching item
in its own catalogue and obtain a fresh URL with its own local credentials.
The controller neither copies a playable URL nor changes target credentials.
The source keeps playing. A successful CLI result means the target dispatched
the request, not that its physical screen or speakers have been verified.

Version 1 supports `m3u`, `xtream` and `stalker` live/archive catalogues where the
player can supply the required identities. Unknown/ambiguous items, mismatched
sources and local policy rejection fail without a fallback by name. VOD/series,
different-account mapping, pausing the source and continuous history/favorites
synchronization are not part of this first contract.

## Validation and recovery exercises

Run `go test -race ./...`, `go vet ./...`, and
`python3 -m unittest discover -s tests`. The tests cover lost replies after a
successful write, exact comparison before resume, configuration drift, unknown
operation deadlines, two writers, corrupt journals, cross-operator reads and
malformed handoff payloads. On a staging player additionally interrupt the CLI
after a settings save, restart the controller, resume and verify; test a handoff
between matching and mismatching sources. Confirm video/audio separately.

For local cross-repository validation, with the current FOSS checkout and its
development dependencies installed, run:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

The smoke builds this Go server, compiles the full FOSS TypeScript project, and
runs its real transport, command handlers, source identity and shipped playlist
codec against the Python CLI. It checks exact settings for all three supported
providers, mismatches, live handoff with different local row IDs, archive seek
offsets, failed URL resolution and strict wire validation. Only storage, provider
policy and playback host ports are fixtures; it does not verify a browser,
physical media output or real account access. It binds to loopback, uses private
temporary files, contacts no stream URLs, and removes its files/server on exit.
