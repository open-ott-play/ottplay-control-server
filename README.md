# OTT-play Control Server

A small self-hosted server that delivers remote commands to OTT-play players. Players make outbound HTTP requests, so a TV does not need to expose a listening port. Each player has its own queue and access token; a separate administrator token submits commands.

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

## Connect the player

Use a player release that includes the command-server connection feature. Open **Settings → Remote control** and enter:

1. **Server address**: the server's IP or hostname, optionally with a port, or a complete `http://` / `https://` address. A bare host defaults to HTTP port 8081.
2. **Access code**: the `token` of that player's entry in `devices`, never `admin_token`.
3. **Connect**: enable polling and check the status shown in the same dialog.

The player stores this connection only on the current installation and excludes it from settings backups/cloud transfer. It does not require the separate local HTTP listener. Web browsers require an allowed CORS origin; packaged TV pages with `Origin: null` may use the explicit `allow_null_origin` setting for designated device/bootstrap routes. This does not enable administrator or diagnostic operator routes.

## Terminal remote

Optional [DNS-SD discovery and pairing](docs/discovery.md) lets players find the
controller through their network domain and receive an individual device token
after `ott approve NAME CODE`. DNS contains service metadata only. Existing
manual connections continue to work without enabling discovery.

Use the Python CLI for player aliases, live volume, channel search, current EPG,
provider selection and supported provider configuration: [CLI guide](docs/cli.md).
Both server and player must include the request/response extension.

The Python CLI is installed from a source checkout, separately from the native
server archive. The [installation and configuration steps](docs/cli.md#installation-and-connection)
cover macOS/Linux and Windows, Python/PATH setup and all four required CLI files.
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
ott a1 restart             # reload the player
ott a1 restart stream      # restart only the current stream
ott a1 screenshot          # save a PNG after local screenshot permission
ott a1 shot -o screen.png  # short alias; never overwrites an existing file
```

`a1` is an example alias for a registered player, not a computer hostname. See
[registration and pairing](docs/cli.md#register-and-connect-a-player),
[all commands with examples](docs/cli.md#commands),
[disconnecting and revoking access](docs/cli.md#disconnect-deregister-or-revoke-a-player),
and [troubleshooting](docs/cli.md#troubleshooting).

Remote [screenshots](docs/cli.md#remote-screenshots) require an updated CLI,
controller and a player with a supported capture adapter. Grant screenshot
permission locally in the player for 10 minutes; `ott a1` reports availability
and any required local action. Screenshots are separate from telemetry capture.

To shuffle all VPortal videos matching a title and play them on repeat:

```sh
ott l vpr "wedding"
```

`vpr` shuffles the complete selection once, then repeats that queue. Run it again
for a new shuffle. `vp` plays matches in catalogue order; `vp --list` lists them
without changing playback. See [VPortal commands](docs/cli.md#commands).

### Load a saved setup

Store named setups in the private `presets` section of
`~/.config/ottplay-control/cli.json`. Each setup contains numbered M3U profiles
(playlist, archive hours and VPortal link), Plex credentials and the M3U profile
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

## Remote diagnostics

Optional [protocol 2 diagnostics](docs/remote-diagnostics.md) provides explicitly
enabled, per-runtime sessions with separate scoped operator credentials and
bounded structured telemetry. Local player consent remains mandatory. Diagnostic
control polling is independent of the existing command queue.

The [diagnostic CLI and MCP guide](docs/diagnostics-cli.md) covers installation,
operator credentials, local permission, every diagnostic command/tool, capture
and repair examples, runtime revocation and troubleshooting. `ott diagnostics --help`
lists this separate command family; ordinary player aliases and administrator
credentials do not grant diagnostic access.

## Deployment packages

- [Docker / Compose, Kubernetes, k3s, systemd, macOS and Windows](docs/deployment.md)
- [Configuration, delivery semantics and limits](docs/api.md)
- [Release and validation process](docs/development.md)

Queues are bounded, expire after 60 seconds by default, and reside in memory. Restarting the server discards pending commands. Run one replica; independent replicas do not share queues. A player acknowledges a dispatch attempt, not confirmed playback or hardware volume. Network retries are deduplicated within a running player session, not across an application restart.

This server follows the command API of the optional `local_proxy.py` example in [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), with separate administrator/device credentials and acknowledgement delivery. The old proxy remains a separate component. This project does not proxy media or change the player's HLS, EPG, or provider networking.

## License

MIT. Vendored release-tooling attribution is recorded in `NOTICE` and `LICENSES/`.
