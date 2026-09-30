# Control players from the terminal

`cli/ott.py` uses Python 3 without additional packages. Commands take a short
player name followed by an action. Assign each player its own device ID
(preferably the Device UUID shown in its settings) and device token.
Do not share one token between active players: they would compete for the same
command queue.

## Installation and connection

The following setup is for macOS/Linux, where the CLI creates private files with
mode 600. On Windows, run `python cli/ott.py` and store configuration in a private
directory protected by NTFS permissions. POSIX mode 600 does not apply there;
new files inherit access rules from the parent directory's ACL.

```sh
mkdir -p ~/.local/bin ~/.config/ottplay-control
ln -s /absolute/path/to/ottplay-control-server/cli/ott.py ~/.local/bin/ott
chmod 700 ~/.config/ottplay-control
```

Create `~/.config/ottplay-control/cli.json` with mode 600:

```json
{
  "server": "http://127.0.0.1:8081",
  "player_server": "https://control.example.com",
  "server_config": "/absolute/path/to/private-server-config.json",
  "players": {"tv": "dev_your_tv_uuid", "mac": "dev_your_mac_uuid"}
}
```

`server_config` is the private server JSON containing `admin_token` and `devices`.
Keep it out of repositories and public website files. `server` is the address
used by the CLI; `player_server` is the address to enter in players. Both may use
the same HTTPS address. Set `OTT_CONFIG` or pass `--config FILE` to select another
configuration file.

To search current programmes on the shared EPG service, add this optional field
to the same CLI configuration:

```json
"epg": {"url": "https://epg.2560801.xyz/epg/v1", "source": "epg-one"}
```

With `epg` configured, all `p` commands ask the player only for its channel
metadata and catalogue identity. The CLI sends that metadata to `/current` on
the configured EPG service and receives current titles. It sends no controller
token, provider credentials, playlist URLs or stream URLs to that service.
The service URL must be HTTP(S) without credentials, a query or a fragment;
redirects are refused. Use HTTPS outside a trusted local network.

The player must support `epg_catalog` and `play_catalog`. Catalogue metadata is
limited to 2048 channels and a 512 KiB request. The service must return a complete,
fresh result for the fixed public `epg-one` source; the service and CLI clocks
must be within one minute. Other `epg.source` values are rejected. Selecting
`epg-one` searches the public guide even if the player uses a private or custom
guide; those sources are neither used nor uploaded. An unsupported player,
stale result or service failure exits with an error. It never falls back to scanning
every channel's EPG on the player. Each EPG HTTP request is limited to ten
seconds (or a smaller `--timeout`) and 2 MiB of response data.

```sh
ott devices
ott add tv DEVICE_UUID
ott pair tv
```

### Discover and pair without entering a device token

With server-side [DNS discovery](discovery.md) enabled, an unconfigured player
can find the controller and display a short pairing code. The player must
already be registered with `ott add NAME UUID`. On an existing installation,
choose **Settings → Remote control → Find command server** to start a new
pairing without changing the saved connection until approval succeeds.

```sh
ott discover
ott pending
ott approve tv ABCD2345
```

Compare the eight-character code with the one shown on the intended player.
Approval matches both its registered device ID and that code. The player then
receives its own existing access token over HTTPS, saves it and connects.
Neither DNS nor the pending list contains device tokens. Requests expire after
ten minutes, are bound to the discovered controller and fail if DNS changes
before approval or delivery. Cancelling on the player removes its request when
the server is reachable. `--json` supports `discover`, `pending` and `approve`.
An uncertain approval response is never retried automatically.

`add` creates a separate random token, saves it in `server_config` and binds the
name. Restart the command server afterwards for a regular installation. For a
server running in k3s, you can add a `kubernetes` object to `cli.json` with
`context`, `namespace`, `secret` and `deployment` fields. Then `add` updates the
Secret's `config.json` entry and restarts the Deployment. It checks
`resourceVersion` and compares local and cluster configuration to avoid
replacing someone else's changes. A restart clears all pending queues.
`alias NAME UUID` names an already registered device without a restart.

