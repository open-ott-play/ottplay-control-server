# Control players from the terminal

`cli/ott.py` and its sibling `cli/programme_search.py` use Python 3 without additional packages. Keep both files together when installing a copy. Commands take a short
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

Title launch now follows the same order on every registered player:
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
ott tv news                 # list name matches and play a random matching channel
ott tv play s               # channel named s, which is also a command
ott tv s                    # all channels from the active provider
ott tv s HD                 # filter by channel name
ott tv p                    # channel — current programme
ott tv p news               # list programme matches and play a random matching channel
ott tv p --list news        # filter programme titles without switching channels
ott tv vp wedding          # loop all VPortal videos with matching titles
ott tv vpr wedding         # shuffle all matching videos and loop that queue
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

`ott tv restart` and `ott tv restart stream` request a restart of the current
stream through the playback backend. `ott tv restart player` requests a full
player reload. Neither command selects another provider or profile. Backends
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
