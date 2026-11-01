# OTT-play Control Server

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Documentation / Wiki](docs/wiki/Home.md)

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15562/badge)](https://www.bestpractices.dev/projects/15562)

[![Fuzzing: RC only](https://img.shields.io/badge/Fuzzing-RC%20only-blue)](docs/fuzzing.md)

A small self-hosted server that delivers remote commands to OTT-play players. Players make outbound HTTP requests, so a TV does not need to expose a listening port. Each player has its own queue and access token; a separate administrator token submits commands.

Optional [Home Assistant event banners](docs/home-assistant.md) use a private
long-lived token and one controller WebSocket connection to notify active
players. The guide covers filtered rules, automation YAML, Docker/Helm token
mounts, `ott ha status`, illustrated delivery flow and recovery. Notifications
respect standby and visibility; queued or polled does not mean visibly displayed.
Use the [single per-player notification preference](docs/notification-preferences.md)
to control banners and notification media together: `ott t1 notifications off`
or `ott t1,t2,t3,t4 notifications on`. This leaves IPTV/Plex playback unchanged.

Optional [reliable control](docs/reliable-control.md) adds scoped integration
credentials, bounded durable command outcomes, resumable Local/Home application
with exact settings verification, and explicit live/archive playback handoff.
The [typed settings guide](docs/player-settings.md) covers `setting KEY VALUE`,
private `--value-file` inputs, Torrent metadata/stream-addon configuration,
runtime schema discovery, conditional saves and dedicated VPortal presets.
These features require controller and CLI revisions containing this guide.
Exact settings verification and handoff also require an updated player. The
[architecture diagram](docs/assets/reliable-control.svg) shows where settings,
credentials and operation state remain.

For Torrent/Stremio lookup failures, use `ott t1 doctor --provider` to collect
safe provider, media and engine evidence in one report. The
[provider diagnostics guide](docs/provider-diagnostics.md) covers addon HTTP
outcomes, runtime freshness and saving a private report without starting playback.

The server runs as a single portable Go executable, with a pinned DNS library for optional service discovery. Release packages cover Linux amd64, arm64 and ARMv7; macOS Intel and Apple Silicon; and Windows amd64 and arm64. Container images support Linux amd64 and arm64. Kubernetes and k3s use the included Helm chart.

## Start a server

Download the matching archive from [Releases](https://github.com/open-ott-play/ottplay-control-server/releases), verify it against the release manifest, and extract it. On Windows, use `ottplay-control-server.exe` in the commands below.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` creates random, distinct credentials in a new file with owner-only permissions on Unix. It refuses to overwrite an existing file or symlink and never prints the credentials. Keep the file private; on Windows, restrict its ACL to the account running the server. `config.example.json` documents the fields and deliberately contains invalid placeholder tokens.

The default address is `0.0.0.0:8081` (all IPv4 interfaces, including LAN). Connect TVs using the server's LAN address and port. Existing configurations retain their explicit `listen` value; change it to `0.0.0.0:8081` or use `--listen 0.0.0.0:8081` to expose a previously loopback-only installation. Add the player's **exact page origin** to `allowed_origins`, for example `http://192.168.1.20:8443`. Scheme, hostname and port must match; do not append a path. Empty origins allow non-browser clients only. Configuration changes take effect after restart. Device and administrator credentials are still required.

HTTP is supported for older TV browsers on a trusted LAN. Use HTTPS when the connection crosses an untrusted network: `serve --config config.json --tls-cert server.crt --tls-key server.key`, or terminate TLS at a reverse proxy. HTTPS player pages cannot connect to a plain HTTP server in a browser. Native applications also retain their operating system transport policy; this setting does not relax it.

Direct HTTPS validates the supplied certificate chain before opening the listener:
RSA keys must be at least 2048 bits, ECDSA keys at least 224 bits, or Ed25519.
Replace an undersized certificate before upgrading. See the
[TLS certificate policy and its scope](docs/security-design.md#direct-https-certificate-policy).

## Connect the player

Use a player release that includes the command-server connection feature. Open **Settings → Remote control** and enter:

1. **Server address**: the server's IP or hostname, optionally with a port, or a complete `http://` / `https://` address. A bare host defaults to HTTP port 8081.
2. **Access code**: the `token` of that player's entry in `devices`, never `admin_token`.
3. **Connect**: enable polling and check the status shown in the same dialog.

The player stores this connection only on the current installation and excludes it from settings backups/cloud transfer. It does not require the separate local HTTP listener. Web browsers require an allowed CORS origin; packaged TV pages with `Origin: null` may use the explicit `allow_null_origin` setting for designated device/bootstrap routes. This does not enable administrator or diagnostic operator routes.

## Terminal remote

Updated players automatically report their current media and playback state.
`ott l` shows live status; `ott l state` reads the controller's last report and
`ott l history` reads recent changes, including when the player is offline.
See [retained playback state](docs/player-state.md) for freshness, persistence,
operator access and troubleshooting.

Players with an owned native Torrent service also expose a typed
[Torrent catalog](docs/torrent-catalog.md): inspect metadata, add or refresh
entries, select a file, and export/import the catalog with optional global
Favorites. Run `ott t1 torrent --help`; support is discovered from the player.

For locally operated discovery, use `ott t1 torrent indexer --help`.
[Local Torznab, Jackett and Prowlarr support](docs/torrent-indexer.md) includes
write-only source configuration, explicit search pages and metadata preparation
through the owned companion. The controller and CLI do not proxy Torrent video.

Save reusable player groups with `ott group add tt t1,t2,t3,t4` and
`ott group add oo o1,o2,o3,o4`. Then use `ott tt R`, `ott oo provider m3u`,
or `ott tt,oo standby`. `ott groups` lists them; `ott group set` updates a
group and `ott group remove` removes it. See [saved player groups](docs/cli.md#saved-player-groups)
for configuration, examples and per-player failure behavior.

The [illustrated Android OTA guide](docs/android-ota.md) distinguishes ordinary
Capacitor Wi-Fi updates (`ott DEVICE update --variant api22|full`, latest beta by
default; `--version` selects a release) from the separate rooted agent. It explains signed agent
and APK/helper updates, recovery and verification of the installed result.

The [Android ADB guide](docs/android-adb.md) covers USB access through a workstation
or Synology, screen and keyboard control, screenshots, logs, APK installation
and normal reboot, including limitations on older tablets.

Optional [DNS-SD discovery and pairing](docs/discovery.md) lets players find the
controller through their network domain and receive an individual device token
after `ott approve NAME CODE`. DNS contains service metadata only. Existing
manual connections continue to work without enabling discovery.

The optional [durable device registry](docs/device-registry.md) can register an
unknown player after administrator approval, retain approved claims across
restarts, and revoke or rotate credentials without rewriting the static Secret.
Initialize its private persistent store explicitly before enabling it.

[Hosted device transport](docs/device-transport.md) carries device polling,
acknowledgements, notifications, pairing and runtime diagnostics through one
bounded JSON POST endpoint. Existing credentials, approval and route limits apply.

Use the Python CLI for player aliases, live volume, channel search, current EPG,
provider selection and supported provider configuration: [CLI guide](docs/cli.md).
Both server and player must include the request/response extension.

For physical LG television power, system volume and app launch, use the separate
[native TV controls](docs/television.md): `ott l tv volume 25`, `ott l tv off`
or `ott -t 60 l tv go`. They work while the player page is closed, prefer direct
webOS control and use Home Assistant only as a configured fallback.
Configured app aliases also support `ott -t 90 lg2 tv go plex` without changing
the default OttPlay app. The [Bash startup shortcuts](docs/shell-shortcuts.md)
provide `coco` for OttPlay and `cocop` for native Plex, with bounded readiness
checks and playback confirmation.

The Python CLI is installed from a source checkout, separately from the native
server archive. The [installation and configuration steps](docs/cli.md#installation-and-connection)
cover macOS/Linux and Windows, Python/PATH setup and the required CLI modules.
They pin a reviewed source commit containing Local source commands, preset
bindings, workbench, offline verification, [local EPG fallback](docs/local-epg.md)
and the explicit [stateful archive proxy policy](docs/cli.md#stateful-archive-proxies);
stable CLI v0.1.0 and native server
archives do not include those commands. [Update or roll back the CLI](docs/cli.md#update-select-or-roll-back-the-cli)
without replacing an existing checkout, launcher or private configuration.
Local launchers can use the read-only [playlist resolver](docs/cli.md#local-playlist-resolver)
to search channels, current programmes and playable archives without a controller.
Use [finite group monitoring](docs/monitoring-runbook.md) to distinguish controller
access failures, unavailable observations, standby and decoder progress before
repairing a player. Saved diagnostic bundles and performance exports can be
verified offline.
The [HLS policy capture extension](docs/performance-capture.md#hls-buffer-policy-and-retry-evidence)
is included in that reviewed CLI source. It adds explicit opt-in evidence for
buffer starvation, distinguishing constructor settings, observed cap changes
and mutable retry statistics. Capture also requires matching controller and
player support.
Once installed and configured:

```sh
ott --help                 # all CLI command families
ott devices                # controller registrations and last contact
ott a1                     # this player's status and available controls
ott a1 prev                # previous channel in the active category (previous also works)
ott a1 next                # next channel, wrapping at the end of the category
ott a1 channels "РЕН"       # list channel-name matches without playing
ott a1 "РЕН ТВ HD"          # search and play a matching channel
ott a1 volume 35            # vol/v are equivalent command aliases
ott a1 aspect               # current Fit/Fill mode and saved-setting readback
ott a1 aspect fill          # fill the screen proportionally, cropping the edges
ott a1 aspect "Fit to screen" # full mode name; preserve the whole picture
ott a1 restart             # reload the player
ott a1 restart stream      # restart only the current stream
ott a1 standby             # stop playback and enter player standby; stay connected
ott a1 go                  # leave standby and start configured playback (start/wake also work)
ott a1 screenshot          # save a PNG; browser builds need a selected capture source
ott a1 shot -o screen.png  # short alias; never overwrites an existing file
ott a1 test run media-progress --report ./a1-progress
ott report verify ./a1-progress --json  # re-evaluate saved evidence offline
```

`a1` is an example alias for a registered player, not a computer hostname. See
[registration and pairing](docs/cli.md#register-and-connect-a-player),
[all commands with examples](docs/cli.md#commands),
[disconnecting and revoking access](docs/cli.md#disconnect-deregister-or-revoke-a-player),
and [troubleshooting](docs/cli.md#troubleshooting).

Use an explicit comma-separated list to run the same player command on several
installations:

```sh
ott t1,t2,t3,t4 R               # independently choose a random channel on each
ott t1,t2,t3,t4 volume 35
ott t1,t2,t3,t4 standby
ott t1,t2,t3,t4 go
ott t1,t2,t3,t4 restart stream
ott -j --receipt t1,t2,t3,t4 caps
```

The CLI resolves all names first, runs the command once per distinct player and
reports each result. A failure on one player does not undo successful changes on another.
The [player-group guide](docs/cli.md#run-a-command-on-several-players) covers
settings, Plex, presets, diagnostics, separate output files and partial failures.

For an always-on player, use [standby and go](docs/cli.md#stop-playback-while-keeping-the-player-online)
to stop playback and wake it later without exiting the app. `stop` remains an
alias for `standby`; `start` and `wake` remain aliases for `go`. Remote control
must stay connected, so standby is not a promise of zero device network traffic.
`ott a1` shows the operations advertised by that player. An accepted command is
not confirmation that shutdown or playback startup has finished.

[Aspect commands](docs/cli.md#aspect-ratio-fit-and-fill) require an updated CLI,
controller and a player advertising the `aspect` capability. A change is applied
after acknowledgement; read `aspect` again to confirm the mode and whether it
was saved. Fill crops proportionally rather than stretching the picture.

Remote [screenshots](docs/cli.md#remote-screenshots) require an updated CLI,
controller and a player with a supported capture adapter. Enabling the player's
Remote control connection authorizes screenshots and diagnostics for that
controller; native screenshots need no separate permission toggle. Browsers
still require their local source picker. Images can include player settings,
PIN screens and visible credentials, so connect only to a controller you trust.
`ott a1` reports availability and any required source selection. Screenshots
are separate from structured telemetry capture.

To shuffle all VPortal videos matching a title and play them on repeat:

```sh
ott l vpr "wedding"
```

`vpr` shuffles the complete selection once, then repeats that queue. Run it again
for a new shuffle. `vp` plays matches in catalogue order; `vp --list` lists them
without changing playback. See [VPortal commands](docs/cli.md#commands).

For an explicit Plex sequence using the player's saved Plex configuration:

```sh
ott l plex preview 78777 78776 78775  # check availability without starting playback
ott l plex play 78777 78776 78775     # starts the first item from zero, in this order
ott l plex queue                     # current item and preparation/playback state
ott l plex next                      # advance once; no wrap at the last item
ott l plex stop                      # stop and clear this queue
ott l plex libraries                 # list libraries (requires plex_library support)
ott l plex shuffle "Tom and Jerry"   # shuffle up to 500 items from the library
ott l plex library --preview id:16    # read-only check of an explicit library
```

The CLI waits up to 45 seconds by default. Writing
`ott -t 45 l plex shuffle "Tom and Jerry"` makes that same response timeout explicit;
it does not limit how long the videos play. Large Plex libraries can take longer
than a short 10–15 second timeout to collect. Put `-t SECONDS` (or
`--timeout SECONDS`) before the player alias. See
[Plex library timeouts](docs/cli.md#plex-libraries-by-name) for preview examples
and what to do if a playback request times out.

Plex queues require a CLI, controller and player implementing `plex_queue`.
`plex play --shuffle ID...` shuffles supplied IDs once. Queues stop after the
final item unless existing kiosk policy repeats them. While a queue remains,
ordinary `next` and `prev` address it; `plex stop` clears it. A preview never
switches the provider or replaces current playback. See
[Plex queue commands](docs/cli.md#ordered-plex-queues) for limits and readiness.
Library lookup additionally requires an updated player and controller advertising
`plex_library`; it uses the player's saved Plex connection and rejects ambiguous
or incomplete selections. With updated CLI, controller and player support, a
library larger than 500 items starts a 500-item queue and prints a limit warning.
The first 500 items are selected in library order, then shuffled when requested;
remaining items are not appended automatically. See
[Plex libraries by name](docs/cli.md#plex-libraries-by-name).

### Play connected local folders

The [reviewed CLI source](docs/cli.md#choose-the-cli-source-revision) includes
`local` commands and Local source bindings in named presets. Use a controller
with `local_media` support and a player advertising the requested operation.
Session-only `play`/`shuffle` additionally require the coordinated CLI/controller/
player update and `local_media_session` handshake; controller beta22 predates it:

```sh
ott t1 local sources
ott t1 local shuffle Movies
ott t1 local shuffle /Volumes/data/Movies/
ott t1,t2 local shuffle Movies
ott t1,t2,t3,t4 local shuffle ~/v/data/FFFF/
```

Remote `play`/`shuffle` (also `offline` and groups) keep the playback selection,
order and position in memory. They do not replace saved provider/profile/startup
settings or become persistent through autosave or export. Explicit UI selection
and native source bindings remain separate persistent actions. The new CLI
refuses playback if the controller or player cannot prove session support.

With session and progressive support on the controller and player, the command
can return after discovering playable items while the selected folder continues
scanning. The CLI labels that count as partial; an accepted request does not
confirm decoded video. Session-capable players without progressive support wait
for a complete selected-source scan.

Folder names are exact. Absolute paths refer to the target player's filesystem
and require native path support; browser players use folders already selected
and permitted on that browser. See [Local folders](docs/cli.md#local-folders-and-shuffled-playback)
for source IDs, limits, aliases and examples, and [named setups](docs/cli.md#named-setups)
for delivering saved Local source bindings with `ott PLAYER load PRESET`.

### Save Local videos profiles

With a current player and controller, select **Local videos** with `provider local`
(the compatible protocol identifier remains `offline`):

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Connect files/folders on the player first. Save records their configuration and
selected video without starting playback. Export transfers descriptions only;
media and OS/browser access grants remain on the device. Another installation
requires local binding, and loading does not imply playback was confirmed.
See [Local videos profiles](docs/cli.md#local-videos-profiles) for setup,
capabilities, portable-format limits and troubleshooting.

### Save the current item across profiles

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm id:00000000-0000-4000-8000-000000000001 # replace with an actual listed UUID
ott l fav global current            # inspect the current portable item without saving
ott l fav legacy list               # inspect legacy-only bookmarks explicitly
```

With `favorites_global_items` support, ordinary `fav` commands use the complete
global list across providers and profiles. Add/remove use the existing guarded
global transfer; playback resolves the selected receiver UUID against the current
list and never substitutes a channel number or similar name. Missing items remain
visible. When that marker is absent, the CLI announces legacy mode; it never falls
back after an error on the global path. `fav legacy` accesses old bookmarks
explicitly, including the legacy-only count shown below the global list. See
[favorites across profiles](docs/cli.md#favorites-across-profiles) for aliases,
limits, acknowledgement semantics and troubleshooting.

Global support also requires qualified physical storage and shared Web Locks;
updating or reloading alone cannot provide those facilities on every host.
Without them, the existing per-source UI remains available, but legacy CLI
commands are limited to the advertised subset: list/play, or none when the
stored format cannot be read safely. Add/remove can remain unsupported.
Restricted command operators need explicit permissions for the new global
operations; existing administrator credentials need no grant migration.

### Transfer a mixed favorites document

On players advertising `favorites_global`, global documents preserve ordered
favorites from multiple providers, profiles and sources. Public source aliases
and exact item locators travel with each entry; provider credentials and media
URLs do not. The receiver resolves its own sources and reports missing,
ambiguous or unsupported entries in the preview.

```sh
ott t1 fav global describe
ott t1,t2 fav global collect Home         # all snapshots, then one local profile update
ott presets Home fav global export favorites.json
ott presets Home fav global import favorites.json merge --dry-run
ott t2 fav global diff Home
ott t2 fav global load Home replace --dry-run
ott t2 fav global load Home replace       # apply the validated plan
ott t2 fav global status OPERATION_UUID   # inspect retained evidence; never replay
ott t1,t2 fav global export favorites.json # favorites.t1.json and favorites.t2.json
ott t2 fav global recovery              # list unresolved transfer records
ott t2 fav global ack OPERATION_UUID --dry-run
ott t2 fav global ack OPERATION_UUID --confirm PLAN_SHA256
```

`merge` preserves the existing order and appends new identities. A conflicting
title for the same identity blocks a local merge; `replace` deliberately uses
the incoming document's order and titles. Local documents live in the separate
`preset_global_favorites` section of the private CLI configuration. Offline
`validate`, `diff FILE` and `restore BACKUP [--dry-run]` operate on that document
only; restore preserves the current provider settings and other profiles.

Ordinary `ott t1 load Home` does not yet deliver either favorites companion and
stops before changing a preset that contains one. Use `ott t1 load Home
--settings-only` explicitly for settings, then a separate `fav global load` for
global favorites. Existing scoped `fav load` remains separate; ordinary `fav play`
uses the global list when supported. No command silently substitutes a source or retries an
uncertain write.

Recovery has its own capability. An acknowledgement requires one player and
the exact plan hash from a fresh preview; it records acceptance of uncertainty,
never proof that the original transfer succeeded. New global writes require a
complete recovery list with no unacknowledged records. Read-only inspection,
exports and previews do not clear those records or wake the player.

### Load a saved setup

Store named setups in the private `presets` section of
`~/.config/ottplay-control/cli.json`. Each setup contains numbered M3U profiles
(playlist and archive hours), Plex credentials and the M3U profile
to select when loading finishes. See the [configuration example](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

The CLI validates the whole setup first, then waits for each player's
acknowledgement before sending the next change. It handles provider loading,
saves Plex and the M3U slots, and selects the configured M3U profile. An error
stops the sequence and reports the confirmed steps; an uncertain write is never
repeated automatically. No additional profile service or controller restart is
needed. Keep this configuration private: it contains provider credentials.

## Send a command

This example reads the administrator token from the local private configuration without placing it in the URL or command-line arguments:

```python
import json
import urllib.request

with open("config.json", encoding="utf-8") as stream:
    config = json.load(stream)
request = urllib.request.Request(
    "http://127.0.0.1:8081/api/webhook/commands?device_id=living-room",
    data=json.dumps({"command": "set_volume", "volume": 35}).encode(),
    headers={
        "Authorization": "Bearer " + config["admin_token"],
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.status)
```

Other commands select a channel by number or name, choose a random channel, switch providers, show a message, change an M3U playlist where supported, or exit the player. Availability depends on the player platform and current provider. See [the API contract](docs/api.md).

With a player supporting the standalone VPortal provider, `ott a1 provider
vportal` selects its own 15 profiles. Use `profile N vportal LINK`, `profile N
name NAME` or a private `profile-config N FILE` containing `name` and `vportal`,
then select the configured slot with `profile N`. Existing M3U profiles stay
separate. `profiles` reports metadata without exposing cabinet links or keys.

After starting a VPortal film or episode queue, `ott a1 kiosk on` locks that
selection. The player preserves the queue and its position across reloads and
resolves media URLs again for recovery. Use `kiosk off` to choose another title
or change profiles, then `kiosk on` to lock it. The kiosk receipt shows the title
and episode count; provider requests and stream URLs stay on the player.

Use `ott a1 kiosk on --strict` for child-facing playback: a local tap shows the
video footer. Updated core players allow tapping or dragging its timeline to
seek within the current VPortal/Plex episode; other local controls remain locked.
Older players, channel kiosks and wrappers that intercept timeline gestures
retain a read-only footer. An existing lock keeps its target. Release it with
`ott a1 kiosk off` from the controller.
On Android websites, system exit protection requires separate app pinning with
a PIN; see [kiosk mode](docs/cli.md#kiosk-mode).

## Remote diagnostics

Start with the [read-only workbench quick start](docs/workbench.md#quick-start)
for `doctor`, UI/media `inspect`, operation receipt lookup, evidence bundles,
`health`, web/native `media-progress` and offline `report verify`. It uses the
existing administrator CLI configuration and player aliases. The native lane is
for the separately provisioned Android maintenance agent; Tauri players use web
inspection. Reports distinguish decoder progress from physical screen/sound
verification. See [workbench troubleshooting](docs/workbench.md#troubleshooting)
for missing capabilities, unavailable lanes and incomplete evidence.

Optional [protocol 2 diagnostics](docs/remote-diagnostics.md) provides explicitly
enabled, per-runtime sessions with separate scoped operator credentials and
bounded structured telemetry. The enabled player connection supplies local
authorization without another trust prompt; each capture still has a bounded
session lease. Diagnostic
control polling is independent of the existing command queue.

The [diagnostic CLI and MCP guide](docs/diagnostics-cli.md) covers installation,
operator credentials, connection setup, every diagnostic command/tool, capture
and repair examples, runtime revocation and troubleshooting. `ott diagnostics --help`
lists this separate command family; ordinary player aliases and administrator
credentials do not grant diagnostic access.

## Deployment packages

- [Android tablet: Plex strict kiosk and startup](docs/android-kiosk-setup.md)
- [Shared Android USB management and boot recovery](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS and Windows](docs/deployment.md)
- [Configuration, delivery semantics and limits](docs/api.md)
- [Release and validation process](docs/development.md)

Queues are bounded, expire after 60 seconds by default, and reside in memory. Restarting the server discards pending commands. Run one replica; independent replicas do not share queues. A player acknowledges a dispatch attempt, not confirmed playback or hardware volume. Network retries are deduplicated within a running player session, not across an application restart.

This server follows the command API of the optional `local_proxy.py` example in [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), with separate administrator/device credentials and acknowledgement delivery. The old proxy remains a separate component. This project does not proxy media or change the player's HLS, EPG, or provider networking.

## License

MIT. Vendored release-tooling attribution is recorded in `NOTICE` and `LICENSES/`.

## Project maintenance

See [contribution and test requirements](CONTRIBUTING.md), the
[security reporting policy](SECURITY.md), [security design](docs/security-design.md),
and the [OpenSSF evidence and remaining criteria](docs/openssf-evidence.md).

Plex queues accept up to 500 IDs on current players. Use `ott f10 plex play --shuffle ID...` to shuffle once, then `ott f10 kiosk on --strict` while the Plex provider is selected to loop and lock that queue. `ott f10 kiosk off` releases the lock. Older players retain their advertised queue limit.

## Release-candidate fuzzing

Public release candidates run bounded native fuzzing of the input boundaries
described in the [fuzzing guide](docs/fuzzing.md). Mutation campaigns do not run on
PRs, betas or nightlies. The guide covers scope, budgets, evidence and regression
reproduction.

## Community and security evidence

See [contributing](CONTRIBUTING.md), [governance](GOVERNANCE.md), the [Code of Conduct](CODE_OF_CONDUCT.md), [roadmap](ROADMAP.md) and [OpenSSF evidence](docs/openssf-evidence.md). The evidence index distinguishes supported security profiles, actual test results and remaining Silver requirements.