If Kubernetes provisioning is interrupted, repeat the same `ott add NAME UUID`.
The private `cli.json.pending-add.json` file records the generated access code
before the Secret changes. The CLI compares the cluster with the recorded
before/after state, resumes with the same code and removes the journal only
after a successful rollout. Conflicting configuration changes stop recovery.
Keep the recovery file and finish the operation before adding another device.
A repeated restart clears pending queues; connect the player after `add`
succeeds. Concurrent changes to other aliases and fields in `cli.json` are
preserved. Changes to the target alias, server address or
`kubernetes`/`server_config` settings require reconciliation; the journal remains
even if the rollout has already completed.

`pair` displays the existing device token, **not the admin_token**. It does not
register a device or change its credentials. Open
**Settings → Remote control → Command server** in the player and enter the
address and access code. Saving the second field starts the connection. If both
values are already saved but the client is disconnected, select **Connect** and
wait for **Connected**. Each browser origin, TV and Tauri instance has its own
settings. For a new browser address, add its exact origin to `allowed_origins`
and restart the command server.

HTTPS pages, including here.now, require an HTTPS command server. Tauri uses its
native HTTP bridge. TV browsers send outgoing XHR requests and do not need an
incoming port on the TV. Packaged TV apps with an Origin of `null` can use the
separate `allow_null_origin: true` setting, which applies only to the device API.
The central player server on ports 8443–8446 is separate from the command server.

## Commands

```sh
ott tv                      # UUID, provider, readiness, channel count and volume
ott tv 12                   # one-based channel number from s
ott tv news                 # exact channel name, otherwise a substring
ott tv play s               # channel named s, which is also a command
ott tv s                    # all channels from the active provider
ott tv s HD                 # filter by channel name
ott tv p                    # channel — current programme
ott tv p news               # find programmes and play the first matching channel
ott tv p --list news        # filter programme titles without switching channels
ott tv vp wedding          # loop all VPortal videos with matching titles
ott tv vp --list wedding   # list the matches without changing playback
ott tv v                    # current volume, 0–100%
ott tv v 35                 # absolute volume
ott tv v +5                 # relative change applied by the player
ott tv v -5
ott tv providers            # active provider marked *, zero-based indices
ott tv provider xtream      # provider ID, index or name fragment
ott tv provider-config ~/private/xtream.json
ott tv playlist https://example.com/list.m3u
ott tv random               # random channel from the current playback list
ott tv random 1 10
ott tv msg Hello
ott tv exit                 # close / standby, depending on the platform
ott --json tv s             # machine-readable output
ott --timeout 60 tv p
```

Searches for channels, programmes, VPortal titles, providers and aliases are case-insensitive,
including Cyrillic text. Exact channel names take precedence. If several
channel names match a `play` query, the CLI lists the choices without switching;
select a number.
The numbers used by `s` and `play` refer to the provider's full catalogue,
regardless of the category currently open. `random` retains the existing
behaviour of using the current playback list.

`p` includes only programmes with a title and `start <= now < end`. Channels
without current EPG are omitted. Plain `p` only lists programmes. `p TEXT`
prints every matching programme and requests playback of the first returned
channel, in provider catalogue order. `p --list TEXT` searches without playing.
An empty or whitespace-only search never switches channels. No matches means
no playback request. The switch confirmation goes to stderr, keeping stdout
in `channel — programme` format. With `--json`, the result includes a `playback`
object containing the player acknowledgement or an `error` if switching fails;
a playback failure exits with code 1 and never retries the switch automatically.

When `epg` is absent from the CLI configuration, the legacy compatibility path
reads programme data from the selected player; missing
EPG is requested through its normal guide service. Collection has a 25-second
budget. If it cannot finish, the CLI reports an incomplete programme search with
the number of channels checked and exits with code 3. This count measures search
coverage, not XMLTV download or server EPG loading progress. An empty partial
result cannot establish that no channels have matching programmes.
A filtered search still plays the first available match from
that partial result; unchecked channels might contain earlier matches. A repeated
`p --list TEXT` query can use the warmed cache without switching again. Results are
rejected if the provider changes during the query. Exit code 3 also applies to
`--json`: JSON goes to stdout and the warning goes to stderr.

