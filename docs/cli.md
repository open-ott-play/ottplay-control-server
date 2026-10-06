# Control players from the terminal

The CLI uses Python 3 and its standard library; no `pip install` is needed.
Keep all four files together: `ott.py`, `programme_search.py`, `diagnostics.py`
and `diagnostics_mcp.py`. Python 3.12 is the version used by CI. Commands take a short
player name followed by an action. Assign each player its own device ID
(preferably the Device UUID shown in its settings) and device token.
Do not share one token between active players: they would compete for the same
command queue.

Use this guide for the terminal client. The [deployment guide](deployment.md)
covers installing, starting and stopping the separate Go command server;
the [player connection guide](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/remote-command-server.md)
covers the settings on the TV, browser, Tauri or Capacitor installation.

- [Install and configure the CLI](#installation-and-connection)
- [Register and connect a player](#register-and-connect-a-player)
- [Disconnect, deregister or revoke a player](#disconnect-deregister-or-revoke-a-player)
- [Help, options and output](#help-options-and-output)
- [Player commands and aliases](#commands), [programme search and archives](#programme-search-configuration-and-archives)
- [Provider settings](#provider-settings), [M3U profiles](#m3u-profiles) and [named setups](#named-setups)
- [Restarts](#restarting-playback-or-the-player), [input and playback controls](#capabilities-input-and-playback-control), [kiosk mode](#kiosk-mode)
- [Scoped diagnostics and MCP](diagnostics-cli.md)
- [Troubleshooting](#troubleshooting)

## Installation and connection

### Install on macOS or Linux

Install Python 3 and Git first. The native server release archives contain the
Go server, **not** the Python CLI. Obtain the CLI from a source checkout or the
source archive for the same release. This first-install example pins a published
release containing the CLI commands in this guide. `prev`/`previous`/`next`
require controller v0.1.0-beta.42 or newer and a player advertising the matching
playback operations in `caps`:

```sh
python3 --version
mkdir -p "$HOME/.local/share" "$HOME/.local/bin" "$HOME/.config/ottplay-control"
git clone --branch v0.1.0-beta.42 --depth 1 \
  https://github.com/open-ott-play/ottplay-control-server.git \
  "$HOME/.local/share/ottplay-control-server"
ln -s "$HOME/.local/share/ottplay-control-server/cli/ott.py" "$HOME/.local/bin/ott"
export PATH="$HOME/.local/bin:$PATH"
ott --help
ott diagnostics --help
chmod 700 "$HOME/.config/ottplay-control"
```

Keep the checkout after making the symlink; moving or deleting it breaks `ott`.
Persist the PATH line in your shell's startup file (`~/.zshrc` for interactive
zsh or the appropriate bash startup file), then open a new terminal. `command -v ott`
shows which installation is selected. If `ott` already exists, inspect it before
changing it; the example intentionally does not overwrite an existing command.
From any checkout you can instead run `python3 /absolute/path/to/cli/ott.py --help`.
If copying the files out of a source archive, copy all four together and make
`ott.py` executable with `chmod u+x /absolute/path/to/cli/ott.py` before linking it.

### Install on Windows

Use a private source checkout and invoke the script through Python:

```powershell
py -3 --version
git clone --branch v0.1.0-beta.42 --depth 1 https://github.com/open-ott-play/ottplay-control-server.git "$env:LOCALAPPDATA\ottplay-control-server"
py -3 "$env:LOCALAPPDATA\ottplay-control-server\cli\ott.py" --help
py -3 "$env:LOCALAPPDATA\ottplay-control-server\cli\ott.py" diagnostics --help
```

In the remaining examples replace `ott` with that Python invocation. Store
configuration in a directory protected by NTFS permissions for your account;
POSIX mode 600 does not establish Windows access control. Files inherit the
parent directory's ACL. Use `--config C:\private\cli.json` before the player name
if you choose a location other than the default under your home directory.

### Configure the administrator client

Start and validate the command server using the [deployment guide](deployment.md).
The CLI is a one-command process, not another background server. Create
`~/.config/ottplay-control/cli.json`; this is separate from the server's `config.json`:

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

Replace the example paths and UUIDs with the actual configuration. `server_config`
must point to a private local copy of the configuration used by that controller,
even when the controller runs remotely. Keep that copy synchronized after device
or credential changes. Loopback `127.0.0.1` always means the machine executing the
command: a TV cannot reach your Mac's server using its own loopback address.
On macOS/Linux protect the CLI configuration:

```sh
chmod 600 "$HOME/.config/ottplay-control/cli.json"
ott devices
```

Use mode 600 for a server-configuration copy read only by your account. If that
same file is consumed by a service/container account, preserve the restricted
ownership and group-read permissions required by the [deployment guide](deployment.md).
`ott add` replaces the local file with mode 600; reapply those service permissions
before restarting a service that reads it directly.

`devices` lists server registrations, last contact and pending commands. It does
not discover every player on the LAN, and it can succeed even if no player is
currently online. For an already registered device, use `ott alias tv DEVICE_UUID`;
for a new one, follow [registration](#register-and-connect-a-player).

## Register and connect a player

For each browser installation, TV or native app, open its settings and read its
Device UUID. Register that exact UUID under an unused local alias. The same Mac
can run the CLI, the command server and several player instances; the alias
selects a player registration, not a computer hostname.

```sh
ott devices
ott add tv DEVICE_UUID
# For an ordinary server, validate and restart it here before connecting the player.
ott pair tv
```

`add` saves a new registration and a random device token in `server_config`.
It also records `tv` in the CLI's `players` mapping. On a regular installation,
validate the changed server configuration, copy it to the server if needed and
restart the server as described in [deployment](deployment.md). Merely editing a
local copy does not update a remote controller. Kubernetes provisioning is
described below and performs its own Secret update and restart.

For a UUID already present in `server_config`, `ott alias tv DEVICE_UUID` assigns
an alias without changing server credentials. Adding the same UUID again also
keeps its existing token; `add` is not token rotation. Aliases are case-insensitive,
up to 32 Latin/Cyrillic letters, digits, underscores or hyphens. Use distinct names
such as `a1` and `living_room`, avoiding command names such as `devices` or `presets`.
An existing alias cannot be reassigned to another UUID without editing `cli.json`.

`pair` prints the device access code, so use it in a private terminal. Enter that
address and code in the player's command-server settings, select **Connect**, and
wait for **Connected**. Then verify the connection before sending a change:

```sh
ott devices
ott tv                      # status and currently available controls
ott tv s                    # read-only channel list
ott tv 12                   # select catalogue row 12 when ready
```

The CLI and Go server can run in different places. No SSH connection to the player
is required for these commands: the connected player polls the controller.

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

For a server running in k3s, you can add a `kubernetes` object to `cli.json` with
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

## Disconnect, deregister or revoke a player

These are different operations. There is currently no `ott remove`, `delete`,
`deregister` or `unpair` command, and no CLI command that remotely grants local
diagnostic consent.

### Temporarily disconnect or forget a connection

On the player, open **Settings → Remote control → Command server → Disconnect**.
Polling stops, but the saved address and access code remain; **Connect** resumes
them. To forget the connection on that installation, clear the server address and
access code in the same screen. Clearing either field also stops polling.
**Cancel pairing** cancels an in-progress discovery pairing, not an already
registered device. Disconnecting, clearing settings or uninstalling a player does
not revoke its token at the server.

### Remove or rename only a local alias

Edit the `players` object in the selected `cli.json`. For example, replacing
`"tv": "dev_your_tv_uuid"` with `"living_room": "dev_your_tv_uuid"` renames the
local shortcut; deleting that entry removes it. No controller restart is needed.
Other aliases and a full registered device ID can still address the same queue:

```sh
ott alias living_room DEVICE_UUID  # add another name for an existing registration
ott living_room
ott DEVICE_UUID                   # also works without a local alias
```

Removing an alias alone does not deregister the device or change its credentials.
If another administrator has their own `cli.json`, their aliases are independent.

### Revoke server access permanently

Use this when retiring a player or when its device token must no longer work:

1. Record the exact device ID with `ott devices` and make a private backup of the
   server configuration. If a Kubernetes `add` recovery journal exists, finish or
   reconcile that operation before editing its configuration.
2. Remove that device's complete `{id, token}` entry from the server's `devices`
   array. Remove the ID from every `diagnostics.operators[].device_ids` grant;
   remove operators left with no authorized devices. The validator requires
   **1–64 devices** and nonempty operator scopes. To retire the last device and
   controller, stop/uninstall the service instead of starting it with an empty list.
3. Validate the edited configuration. Deploy that exact configuration to the
   actual server and restart it using the [deployment procedure](deployment.md).
   In Kubernetes update the Secret as well as any private local copy; a rollout
   restart alone does not change its contents. Keep unrelated devices, settings
   and deployment overrides intact.
4. Remove all local `players` aliases mapped to the retired ID, and disconnect or
   clear the saved connection on the player when it is accessible.
5. Run `ott devices` against the restarted controller: the ID must be absent.
   Its old device token is now rejected. Deleting only the local copy or alias
   would not produce this revocation.

For the local files, validation and the final registration check are:

```sh
./ottplay-control-server validate --config /absolute/private/config.json
# Apply the validated file and restart using the procedure for your deployment.
ott devices
```

Server restarts discard pending commands, pairing requests and diagnostic
sessions for every device. They cannot undo actions already performed by a player.
Removing the server registration does not erase its local playlists or player
settings. Local diagnostic trust and scoped operator access have their own
[revocation procedure](diagnostics-cli.md).

To rotate a device token without removing its registration, keep its ID, replace
its token in the private server configuration with a newly generated URL-safe
random token (32–256 characters, distinct from every other token), validate,
deploy and restart. Update the private CLI copy before using `ott pair NAME` to
enter the new code on the intended player. The old token then fails, and `add`
must not be used as a substitute for rotation. The administrator token and
diagnostic operator credentials are separate credentials.

## Help, options and output

`ott`, `ott help` and `ott --help` print help without a configuration or network
connection. There is no `ott --version` or built-in CLI update command; record
the source tag/commit used for installation. `ott PLAYER caps` reports the
**player's** version, while the Go server has its own `version` subcommand.

Options for the ordinary CLI must precede `PLAYER` or the management verb:

```sh
ott --help
ott -c /private/cli.json tv
ott --config /private/cli.json --timeout 60 --json tv status
ott -t 60 -j tv channels
ott --refresh tv p --list "Кино"
```

- `-c FILE` / `--config FILE`: configuration path; otherwise `OTT_CONFIG`, then
  `~/.config/ottplay-control/cli.json`.
- `-t SECONDS` / `--timeout SECONDS`: finite value from 1 to 300; default 45.
  Multi-step searches/setups can take longer than one request budget; see their
  sections below. Increasing it does not extend a command's server-side TTL.
- `-j` / `--json`: machine-readable results for player commands and for
  `presets`, `discover`, `pending`, `approve`. `devices`, `add`, `alias` and `pair`
  still print text, even with this option; `pair` includes the private device token.
- `--refresh`: bypass EPG history/search and archive-probe caches on the
  configured central EPG path. It does not reload the player or playlist.
- `-h` / `--help`: display the command summary.

`--list` / `-l` is different: it follows `p`, `vp` or `vpr` before the title.
Place global flags before `PLAYER`, because everything after it is parsed as a
player command or search text. Quote multiword names and URLs containing shell
characters. Exact aliases such as `vol` and `volume` are interchangeable; an
arbitrary shortened command is a channel search, not a command abbreviation.

Management commands address the controller or local configuration:

```sh
ott devices                    # registered devices, last contact and queue size
ott add tv DEVICE_UUID          # register a device and bind an alias
ott alias lounge DEVICE_UUID    # another alias for an existing registration
ott pair tv                    # privately display the player's address and token
ott discover                   # optional controller DNS discovery metadata
ott pending                    # pending pairing requests and approval codes
ott approve tv ABCD2345         # approve the code actually displayed on this player
ott presets                    # names of saved local setups, without credentials
```

`ott diagnostics` is a separate command family with separate credentials and
options: `diagnostics` must be the first argument. Use, for example,
`ott diagnostics --server https://control.example.com --token-file /private/operator.token runtimes --device DEVICE_UUID`.
Do not prefix it with ordinary `--config` or `--json` flags. Its full command and
MCP tool reference is in [Diagnostics CLI and MCP](diagnostics-cli.md).

Ordinary CLI exit codes are `0` for a successful result, `1` for configuration,
transport or command errors, `2` for argparse syntax errors, `3` for incomplete
legacy programme searches, and `130` for an interrupted running command. In
particular, exit `3` can accompany a dispatched choice from the partial results;
use `p --list` when investigating without playback. Diagnostics has its own
output and errors. Human warnings and playback confirmations may use stderr;
do not merge stderr into stdout when consuming JSON.

### Update or uninstall the CLI

For the Git installation above, choose a published `RELEASE_TAG` and update a
clean checkout. Review `git status --short` first and preserve any local edits:

```sh
cd "$HOME/.local/share/ottplay-control-server"
git status --short
git fetch origin tag RELEASE_TAG
git switch --detach RELEASE_TAG
ott --help
ott diagnostics --help
```

The existing symlink follows the checkout. For a copied installation replace all
four sibling Python files together from one version. Keep `cli.json`, presets
and server credentials outside the checkout; updating CLI files does not update
or restart the command server, native apps or players already open in a browser.
After updating a hosted player, reload it and inspect `ott PLAYER caps`.

To uninstall the symlink installation, remove only the `~/.local/bin/ott` link you
created and, when no longer needed, its source checkout. Keep private configuration
until any required deregistration is complete. Uninstalling the CLI does not
stop the controller or revoke player/operator credentials.

## Commands

```sh
ott tv                      # player status and available control commands
ott tv 12                   # one-based channel number from s
ott tv prev                 # previous channel in the current category; wraps
ott tv previous             # full spelling of prev
ott tv next                 # next channel in the current category; wraps
ott tv news                 # list name matches and play a random matching channel
ott tv "РЕН"                # short case-insensitive channel-name fragment
ott tv "РЕН ТВ HD"          # longer channel-name query; same matching rules
ott tv play s               # channel named s, which is also a command
ott tv s                    # all channels from the active provider
ott tv s HD                 # filter by channel name
ott tv p                    # channel — current programme
ott tv p news               # list programme matches and play a random matching channel
ott tv p --list news        # filter programme titles without switching channels
ott tv vp wedding          # loop all VPortal videos with matching titles
ott tv vpr wedding         # shuffle all matching videos and loop that queue
ott tv vp --list wedding   # list the matches without changing playback
ott tv vpr -l wedding       # same read-only listing; do not start a shuffled queue
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
ott tv caps                 # version, runtime identity and supported controls
ott tv key enter            # one named OK/select input
ott tv input channel_up     # supported input, subject to local UI restrictions
ott tv pause                # pause supported archive/VOD playback
ott tv resume
ott tv seek 90.5            # absolute position in seconds; supported VOD only
ott tv restart             # reload the player page after acknowledgement
ott tv restart stream      # restart only the current stream
ott tv reload               # reload the player page after acknowledgement
ott tv restart app          # relaunch a supported native app
ott tv standby              # enter player standby
ott tv wake                 # leave player standby
ott tv exit                 # exit a supported app; no standby fallback
ott tv reboot device        # reboot the device OS only if advertised/supported
ott --json tv s             # machine-readable output
ott --timeout 60 tv p
```

Short and full command names share the same dispatch and output behavior:

Global flags before `PLAYER` also have standard short forms:
`-c` / `--config FILE`, `-t` / `--timeout SECONDS`, and `-j` / `--json`.

- `status` / `st`
- `s` / `channels`
- `p` / `programs` / `programmes`
- `prev` / `previous`; `next`
- `v` / `vol` / `volume`
- `vp` / `vportal`, and `vpr` / `vportal-random`
- `msg` / `message`
- `profile` / `prof`, and `profiles` / `profs`
- `provider` / `prov`, and `providers` / `provs`
- `capabilities` / `caps`, and `input` / `key`
- `exit` / `quit` / `close`

Aliases are case-insensitive exact tokens. They are not prefix completion:
`rest` does not restart a player. An invalid argument list for a recognized
command is an error; it does not fall through to channel playback. Use
`ott tv play volume` for a channel whose title is also a command or alias.
Channel text itself is preserved, including case and Unicode characters.

`p`, `programs`, `programmes`, `vp`, `vportal`, `vpr` and `vportal-random`
accept both `-l` and `--list` before the search text. These options only list
results and never switch channels or start a VPortal queue.

Profile setters accept `url` / `playlist`, `history` / `history-hours` /
`history_hours`, `vp` / `vportal`, and `n` / `name`. For example,
`ott tv prof 2 playlist https://example.com/list.m3u` and
`ott tv profile 2 url https://example.com/list.m3u` are equivalent.
These are CLI token aliases; JSON configuration keeps its canonical field names.

Volume queries print the numeric percentage, or only `{"volume": 35}` with
`--json`. Mutations additionally return `"dispatched": true` in JSON. Both
formats reject missing, nonnumeric or out-of-range readings; a failed volume
receipt never triggers an automatic retry. This is the platform's reported
volume, not proof of physical audio output.

Searches for channels, programmes, VPortal titles, providers and aliases are case-insensitive,
including Cyrillic text. A text channel query lists every channel whose name
contains the query, including exact names and longer names. For example,
`ott t1 zee` lists all names containing `zee`; `ott iphone "РЕН ТВ"` can include
both `РЕН ТВ` and `РЕН ТВ HD`. The list stays in provider catalogue order. With
multiple matches, each matching channel has an equal chance of being selected;
one match is selected directly. The CLI requests one channel switch after
validating the complete returned list. `ott iphone s РЕН` only lists matches.
Numeric queries such as `ott tv 12` retain a single playback request.

Text queries print `NUMBER: NAME` rows to stdout and the selected channel's
switch acknowledgement to stderr. For multiple matches, stderr also reports
`Randomly selected channel: NUMBER: NAME`; selection alone does not confirm
that the player accepted the switch. With `--json`, stdout contains the full
`channels` list and a `playback` object containing the player acknowledgement
or an `error` if switching fails. No matches means no playback request. An
invalid list or failed search never triggers a switch. The playback response
must confirm the selected number, ID and name before the CLI reports success.
A failed or uncertain switch exits with code 1 and is never retried automatically.
Keep the provider/catalogue unchanged between search and playback; a mismatching
acknowledgement reports an error but cannot undo a switch that already happened.
The numbers used by `s` and `play` refer to the provider's full catalogue,
regardless of the category currently open. `random` retains the existing
behaviour of using the current playback list.

`p` includes only programmes with a title and `start <= now < end`. Channels
without current EPG are omitted. Plain `p` only lists programmes. `p TEXT`
prints every matching programme in provider catalogue order and requests playback
of a randomly selected matching channel. Each matching channel has an equal
chance when there are multiple matches; a single match is selected directly.
`p --list TEXT` searches without playing.
An empty or whitespace-only search never switches channels. No matches means
no playback request. The switch confirmation goes to stderr, keeping stdout
in `channel — programme` format. Multiple matches also report
`Randomly selected channel: NUMBER: NAME` on stderr. With `--json`, the result includes a `playback`
object containing the player acknowledgement or an `error` if switching fails;
a playback failure exits with code 1 and never retries the switch automatically.

When `epg` is absent from the CLI configuration, the legacy compatibility path
reads programme data from the selected player; missing
EPG is requested through its normal guide service. Collection has a 25-second
budget. If it cannot finish, the CLI reports an incomplete programme search with
the number of channels checked and exits with code 3. This count measures search
coverage, not XMLTV download or server EPG loading progress. An empty partial
result cannot establish that no channels have matching programmes.
A filtered search selects randomly among the matches in that partial result;
unchecked channels are excluded from that selection and might contain additional
matches. A repeated
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
changing playback. `vpr TEXT` shuffles the complete selection once and plays
every matching video in that order before repeating the same queue. Each new
`vpr` command creates a new random permutation; a one-video selection remains
unchanged. `vpr --list TEXT` is also read-only and lists matches in catalogue
order, without preparing a playback queue. The filter must contain 1–1024 UTF-8 bytes after trimming
outer whitespace; empty filters are rejected. Queue numbers are one-based match
positions, independent of live channel numbers. To play a live channel named
`vp` or `vpr`, use `play vp` or `play vpr`.

These commands print `number: title` rows in their resulting queue order. JSON
output contains only `items` and `total`, plus `loop: true` and `dispatched: true`
for a playback request. A shuffled queue also requires and returns
`shuffled: true`. Stream
URLs and provider credentials are excluded. The playback confirmation goes to
stderr and means the player dispatched the queue, not that a video frame has
been rendered. No matches or a player that cannot use VPortal reports an error
for `vp` and `vpr`; the list command may return an empty list. Update the controller
and player to versions that support these actions. `vpr` needs the
`vportal_random` action on both; an older player must be updated/reloaded.
The CLI never substitutes ordered playback when shuffle is unsupported or
unconfirmed. A failed or uncertain request is never automatically submitted again.

The player collects all advertised search pages and episodes of matching series
before starting. Incomplete, timed-out or oversized searches start nothing; use a
narrower filter when a catalog limit is reached. Only natural video completion
advances the queue. Stop, manual playback or a provider change interrupts it. Each
visit resolves a fresh stream URL; an unavailable next item stops playback instead
of being silently skipped. Queues are not restored after restarting the player.

## Programme search configuration and archives

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
limited to 10,000 channels. The CLI searches the entire catalogue in batches of
at most 2048 channels, leaving space within the service's 512 KiB request limit
even for multibyte names. Every batch must use the same guide generation; missing
or invalid later batches reject the entire query without playback. The service must return a complete,
fresh result for the fixed public `epg-one` source; the service and CLI clocks
must be within one minute. Other `epg.source` values are rejected. Selecting
`epg-one` searches the public guide even if the player uses a private or custom
guide; those sources are neither used nor uploaded. An unsupported player,
stale result or service failure exits with an error. It never falls back to scanning
every channel's EPG on the player. Current-programme HTTP requests are limited
to ten seconds (or the smaller remaining budget) and 2 MiB of response data;
all current-programme batches together share the `--timeout` budget. Archive
history requests allow twenty seconds and 16 MiB per response.

With this optional EPG service configured, title launch follows this order:
`ott PLAYER TITLE` searches channel names first, then current programme titles,
then available archive programmes. `ott PLAYER p TITLE` starts at the programme
step. Both list the matches and choose one randomly when several match.
`p --list TITLE` searches without launching playback.

Archive lookup uses `/match` and `/programmes` on the same EPG service. Only
channels advertising archive support are eligible, within the smaller of their
retention and **144 hours**. Adjacent matching programmes form one result; an
intervening title, gap, overlap or retention boundary breaks the chain. Playback
starts at the earliest programme in the chain whose manifest and initial media
bytes are available. Separate chains on the same channel remain separate
choices. The first successful fragment is a startup check, not a guarantee for
every subsequent segment. The archive continues along the channel after launch.

EPG mappings, history and successful search results are cached for **two hours**;
successful media checks are reused for **seven days**, subject to the current
retention boundary. Files are private (0600) under `~/.cache/ottplay-control`
or `OTT_CACHE`. Read hits do not extend cache lifetime. Programme boundaries and
guide expiry invalidate search results earlier. `ott --refresh PLAYER p TITLE`
bypasses these caches. Current EPG is still checked first on every invocation;
the player's current catalogue is read again before dispatch, so a source change
cannot silently redirect an old search. Failed archive checks are not cached.

Archive search requires updated player and controller builds with
`resolve_archive` and `play_archive_catalog`. Those handlers belong to the shared
FOSS client used by web, Tauri desktop, Capacitor/iOS and packaged TV players.
Old installations retain live searches and report the needed update if an
archive fallback is required. Local validation of shared code does not install
new packages on offline TVs or phones.

## Provider settings

A `provider-config` file contains `provider` and `settings`. Select the intended
provider with `provider` first. Supplied fields are updated; other fields are
preserved. Credentials are not echoed in status, provider lists or
acknowledgements. Save these files with mode 600 and use HTTPS outside a trusted
local network.

Plex:

Select Plex, then configure the server address and its Plex access token in one
request. The token prompt hides input and does not add the token to shell history:

```sh
ott t1 provider plex
ott t1 plex setup 'http://nas.example:32400'
```

Both fields are required for the first setup. Once saved, change either field
while preserving the other credential and the current Plex playback preference:

```sh
ott t1 plex server 'https://plex.example'
ott t1 plex token
```

For scripts, read the token from a private UTF-8 file (one token, with an optional
trailing newline), or supply a private provider-config JSON file:

```sh
ott t1 plex setup 'http://nas.example:32400' --token-file ~/private/plex-token
ott t1 plex token-file ~/private/plex-token
ott t1 provider-config ~/private/plex.json
```

```json
{"provider":"plex","settings":{"server":"http://nas.example:32400","token":"YOUR_PLEX_TOKEN"}}
```

Direct arguments are also supported: `plex setup URL TOKEN` and `plex token TOKEN`.
Those tokens can appear in shell history and process arguments; use the hidden
prompt or file forms when that matters. A terminal without hidden input must use
a file or an explicit argument; the CLI never falls back to echoing the prompt.

The server address must be HTTP(S), with a valid port and no embedded credentials,
query, fragment or `..` path segment. The token must be nonempty, at most 1024
UTF-16 code units, and contain no whitespace or control characters. Outer pasted
whitespace and a token file's trailing newline are removed before sending.

These commands require both a command server and a player with remote Plex
settings support. Plex must be selected and its settings unlocked. Saving uses
the player's existing Plex driver, reloads the library and clears saved account
connection candidates when the address or token changes. `Plex settings saved`
confirms that the settings were stored; it does not prove a successful server
connection or playback. Tokens and addresses are omitted from acknowledgements,
including `--json`. A rejected or uncertain operation is not retried automatically.

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

## M3U profiles

M3U has 15 numbered profiles. Select the M3U provider first; these commands
require it to be active and respect the player's settings lock:

```sh
ott tv provider m3u
ott tv profiles
ott tv profile 3
ott tv profile 3 name "Living room"
ott tv profile 3 url 'https://provider.example/list.m3u'
ott tv profile 3 history 168
ott tv profile 3 vportal 'portal::[key:YOUR_KEY]https://provider.example/api/v1/'
ott tv profile 3 vportal ''
ott tv profile-config 3 /private/profile.json
```

`profiles` lists all 15 slots in order, marking the active slot with `*`. It shows
the name, archive depth in hours and whether playlist/VPortal links are configured.
URLs, provider keys and credentials are omitted from both normal and JSON output.
An existing history value that cannot be validated appears as `unknown` (`null`
in JSON). `profile N` selects that slot; it reports a switch request, not completed
provider loading or visible playback.

Use `profile-config N FILE.json` to update several settings together. Its contents
are the settings object itself, without a `provider` or `settings` wrapper:

```json
{"name":"Cinema","playlist":"https://provider.example/list.m3u","history_hours":168,"vportal":"portal::[key:YOUR_KEY]https://provider.example/api/v1/"}
```

VPortal requires the complete cabinet link in
`portal::[key:YOUR_KEY]https://provider.example/api/v1/` format, including its key
and endpoint. A bare HTTP(S) URL is not a VPortal cabinet link.

Only supplied fields change. Editing an inactive profile leaves the active
playlist running. Renaming a profile or changing only its VPortal link does not
reload channels; a VPortal change replaces that profile's media source. Changing
the active playlist or its archive depth reloads channels once.
Profile numbers are integers 1–15; `history_hours`
is an integer 0–8760. Archive depth tells the player what history to offer; the
provider must still supply that history. Names are limited to 256 UTF-8 bytes,
playlist and VPortal strings to 8192 bytes each, with no C0 or DEL control
characters. Empty strings clear the corresponding name or link. Clearing the
active playlist also reloads that slot, so its previous stream does not remain
active. The player validates links and applies changes through its normal driver.

The JSON file and complete request must each fit within 16 KiB. Empty settings,
duplicate fields, unknown fields and invalid values are rejected before sending.
Save files containing private links with mode 600. Updating settings is atomic:
all supplied fields must pass validation. The acknowledgement must match the
requested profile and nonsecret settings before the CLI reports them saved;
URL changes are confirmed only through configured/empty flags. Unsupported,
rejected or uncertain requests are never repeated automatically.

## Named setups

A named setup groups provider settings for one environment. `ott PLAYER load NAME`
loads it onto an already connected player. It is separate from the numbered M3U
slots selected by `ott PLAYER profile N`. Names are case-insensitive, contain
1–32 ASCII letters, digits, underscores or hyphens, and start with a letter or
digit. Use `ott PLAYER play load` to select a channel literally named `load`.

Add `presets` to the existing private `~/.config/ottplay-control/cli.json`, keeping
its controller settings and player aliases. This example shows the object to add;
replace the credential placeholders before use:

```json
"presets": {
  "local": {
    "m3u": [
      {
        "number": 1,
        "name": "Local 1",
        "playlist": "http://127.0.0.1:8090",
        "history_hours": 144,
        "vportal": "portal::[key:YOUR_KEY]https://provider.example/api/v1/"
      },
      {
        "number": 2,
        "name": "Local 2",
        "playlist": "http://127.0.0.1:8090",
        "history_hours": 144,
        "vportal": "portal::[key:YOUR_KEY]https://provider.example/api/v1/"
      }
    ],
    "plex": {"server": "http://nas.example:32400", "token": "YOUR_PLEX_TOKEN"},
    "active_profile": 1
  },
  "home": {
    "m3u": [
      {
        "number": 1,
        "name": "Home 1",
        "playlist": "https://m.2560801.xyz",
        "history_hours": 144,
        "vportal": "portal::[key:YOUR_KEY]https://provider.example/api/v1/"
      },
      {
        "number": 2,
        "name": "Home 2",
        "playlist": "https://m.2560801.xyz",
        "history_hours": 144,
        "vportal": "portal::[key:YOUR_KEY]https://provider.example/api/v1/"
      }
    ],
    "plex": {"server": "http://nas.example:32400", "token": "YOUR_PLEX_TOKEN"},
    "active_profile": 1
  }
}
```

`local` uses the Mac's playlist service on loopback. `home` uses its external
address for the TV and phone. Both can use the same VPortal link and Plex
credentials. The Plex address must be reachable from each target device.
The second M3U slot starts with the same settings so it can be customized
independently. Editing a preset changes only this file; run `load` to apply it.
Set mode 600 on the file and keep it out of source control and public uploads.

Each setup may also contain an optional `stalker` list. Its entries address
Stalker slots independently of the M3U slots:

```json
"stalker": [
  {"number": 1, "name": "My portal", "server": "https://portal.example/c/", "mac": "02:00:00:00:00:01"}
]
```

Provide all four fields, with unique slot numbers from 1 to 15. Loading saves
these slots before Plex and M3U, verifies each acknowledgement, and preserves
the selected Stalker slot. It still finishes on the setup's `active_profile`
in M3U. The player must support numbered Stalker settings; an older player
rejects the request and loading stops without substituting the active slot.
The output does not include portal URLs or MACs.

```sh
ott presets
ott t1 load local
ott o1 load local
ott l load home
ott iphone load home
ott --json t1 load LOCAL
```

To configure all four instances, run each load sequentially and stop on failure:

```sh
for player in t1 t2 t3 t4; do ott "$player" load local || break; done
for player in o1 o2 o3 o4; do ott "$player" load local || break; done
```

The selected setup is fully validated before any remote request. Each M3U entry
must include `number`, `name`, `playlist`, `history_hours` and `vportal`; numbers
must be unique integers from 1 to 15. `active_profile` must refer to an included
slot. Playlist URLs must be nonempty HTTP(S) addresses. Use IPv4 addresses in
four decimal octets (for example, `127.0.0.1`), not shortened or hexadecimal
forms. An empty VPortal string
disables VPortal for that slot. Plex requires both `server` and `token`.
The existing provider length and request-size limits apply.

Loading selects Plex, waits for its settings handler, saves its credentials,
then selects M3U and waits for the profile handler. It saves inactive slots
first and selects `active_profile`. If the previously active slot also needs
updating, it saves that slot after switching, avoiding an unnecessary playlist
reload. Finally, it checks the resulting M3U metadata. Unlisted M3U slots remain
unchanged. Each change uses the existing
request/acknowledgement API; the next change is sent only after the preceding
one is confirmed. A provider's empty library or unavailable playlist does not
prevent saving settings: channel readiness is not a configuration barrier.

The whole setup is not a transaction. If a step fails, earlier confirmed saves
remain in place, and the CLI reports the failed step and completed steps. It
does not roll back or continue after an uncertain response. Ctrl+C also reports
confirmed steps and exits with status 130. It retries only
read-only readiness queries and explicit prerequisite rejections that occur
before any settings write; it never replays an unacknowledged write. Check the
player before repeating a failed load. Avoid changing providers/settings from
another remote or the player's UI while a load is in progress.

`--timeout` bounds each request/readiness phase, so a complete load can take
longer than a single timeout. Setup output, including `--json`, contains only
the setup name, step names and selected profile; it omits URLs, VPortal keys and
Plex tokens. Success confirms storage and selection, not successful media
playback. Settings locks and platform restrictions still apply. Use a player
with remote M3U profile and Plex settings support; the existing controller needs
no deployment change.

## Restarting playback or the player

`ott tv restart` requests a full player reload. `ott tv restart player` and
`ott tv restart p` are explicit equivalents. `ott tv restart stream` (or
`restart s`) restarts only the current stream through the playback backend.
Bare `restart` previously defaulted to the stream; scripts needing that behavior
must use the explicit `restart stream` command. Neither operation selects
another provider or profile. Backends
that cannot perform the requested operation return an explicit unsupported result.

A stream result means the restart was dispatched, not that video has resumed.
A player reload result means the request was accepted: the player reloads only
after its response reaches the controller successfully. The CLI prints
`Player reload accepted; waiting for the acknowledgement to reach the player.`
It does not claim the reload completed, poll the player's state or resend the
request. An expired request or changed/disconnected command-server configuration
can discard the pending reload. Check the player before manually repeating an
uncertain request. A full player reload does not restore an in-memory VPortal
repeat queue.

`restart s` and `restart p` are exact aliases for `restart stream` and
`restart player`. `reload` or `reload player` also reloads the page through the
new acknowledged lifecycle API. `restart app`, `restart application` and
`restart a` request a native app relaunch. `reboot` or `reboot device` requests
an operating-system reboot, never a page reload. `exit`, `quit` and `close`
request app exit, never standby. `standby` and `wake` are separate player-state
operations; `wake` cannot contact an offline or powered-off player.

Lifecycle availability is platform-specific. The player must advertise and
implement the operation; an unavailable native exit, relaunch or OS reboot
returns unsupported. None of these commands falls back to another operation.
Existing `restart stream/player` retains its original request/ACK shape;
new lifecycle requests return `accepted: true`, `dispatched: false` and
`effect: "lifecycle-after-ack"`. The effect runs only after the player receives
the controller ACK, with current local restrictions checked again.

## Capabilities, input and playback control

`ott tv`, `ott tv status` and `ott tv st` show status followed by commands for
the controls that the device currently advertises, including `ott tv restart`
when page reload is available. They make two read-only requests within one
`--timeout` budget. `--json` retains the status fields and adds a validated
`capabilities` object. If that secondary request fails, is unsupported or belongs
to a different player runtime, status still succeeds with `capabilities: null`
and a `capabilities_error` explanation; text output shows the same limitation.
Unavailable controls are never inferred from another platform. Volume-only
queries (`v`, `vol`, `volume`) remain a single request with compact output.

`ott tv caps` / `capabilities` reports the application's version, platform and a
public runtime identifier for the current page, plus supported lifecycle,
input and playback operations. It excludes provider credentials, channel IDs
and stream URLs. Current `status` also includes this public player identity;
older versions may omit it. A changed runtime after a reload provides stronger
evidence than a readiness response alone. These capabilities describe
the current state: an operation can become unavailable before it executes.

`key KEY` / `input KEY` accepts one named input from the capabilities response.
Available names include `up`, `down`, `left`, `right`, `ok`, `back`, `menu`,
`settings`, `channels`, `guide`, `info`, `channel_up`, `channel_down`,
`volume_up`, `volume_down`, `mute`, `play_pause`, `audio`, `aspect`, `zoom`,
`pip` and `fullscreen`. Exact parameter aliases are:

- `u/d/l/r` → `up/down/left/right`; `enter` or `select` → `ok`; `return` → `back`.
- `setup` → `settings`; `channel-list` → `channels`; `epg` → `guide`; `i` → `info`.
- `ch+` or `channel-up` → `channel_up`; `ch-` or `channel-down` → `channel_down`.
- `vol+` or `volume-up` → `volume_up`; `vol-` or `volume-down` → `volume_down`.
- `pp` or `play-pause` → `play_pause`; `fs` → `fullscreen`.

The CLI does not accept numeric keycodes or arbitrary scripts. An input receipt
contains the exact key, `accepted: true`, `dispatched: false` and
`effect: "input-after-ack"`; it does not prove that the visible UI changed.
Kiosk and parental restrictions still apply.

### Previous and next channel

```sh
ott l prev                 # previous channel on the player registered as l
ott l previous             # same command, full spelling
ott l next                 # next channel
ott --json l prev          # operation, dispatched and selected channel metadata
```

These commands switch one position in the **currently playing category or
favourites**, with wrap from first to last and last to first. `prev` means the
preceding entry in that list, not the previously watched channel. The player
uses its current playback selection when handling the request; a different
category being browsed in the open channel list does not change this order.
An admitted switch closes that list. A single-channel category selects that
same channel. Each command accepts no arguments.

The player advertises `previous_channel` and `next_channel` in `caps.playback`;
bare `ott l` shows the corresponding `prev` and `next` commands. Both the
controller and the player must support these operations. An unloaded or stale
channel selection, protected UI/PIN, standby, kiosk or settings lock can reject
the request. Unlock locally and retry only after checking the result of the
first request. These operations never fall back to UI keypresses: `key ch+`
and `key ch-` retain the normal remote-key meaning, which can paginate an open
list instead of changing playback.

JSON contains `operation`, `dispatched: true` and `channel: {id, number, name}`;
the number is from `s`, while adjacency follows the current category. A receipt
confirms dispatch, not rendered video. The CLI submits only one mutation and
never resubmits it after a lost or invalid receipt. Use `ott l play prev` or
`ott l play next` to search a channel whose name is a reserved command.

### Pause, resume and seek

`pause` and `resume` use the typed playback API for an owned, active archive/VOD
decoder. `seek SECONDS` is available only for VOD, because archive seeking uses
its separate programme timeline. Live streams do not advertise these operations.
Seek is an absolute, finite position from 0 to 9007199254740991 seconds; `+5`
is not a relative seek. The CLI validates the returned operation and, for seek, the
requested position before reporting dispatch. A valid receipt does not prove
that the decoder reached that position or recovered. An unsupported player or
changed playback state fails explicitly, without a keypress fallback or retry.

## Acknowledgements and limitations

The CLI requires a server and player supporting `request_protocol=1`. Older
versions still accept legacy commands but do not answer CLI requests.
`ok` means the player handler returned a result; `dispatched` means it invoked
the normal player action. Neither confirms visible playback, PIN acceptance or
the TV's physical volume. `v` shows the value reported by the platform API.
New input and lifecycle commands wait for acknowledgement before invoking their
effect; an accepted response still does not prove that the effect completed.

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

## Scoped remote diagnostics

`ott diagnostics --help` opens the separate diagnostics CLI without reading the legacy administrator configuration. Install `diagnostics.py` beside `ott.py`. See [Diagnostics CLI and MCP](diagnostics-cli.md) for operator credentials, runtime selection and temporary sessions.

## Kiosk mode

Requires updated player **and** controller builds. The player must be connected
to the controller with its device credentials and have loaded live channels.

```sh
ott tv kiosk                # current policy and playback health
ott tv kiosk on             # lock the next channel selected in the player's UI
ott tv kiosk on 12          # immediately select and lock channel 12 from `s`
ott tv kiosk on "Новости"   # lock the first name containing Новости, ignoring case
ott tv kiosk set 7          # remotely replace the locked channel
ott tv kiosk off            # disable kiosk mode
ott --json tv kiosk status
```

`on` without a channel arms the player; it does not lock the currently playing
channel. Repeating it while already locked preserves that lock. `set` requires
kiosk to be enabled and changes the channel within the current source. Both `on`
and `set` treat text as a literal case-insensitive substring and select the first
matching channel in `ott tv s` order. A later exact name has no priority, and
multiple matches are not an error. For example, with `Новости HD` listed before
`Новости`, the query `Новости` locks `Новости HD`. This is not regex/glob syntax.
Numbers still select a one-based catalogue row. No match, or an unavailable or
protected first match, rejects without changing the lock or skipping to another
match. Retries keep the selected channel ID, even if the list is later reordered.
It never uses random channel/programme/archive fallback. Disable kiosk before changing
provider, profile or provider settings, then re-enable it on the desired source.
Parental access must already allow the requested channel; kiosk does not bypass it.

After selection the player blocks local navigation, channel switching, archives
and VOD. Ordinary remote playback/provider/profile mutations and exit commands
are rejected as well. Read queries, volume/mute, notifications and explicit remote
restarts remain available. Only the `kiosk` request changes this policy.

The client retries the same channel every ten seconds without playback progress,
resolving its stream URL again. Healthy playback continues uninterrupted. The lock
uses channel and source identities, so list reordering or a missing channel never
selects an unrelated stream. Policy persists across page reloads and controller
outages, and is excluded from portable settings. The running page owns recovery;
this does not relaunch a crashed process or wake a suspended device.

`status` reports `off`, `waiting` or `locked`, channel/provider metadata,
`retry_seconds: 10`, retry count and observed health. A successful mutation means
policy was stored and any requested launch attempted, not that visible playback
was verified. Delivery and uncertain-result handling match the other CLI requests;
do not repeat an uncertain mutation blindly. Inspect `kiosk status` first.

Direct API example, submitted to the existing authenticated `/api/requests`
route for the selected device:

```json
{"action":"kiosk","params":{"mode":"on","query":"12"}}
```

`status` and `off` accept only `mode`; `on` optionally accepts `query`, and `set`
requires it. Queries contain 1–1024 UTF-8 bytes without control characters.

## Troubleshooting

Start with read-only checks, replacing `tv` with your registered alias:

```sh
command -v ott
python3 --version
ott --help
ott devices
ott tv
ott tv caps
```

Do not include `ott pair` output, private configuration, provider URLs with
credentials or operator tokens in a shared report. Useful evidence is the exact
command with secrets removed, exit code, fixed error message, time, player
version/platform and whether the problem affects one player or every player.
For structured capture use the [diagnostics workflow](diagnostics-cli.md), which
requires separate permissions and local consent.

### `ott: command not found`, wrong installation or missing Python module

Add `~/.local/bin` to the current shell's PATH and its startup file, then reopen
the terminal. Inspect `command -v ott` (or `type -a ott` in bash/zsh) for an older
installation taking precedence. Check the symlink target still exists. Use
`python3 /absolute/path/to/cli/ott.py --help` to separate PATH/executable problems
from Python problems. Keep all four CLI files from the same source version in
the resolved target directory. A copied `ott.py` alone is not a complete install.
On Windows use the `py -3 ...` invocation from the installation section.

### Configuration cannot be read or the wrong controller is selected

Check the path in `--config`, then `OTT_CONFIG`, then the default file. Global
flags must come before the player name. Confirm the JSON is valid and has no
duplicate fields, and that `server_config` points to an existing private file
with the administrator token and device registrations for this exact server.
Use an absolute path when a service or terminal has a different working directory.
The CLI refuses credential-bearing URLs, query/fragment suffixes and redirects;
set `server` to the final HTTP(S) base address, including the intended proxy prefix.
Keep credentials in the private configuration, not in the address.

### `Unknown player`, wrong alias or registration missing

`ott devices` reads the live server, while alias resolution also uses the local
configuration. Synchronize `server_config` after registration changes made
elsewhere. Use `ott alias NAME UUID` for an existing device or `ott add NAME UUID`
for a new one. Apply the edited server configuration and restart as needed.
An alias is not a hostname and does not require SSH. Check the exact device ID
on the intended player; give every active installation its own device token.

### The server is healthy, but the player is offline or commands time out

Check **Connected** in the player's command-server settings and keep the app
awake. `last_seen=never` means no contact has been recorded since that server
started; a historical timestamp does not prove the player is connected now.
A successful `/healthz` or `/readyz` probe confirms the server process, not
player polling or playback. Verify that the player can reach `player_server`,
not just that the CLI can reach `server`.

For a browser, allow the exact origin (scheme, hostname and port) and use HTTPS
from an HTTPS page such as here.now. Check trusted certificates and proxy paths.
Packaged TV pages with `Origin: null` require the explicit device-route opt-in;
it does not authorize administrator routes. Native apps retain their transport
restrictions. If one token is shared by two players, they compete for commands;
register them separately. Update/reload old clients that lack request-protocol-1
replies. See [server troubleshooting](deployment.md#troubleshooting).

### HTTP errors

- **401/403:** check which credential is being used. The player needs its device
  token, ordinary CLI requests need `admin_token`, and diagnostics need their own
  scoped operator credential. Verify the configuration was applied and restarted,
  and check origin or operator-scope restrictions for the affected route.
- **404:** check the final server base URL/proxy prefix and the server version.
  Discovery/pairing endpoints require optional discovery configuration. A missing
  receipt after a restart or expiry does not prove the command never ran.
- **429 / queue full:** stop flooding the queue, inspect connectivity and wait
  before another read. An offline player cannot drain requests. After an accepted
  mutation, a failed receipt lookup is an uncertain result, not permission to send
  the mutation again.
- **502/503/504 or transport timeout:** inspect the reverse proxy and server
  readiness. The ordinary CLI retries eligible reads of the same request ID
  within its budget; it never automatically submits the command twice.

### A change timed out or returned an uncertain result

Inspect `ott tv`, the relevant read-only query (`v`, `profiles`, `kiosk status`)
and the actual player before repeating it. A timeout does not cancel an accepted
command, and it may execute before server expiry. A reload, native restart or
exit acknowledgement confirms acceptance rather than completion. After a reload,
run `caps` again to observe the new page-runtime identity. Increasing `--timeout`
can allow more response time but cannot fix an unsupported operation or prove
that a previous mutation failed. Diagnostics use a separate epoch/idempotency
workflow; follow [unknown-outcome recovery](diagnostics-cli.md#timeouts-and-uncertain-outcomes).

### Status succeeds, but available controls are missing

The status and capabilities requests share one timeout. An old client, a reload
between replies, unavailable capabilities or an exhausted budget may leave valid
status with `capabilities: null` and `capabilities_error`. Check the player version,
then rerun a read-only status/capabilities query after the connection settles.
Do not assume a control is supported because another platform exposes it.

### A control, provider setting or channel change is rejected

Read `caps` for currently supported lifecycle/input/playback operations. Ordinary
browsers cannot perform native app exit/relaunch or an OS reboot; OS reboot is
currently unsupported by every shipped player platform. `wake` only addresses
player standby while connected, not a powered-off machine. Live streams do not
support the archive/VOD `pause`/`resume` controls or VOD seeking.

Check `kiosk status`, parental/settings locks, the active provider and whether
its catalogue has finished loading. Use local interaction for PINs and consent.
Select M3U before managing its profiles; select the matching provider before
applying `provider-config` or Plex settings. Disabling kiosk or changing a lock
is a deliberate policy change, not an automatic recovery step. Successful settings
storage does not prove provider login, media availability or visible playback.

### A search is empty, incomplete, or plays an unexpected match

Use `ott tv s "РЕН"` or `ott tv p --list "Кино"` to inspect results without
switching. Channel queries use literal case-insensitive substrings: a full title
can still match a longer title. Ordinary matching selects randomly among multiple
matches; kiosk matching selects the first. Use the displayed catalogue number
for a particular channel, keeping the provider/catalogue unchanged. Use
`play TITLE` when the title is reserved by a command.

For programme searches check the configured EPG service, guide coverage and
clock accuracy. Exit `3` on the legacy path reports incomplete coverage; repeat
a read-only `p --list` query after the guide cache warms. On the central-service
path, `ott --refresh tv p --list "Кино"` bypasses search/archive-probe caches.
Archive searches additionally require supported retention, matching server/player
actions and accessible media. A failed availability check is not fixed by blindly
replaying the same mutation. Narrow overly broad VPortal searches rather than
assuming a partial catalogue represents every match.

### Kubernetes registration was interrupted

Preserve `cli.json.pending-add.json` and repeat the exact `ott add NAME UUID`
operation after resolving connectivity or rollout problems. It reconciles the
recorded before/after state and preserves the generated token. If another
operator changed the configuration, reconcile those changes before retrying;
deleting the journal or adding a second credential can lose the recovery path.
Check context, namespace, Secret, Deployment and Pod events. Connect the player
only after registration finishes successfully.