The EPG query and playback are separate requests. With the EPG service configured,
playback uses `play_catalog` with the original catalogue identity and channel ID;
the player rejects a changed catalogue instead of switching a different channel.
The legacy compatibility path uses the returned channel number, so keep the
provider/catalogue unchanged between query and playback. Controller requests each
use the `--timeout` budget.

`vp TEXT` searches the player's VPortal video titles and requests a repeating
queue of all matches in catalogue order. After the last video finishes, playback
returns to the first. `vp --list TEXT` returns the same title matches without
changing playback. The filter must contain 1–1024 UTF-8 bytes after trimming
outer whitespace; empty filters are rejected. Queue numbers are one-based match
positions, independent of live channel numbers. To play a live channel named
`vp`, use `play vp`.

Both commands print `number: title` rows. JSON output contains only `items` and
`total`, plus `loop: true` and `dispatched: true` for a playback request. Stream
URLs and provider credentials are excluded. The playback confirmation goes to
stderr and means the player dispatched the queue, not that a video frame has
been rendered. No matches or a player that cannot use VPortal reports an error
for `vp`; the list command may return an empty list. Update the controller and
player to versions that support these actions. A failed or uncertain request
is never automatically submitted again.

The player collects all advertised search pages and episodes of matching series
before starting. Incomplete, timed-out or oversized searches start nothing; use a
narrower filter when a catalog limit is reached. Only natural video completion
advances the queue. Stop, manual playback or a provider change interrupts it. Each
visit resolves a fresh stream URL; an unavailable next item stops playback instead
of being silently skipped. Queues are not restored after restarting the player.

## Provider settings

A `provider-config` file contains `provider` and `settings`. Select the intended
provider with `provider` first. Supplied fields are updated; other fields are
preserved. Credentials are not echoed in status, provider lists or
acknowledgements. Save these files with mode 600 and use HTTPS outside a trusted
local network.

Xtream:

```json
{"provider":"xtream","settings":{"server":"https://provider.example","username":"YOUR_LOGIN","password":"YOUR_PASSWORD"}}
```

Stalker:

```json
{"provider":"stalker","settings":{"server":"https://provider.example/stalker_portal/c/","mac":"00:1A:79:00:00:01"}}
```

M3U:

```json
{"provider":"m3u","settings":{"playlist":"https://provider.example/list.m3u"}}
```

OTTClub:

```json
{"provider":"ottclub","settings":{"server":"provider.example","key":"YOUR_KEY"}}
```

OTTClub's `server` field takes a hostname with an optional port, without
`http://`, `https://`, a path or a query. Its driver supplies the protocol.

Settings use the existing save and reload drivers. Parental settings locks and
platform restrictions still apply; unlock the settings on the player first.
Other providers currently require their own settings UI.

## Acknowledgements and limitations

The CLI requires a server and player supporting `request_protocol=1`. Older
versions still accept legacy commands but do not answer CLI requests.
`ok` means the player handler returned a result; `dispatched` means it invoked
the normal player action. Neither confirms visible playback, PIN acceptance or
the TV's physical volume. `v` shows the value reported by the platform API. The
application may close before it can send a response to `exit`.

Commands and results are held in memory with size and lifetime limits. Retrying
a lost result in the same player session does not apply relative volume twice;
that history does not survive a player restart. After a timeout, do not repeat
changes blindly: the request may still execute before its TTL expires
(60 seconds by default).

The `--timeout` budget starts before enqueueing. HTTP waiting is limited to the
remaining time, including slowly delivered bodies; a late response is not
reported as success. A timeout stops the CLI from waiting but does not cancel
an already submitted command. Temporary network errors and HTTP 502/503/504
responses retry only the readback of the same request ID within the remaining
budget. Enqueueing is never retried automatically, even if its acknowledgement
is lost. Other HTTP errors while reading an accepted request's receipt, including
401, 403 and 429, stop the CLI with a warning that the command may have executed.
Check the player's state before deciding whether to send another command.

Checks: `go test -race ./...`, `python3 -m unittest discover -s tests`.
Run the end-to-end test from the player repository:

```sh
OTT_CONTROL_BINARY=/path/to/ottplay-control-server \
OTT_CLI=/path/to/cli/ott.py node scripts/smoke-remote-cli.cjs
```
