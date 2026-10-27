# Control players from the terminal

The CLI uses CPython 3.12 or newer and its standard library; no `pip install` is needed.
Keep all files from the same `cli/` revision together: `ott.py`, `programme_search.py`, `playlist_search.py`,
`diagnostics.py`, `diagnostics_mcp.py`, `workbench.py`, `report_verify.py`, `preset_session.py`,
`player_settings.py`, `app_update.py`, `device_registry.py`, `performance.py` and `fleet.py`. Python 3.12 is the version used by CI. Commands take a short
player name followed by an action. Assign each player its own device ID
(preferably the Device UUID shown in its settings) and device token.
Do not share one token between active players: they would compete for the same
command queue.

For opt-in performance recording, bounded export and guarded notification A–B–A
trials, see [Temporary performance capture](performance-capture.md). `ott PLAYER
perf --help` works without loading credentials; recording is off by default.
The reviewed source revision pinned below includes version 3 support:
`ott d1 perf start --hls-policy-details` selects HLS policy evidence when
supported; `--hls-details` continues to select version 2. See the
[buffer-policy runbook](performance-capture.md#hls-buffer-policy-and-retry-evidence)
for capture, export, verification and the limits of retry/cause observations.

For a finite read-only observation of one player or an explicit group, use
`ott d1 watch` or `ott t1,t2,t3,t4 watch --duration 60 --interval 5`. The
[monitoring runbook](monitoring-runbook.md) explains controller failures,
standby, decoder progress, saved evidence and deliberate repair.

For dynamic registration, removal, credential rotation and recovery, see the
[device registry CLI workflow](device-registry.md#cli-workflow). `ott remove NAME`
revokes access before removing its local alias; `ott unalias NAME` only removes
the local alias. Run `ott registry --help` for all lifecycle commands.

Use this guide for the terminal client. The [deployment guide](deployment.md)
covers installing, starting and stopping the separate Go command server;
the [player connection guide](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/remote-command-server.md)
covers the settings on the TV, browser, Tauri or Capacitor installation.

- [Install and configure the CLI](#installation-and-connection)
- [Register and connect a player](#register-and-connect-a-player)
- [Disconnect, deregister or revoke a player](#disconnect-deregister-or-revoke-a-player)
- [Help, options and output](#help-options-and-output)
- [Run a command on several players](#run-a-command-on-several-players)
- [Player commands and aliases](#commands), [programme search and archives](#programme-search-configuration-and-archives)
- [Switch provider and start playback](#switch-provider-and-start-playback)
- [Switch providers on several players](#switch-providers-on-several-players)
- [Local folders and shuffled playback](#local-folders-and-shuffled-playback)
- [Resolve a playlist for a local launcher](#local-playlist-resolver)
- [Check searches on every registered player](#check-every-registered-player)
- [Provider settings](#provider-settings), [M3U profiles](#m3u-profiles), [Local videos profiles](#local-videos-profiles) and [named setups](#named-setups)
- [Favorites across profiles](#favorites-across-profiles)
- [Plex libraries by name](#plex-libraries-by-name) and [explicit Plex queues](#ordered-plex-queues)
- [Restarts](#restarting-playback-or-the-player), [input and playback controls](#capabilities-input-and-playback-control), [screenshots](#remote-screenshots), [kiosk mode](#kiosk-mode)
- [Scoped diagnostics and MCP](diagnostics-cli.md)
- [Home Assistant connection and event delivery status](#home-assistant-event-status), [per-player notification preference](#per-player-notification-preference)
- [Read-only doctor, inspect, debug, bundles and test scenarios](workbench.md)
- [Monitor one player or a group and diagnose failures](monitoring-runbook.md)
- [Verify saved reports offline](workbench.md#verify-saved-evidence-offline)
- [Troubleshooting](#troubleshooting)

## Home Assistant event status

The optional [Home Assistant bridge](home-assistant.md) turns filtered HA events
into banners on active players. Its status is controller-wide, so no player
alias precedes `ha`:

```sh
ott ha status
ott ha status -j
```

These read-only commands require a CLI and controller version containing the
bridge. They use the administrator credential or a command-operator credential
with the `status` action, show connection state and player activity, and never return the HA
token or event text. Operators see only their authorized devices; global
bridge and `delivery` counters are administrator-only. The JSON form projects known public
fields; use the API reference for the complete endpoint response.

`queued` means accepted into a temporary device queue; `polled` means fetched by
a player. Neither proves a visible banner. Standby/hidden players are excluded,
and old events are not replayed on wake. See the [setup examples, rules and
troubleshooting](home-assistant.md), or the [Russian guide](home-assistant.ru.md).

## Per-player notification preference

One preference controls event banners, camera images and future notification
clips together. It is saved in the player installation and survives reloads and
provider/profile changes; it does not stop or change IPTV/Plex playback.

```sh
ott t1 notifications                 # read the saved preference
ott t1 notifications off             # disable banners and notification media
ott t1 notifications on              # enable future events without replay
ott t1,t2,t3,t4 notifications off     # save independently on each player
ott -j t1,t2 notifications status     # individual success/error results
ott t1 notif off                     # short command alias
```

Both player and controller must support this operation. The player must be
online; an acknowledged result confirms persistent storage in that installation.
After a timeout, read the preference before deciding whether to repeat a change.
The available-controls output from `ott t1` includes these commands when the
player advertises them. Scoped operators need `notifications.get` for reads or
`notifications.set` for changes, plus `capabilities` for CLI discovery.

`ott ha status` shows the last reported `notifications_enabled` value, which
is not a replacement for a direct durable read. Group operations report partial
failures and return a nonzero exit status if any player failed. See the
[single-player disable/enable and verification recipe](notification-preferences.md#disable-or-enable-one-player-and-verify-delivery)
and [persistence, compatibility and recovery details](notification-preferences.md).

## Installation and connection

### Check the Python and TLS runtime

The supported HTTPS profile uses the standard CPython/OpenSSL defaults with
OpenSSL security level 2 or higher, TLS 1.2 or higher, certificate verification
and hostname checking. The CLI does not lower these defaults. Older Python
versions, alternative Python implementations and vendor-modified TLS defaults
are outside this verified profile. Keep the Python/OpenSSL installation updated;
do not lower its security level to connect to a server with an undersized key.

Run this with the same interpreter that will run the CLI. On Windows, replace
`python3` with `py -3`. It prints the runtime profile and exits unsuccessfully if
the required settings are absent:

```sh
python3 -c 'import platform, ssl, sys
context = ssl.create_default_context()
print(platform.python_implementation(), platform.python_version())
print(ssl.OPENSSL_VERSION)
print("security_level=", context.security_level,
      "minimum_tls=", context.minimum_version.name,
      "verify_mode=", context.verify_mode.name,
      "check_hostname=", context.check_hostname)
supported = (platform.python_implementation() == "CPython"
             and sys.version_info >= (3, 12)
             and context.security_level >= 2
             and context.minimum_version >= ssl.TLSVersion.TLSv1_2
             and context.verify_mode == ssl.CERT_REQUIRED
             and context.check_hostname)
raise SystemExit(0 if supported else 1)'
```

This checks the local defaults, not a remote server's identity. Each HTTPS
connection still verifies its certificate and hostname. The
[security design](security-design.md#python-cli-https-profile) records the tested
runtime and key-strength checks. Plain HTTP does not provide these protections.

### Choose the CLI source revision

Install CPython 3.12 or newer and Git first. The native server release archives
contain the Go server, **not** the Python CLI. The examples below install reviewed
source commit
[`8c6a89989b6bedd703db9614779deef0b4e935a7`](https://github.com/open-ott-play/ottplay-control-server/commit/8c6a89989b6bedd703db9614779deef0b4e935a7).
This is an exact source pin, not a claim about the latest published release.
It contains the complete CLI module set, including finite group `watch`,
performance-export verification, negotiated debug bundles and the local `resolve` command,
[workbench](workbench.md#quick-start), offline `report verify`, Plex library commands and the verified-release Android
OTA workflow (`app_update.py`), including stable releases promoted from RC bytes, Local videos profile save/load/import/export, and
`standby` / `go` controls. Kiosk confirmation accepts screen-suspended playback
while retaining policy and strict-state checks. It also includes media categories, stable-ID playback
and bounded catalogue pages (`s --limit` / `--offset`), plus explicit player lists
for player commands such as `ott t1,t2,t3,t4 R`, settings, lifecycle, presets
and diagnostic exports. Provider switching also accepts an optional channel
number, `R` / `random` or title selector. It includes HLS policy capture v3,
`local sources`, `local play` / `local shuffle` by connected folder name or native
path, and [Local source bindings in named presets](#named-setups). The public
name is `local`; `offline` remains a compatibility alias. Controller support for
`local_media` is included in this same source revision. Update the controller
and player too; confirm the requested operation with `ott PLAYER caps`. Native
paths additionally require the player to advertise `absolute_paths: true`.
This revision also supports literal colons in Local folder names and avoids
repeated per-folder reads when loading an already connected Local preset.

The full source hash is verified before and after checkout. Record
`git rev-parse HEAD` with diagnostic results. Older CLI revisions, including
`v0.1.6-beta.13`, lack the new `local` source commands and preset bindings; other
older revisions may also lack `go`, Local videos profile commands or
high-level Android release updates. A server's `version` output, the repository's `VERSION` file and
a player's reported version identify different components; none substitutes for
checking this CLI checkout's commit. Follow the
[deployment guide](deployment.md) for the controller and the
[target installation guidance](#update-the-installation-used-by-the-target-player)
for players. Installing this CLI does not update either component or provision
an Android agent.

### Install on macOS or Linux

Use a new revision-specific directory. This block refuses an existing path,
checks out the exact commit and checks help without reading configuration or
contacting a controller:

```sh
(
  set -eu
  ott_ref=8c6a89989b6bedd703db9614779deef0b4e935a7
  ott_source="$HOME/.local/share/ottplay-control-server-local-8c6a89989b6b"
  python3 --version
  mkdir -p "$HOME/.local/share"
  if [ -e "$ott_source" ] || [ -L "$ott_source" ]; then
    printf '%s\n' "Already exists: $ott_source; inspect it or choose a new directory." >&2
    exit 1
  fi
  git clone --no-checkout https://github.com/open-ott-play/ottplay-control-server.git "$ott_source"
  ott_revision="$(git -C "$ott_source" rev-parse "$ott_ref^{commit}")"
  test "$ott_revision" = "$ott_ref"
  git -C "$ott_source" checkout --detach "$ott_revision"
  test "$(git -C "$ott_source" rev-parse HEAD)" = "$ott_revision"
  python3 "$ott_source/cli/ott.py" --help
  python3 "$ott_source/cli/ott.py" diagnostics --help
  python3 "$ott_source/cli/ott.py" report verify --help
  python3 "$ott_source/cli/ott.py" t1 perf start --help
)
```

Run this installation directly, including when an older `ott` is already on PATH:

```sh
ott_script="$HOME/.local/share/ottplay-control-server-local-8c6a89989b6b/cli/ott.py"
python3 "$ott_script" --help
# Once your existing configuration is ready:
python3 "$ott_script" a1 doctor --json
```

For a first installation, this optional block adds `ott` only if that path is
unused; it never replaces an existing launcher, directory or dangling symlink:

```sh
mkdir -p "$HOME/.local/bin"
if [ -e "$HOME/.local/bin/ott" ] || [ -L "$HOME/.local/bin/ott" ]; then
  printf '%s\n' 'Existing ott preserved; use the direct Python invocation.'
else
  ln -s "$HOME/.local/share/ottplay-control-server-local-8c6a89989b6b/cli/ott.py" "$HOME/.local/bin/ott"
fi
export PATH="$HOME/.local/bin:$PATH"
command -v ott
```

Keep the checkout after making the symlink; moving or deleting it breaks that
launcher. Persist the PATH line in your shell's startup file (`~/.zshrc` for
interactive zsh or the appropriate bash startup file), then open a new terminal.
Use `type -a ott` in bash/zsh to detect older aliases, functions or launchers that
take precedence. In the remaining examples, replace `ott` with
`python3 "$ott_script"` whenever using the direct invocation. If copying a source
archive instead, copy all Python files in `cli/` together; a Git checkout is needed
for the exact `git rev-parse HEAD` verification above.

### Install on Windows

Use PowerShell, Git and CPython 3.12 or newer (`py -3`). This installs the same
reviewed commit in a new directory without replacing an existing checkout or
creating/changing a launcher:

```powershell
$ottRef = '8c6a89989b6bedd703db9614779deef0b4e935a7'
$ottSource = Join-Path $env:LOCALAPPDATA 'ottplay-control-server-local-8c6a89989b6b'
if ($null -ne (Get-Item -LiteralPath $ottSource -Force -ErrorAction SilentlyContinue)) {
    throw "Already exists: $ottSource; inspect it or choose a new directory."
}
py -3 --version
if ($LASTEXITCODE -ne 0) { throw 'Python is unavailable.' }
git clone --no-checkout https://github.com/open-ott-play/ottplay-control-server.git $ottSource
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
$ottRevision = git -C $ottSource rev-parse "$ottRef^{commit}"
if ($LASTEXITCODE -ne 0 -or $ottRevision -ne $ottRef) { throw 'Reviewed source revision is unavailable.' }
git -C $ottSource checkout --detach $ottRevision
if ($LASTEXITCODE -ne 0) { throw 'Checkout failed.' }
$ottActualRevision = git -C $ottSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $ottActualRevision -ne $ottRevision) {
    throw 'Unexpected CLI revision.'
}
$ottScript = Join-Path $ottSource 'cli\ott.py'
py -3 $ottScript --help
py -3 $ottScript diagnostics --help
py -3 $ottScript report verify --help
py -3 $ottScript t1 perf start --help
```

In the remaining examples replace `ott` with `py -3 $ottScript`, for example
`py -3 $ottScript a1 doctor --json` after configuration. Store configuration in a
directory protected by NTFS permissions for your account; POSIX mode 600 does
not establish Windows access control. Files inherit the parent directory's ACL.
Use `--config C:\private\cli.json` before the player name if you choose a location
other than the default under your home directory.

### Update, select or roll back the CLI

Keep the existing checkout, launcher and private configuration. Run the relevant
installation block above to add this revision beside an older installation. If
the revision-specific directory already exists, inspect its `git status --short`
and `git rev-parse HEAD`; do not reset it, pull over local changes or mix in
individual modules. Choose another new directory if needed, updating all paths
in the example consistently. For a future update, use its reviewed full commit
and a new directory name in the same procedure.

Select the new CLI by its direct path, preserving the same `--config` path (or
existing `OTT_CONFIG`/default configuration). The following read-only check uses
the normal default configuration; put `--config /absolute/path/to/cli.json` or
`--config C:\private\cli.json` before `a1` if that is how you already run it:

```sh
ott_script="$HOME/.local/share/ottplay-control-server-local-8c6a89989b6b/cli/ott.py"
python3 "$ott_script" a1 doctor --json
```

```powershell
$ottScript = Join-Path $env:LOCALAPPDATA 'ottplay-control-server-local-8c6a89989b6b\cli\ott.py'
py -3 $ottScript a1 doctor --json
```

To roll back, set `ott_script`/`$ottScript` to the saved older checkout's
`cli/ott.py` and use the same Python invocation, or resume the preserved `ott`
launcher. For example, if the previous installation used the old default path:

```sh
ott_script="$HOME/.local/share/ottplay-control-server/cli/ott.py"
python3 "$ott_script" --help
```

```powershell
$ottScript = Join-Path $env:LOCALAPPDATA 'ottplay-control-server\cli\ott.py'
py -3 $ottScript --help
```

Substitute your actual previous path. Older CLIs may not understand workbench
commands or newer report formats; retain this reviewed checkout for offline
verification. These selection steps do not migrate configuration, rotate tokens,
re-register players or replace your existing `ott` launcher. Complete
[configuration](#configure-the-administrator-client) only for a new installation;
for an existing one, continue with the [workbench quick start](workbench.md#quick-start).

#### Persistently switch an existing macOS/Linux `ott` symlink

After checking the new CLI directly, this optional block replaces only the
`~/.local/bin/ott` symlink. It preserves the previous link as
`ott.before-local-8c6a89989b6b`, stages the replacement beside it, then switches atomically.
It refuses a regular launcher file, a directory and occupied staging/backup paths;
inspect a custom launcher separately. Close other installer/update processes
before running it. This changes no configuration or running service.

```sh
python3 - update <<'PYTHON'
import os
from pathlib import Path
import sys

launcher = Path.home() / ".local/bin/ott"
backup = launcher.with_name("ott.before-local-8c6a89989b6b")
staged = launcher.with_name("ott.switch-local-8c6a89989b6b")
reviewed = Path.home() / ".local/share/ottplay-control-server-local-8c6a89989b6b/cli/ott.py"
if not launcher.is_symlink() or launcher.is_dir():
    raise SystemExit("Expected an existing ott symlink to a file; inspect it first.")
if os.path.lexists(staged):
    raise SystemExit("Staging path exists; inspect it first.")
if sys.argv[1] == "update":
    if os.path.lexists(backup):
        raise SystemExit("Backup path exists; inspect it first.")
    if not reviewed.is_file():
        raise SystemExit("Install and verify the reviewed checkout first.")
    target = str(reviewed)
    os.symlink(os.readlink(launcher), backup)
elif sys.argv[1] == "rollback":
    if os.readlink(launcher) != str(reviewed) or not backup.is_symlink() or not backup.is_file():
        raise SystemExit("Launcher changed or previous target is unavailable; inspect both links.")
    target = os.readlink(backup)
else:
    raise SystemExit("Choose update or rollback.")
os.symlink(target, staged)
os.replace(staged, launcher)
print("Selected:", launcher, "->", os.readlink(launcher))
PYTHON
```

To roll back, run the same block with `python3 - rollback` in its first line.
The backup remains available and the old checkout must still exist. A failed
switch may leave the backup/staging link for inspection; do not overwrite it
blindly. After either operation, open a new terminal and check `type -a ott` and
`ott --help`; an alias, function or earlier PATH entry can still select another
installation. On Windows, retain the direct `py -3 $ottScript` selection above;
update a custom launcher only after inspecting how it selects the script.

### Update the installation used by the target player

The CLI, command server and each player frontend have separate installations.
A healthy, updated controller can deliver a command to an older player that
does not implement it. Update the component serving the target instance:

- **CLI and controller:** update the Python CLI files and the Go command server
  separately. Updating the controller does not replace player files.
- **Local web player:** install or build the updated player files in the local
  web server's served directory, then reload that browser page. Another source
  checkout or a published site does not update this directory.
- **Hosted player, including here.now:** publish the updated frontend to the
  exact site the player opens, then reload that page. This updates that site;
  it does not update a local web installation or an installed native app.
- **Packaged Tauri or Capacitor player:** install an app release containing the
  updated embedded frontend. Reloading its page or relaunching the app uses the
  installed files and does not install a newer native release.

Use `ott --json tv caps` to read the target's `player.version`, `player.runtime`
and available operations. `ott --json tv status` also reports its UUID and
catalogue readiness. After updating, verify the expected player version and
the same target UUID; a completed reload should have a new runtime identity.

### Configure the administrator client

Start and validate the command server using the [deployment guide](deployment.md).
The CLI is a one-command process, not another background server. For a new
installation, create the private directory (`mkdir -p "$HOME/.config/ottplay-control"`
and `chmod 700 "$HOME/.config/ottplay-control"` on macOS/Linux), then create
`~/.config/ottplay-control/cli.json`; this is separate from the server's `config.json`.
Preserve an existing CLI configuration when updating; the following is a new-file
example, not a replacement for saved aliases, native bindings, presets or credentials:

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
such as `a1` and `living_room`. Top-level command names are reserved, regardless of
case: `add`, `alias`, `approve`, `devices`, `diagnostics`, `discover`, `handoff`,
`help`, `pair`, `pending`, `presets`, `report`, `resolve` and `server`. New `add`
and `alias` operations reject these names. Top-level commands take precedence
over player aliases, including before `load PRESET`.

Existing aliases are preserved. If an older CLI accepted a reserved name, use
the device UUID for player commands or add another name with
`ott alias living_room DEVICE_UUID`; the existing access code is unchanged.
An existing alias cannot be reassigned to another UUID without editing `cli.json`.

`pair` prints the device access code, so use it in a private terminal. Enter that
address and code in the player's command-server settings, select **Connect**, and
wait for **Connected**. Then verify the connection before sending a change:

```sh
ott devices
ott tv                      # available controls first, then readable player status
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

If an older CLI started an unfinished `add` with a now-reserved name, keep the
journal and repeat `ott add ORIGINAL_NAME UUID` with that original name and the
same configuration. This exception only resumes an existing matching journal;
it does not permit a new registration under a reserved name. Do not change the
name to work around the error or delete the journal. After recovery succeeds,
add a usable alias with `ott alias living_room UUID` for player commands.

`pair` displays the existing device token, **not the admin_token**. It does not
register a device or change its credentials. Open
**Settings → Remote control → Command server** in the player and enter the
address and access code. Saving the second field starts the connection. If both
values are already saved but the client is disconnected, select **Connect** and
wait for **Connected**. Each browser origin, TV and Tauri instance has its own
settings. For a new browser address, add its exact origin to `allowed_origins`
and restart the command server.

Enabling this connection authorizes the controller to operate the player and
request diagnostics and supported screenshots. There is no additional player
trust switch or ten-minute authorization prompt. Screenshots can include
settings, PIN screens and visible credentials; connect only to a controller you
trust. Browser screenshots still require a local source selection, and diagnostic
operators still need their separate server scopes. See [screenshots](#remote-screenshots)
and [diagnostics](diagnostics-cli.md).

HTTPS pages, including here.now, require an HTTPS command server. Tauri uses its
native HTTP bridge. TV browsers send outgoing XHR requests and do not need an
incoming port on the TV. Packaged TV apps with an Origin of `null` can use the
separate `allow_null_origin: true` setting, which applies only to the device API.
The central player server on ports 8443–8446 is separate from the command server.

## Disconnect, deregister or revoke a player

These are different operations. There is currently no `ott remove`, `delete`,
`deregister` or `unpair` command. The player authorizes remote support through its
enabled controller connection; there is no separate diagnostic consent to grant
from the CLI.

### Temporarily disconnect or forget a connection

On the player, open **Settings → Remote control → Command server → Disconnect**.
Polling, diagnostic authority and screenshot access stop, but the saved address
and access code remain; **Connect** restores support with a new runtime. Native
screenshots become available again when the adapter is ready; browser screenshots
need a newly selected source. To forget the connection on that installation, clear the server address and
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

With a player name, `ott d1`, `ott d1 status` and `ott d1 st` contact that player
and print its available commands first, followed by a readable status summary.
The ordinary output uses English labels and does not dump the raw status JSON.
Use `ott --json d1 status` for the unchanged machine-readable status and
capabilities fields; scripts should consume that JSON instead of parsing the
human summary. Update the CLI installation to obtain this output format;
updating only the controller or player does not replace the CLI.

Illustrative output for a standby player; the advertised command list is
shortened here, and the version is an example:

```text
$ ott d1
Available controls now:
  ott d1 providers  — list allowed providers (alias: provs)
  ...
Local diagnostic help: ott d1 test list
Request IDs: ott --receipt d1 COMMAND  — write RPC receipts to stderr

Player summary:
  Player:       d1
  Status:       Standby
  Now playing:  Nothing (standby)
  Provider:     M3U
  Volume:       100%
  Channels:     0 (catalog not ready)
  Platform:     browser
  Version:      1.1.52-beta.48
  Background:   suspended (0 pending)
  Kiosk:        Off
```

`Awake` means standby is off; `Now playing: Not reported` means this response
does not confirm current content. On older players,
`Connected (standby not reported)` leaves standby unknown. The provider label `Local videos`
names the local-file provider; it does not mean the player is disconnected.

Options for the ordinary CLI must precede `PLAYER` or the management verb:

```sh
ott --help
ott -c /private/cli.json tv
ott --config /private/cli.json --timeout 60 --json tv status
ott -t 60 -j tv channels
ott --refresh tv p --list "Кино"
ott --receipt tv restart
```

- `-c FILE` / `--config FILE`: configuration path; otherwise `OTT_CONFIG`, then
  `~/.config/ottplay-control/cli.json`.
- `-t SECONDS` / `--timeout SECONDS`: how long the CLI waits for a response, in
  seconds; finite value from 1 to 300, default 45. For example, `-t 45` explicitly
  selects the default response timeout, not a 45-second playback limit or delay
  before playback starts. Put it before `PLAYER` or the management verb.
  Multi-step searches/setups can take longer than one request budget; see their
  sections below. Increasing it does not extend a command's server-side TTL.
- `-j` / `--json`: machine-readable results for player commands and for
  `presets`, `discover`, `pending`, `approve`. `devices`, `add`, `alias` and `pair`
  still print text, even with this option; `pair` includes the private device token.
- `--refresh`: bypass EPG history/search and archive-probe caches on the
  configured central EPG path. It does not reload the player or playlist.
- `--receipt`: write one safe JSON line to stderr for every completed or failed
  player RPC, preserving the command's normal stdout. Its base fields are
  `action`, `request_id`, and `status` (`ok`, `rejected`, `unsupported`, or
  `unknown`). A lost submit reply has `request_id:null` and `status:"unknown"`;
  the CLI does not resend the POST. Multi-step commands emit one line per RPC.
  Group commands also include `player` to identify the target alias.
  Existing human-readable stderr diagnostics may accompany these JSON lines.
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

Use the [revision-specific installation and selection procedure](#update-select-or-roll-back-the-cli)
for updates. Install a reviewed commit into a new directory, verify it directly,
then select its script or deliberately switch the existing symlink. Retain the
previous checkout for rollback; do not move an installed revision's checkout to
a different commit or mix individual Python modules from different versions.
Keep `cli.json`, presets and server credentials outside the checkout. Updating
CLI files does not update or restart the command server, native apps or players
already open in a browser. After updating a hosted player, reload it and inspect
`ott PLAYER caps`.

To uninstall the symlink installation, remove only the `~/.local/bin/ott` link you
created and, when no longer needed, its source checkout. Keep private configuration
until any required deregistration is complete. Uninstalling the CLI does not
stop the controller or revoke player/operator credentials.

## Run a command on several players

Replace a single player name with an explicit comma-separated list. The same
command and arguments run independently on each distinct player, using the
ordinary single-player implementation:

```sh
ott t1,t2,t3,t4 R                 # choose a random channel independently on each
ott t1,t2,t3,t4 random            # same command; r also works
ott t1,t2,t3,t4 next
ott t1,t2,t3,t4 prev
ott t1,t2,t3,t4 +15               # advance 15 channels with each player's wrapping
ott t1,t2,t3,t4 volume 35
ott t1,t2,t3,t4 standby           # stop playback while retaining remote control
ott t1,t2,t3,t4 go
ott t1,t2,t3,t4 restart           # reload each player
ott t1,t2,t3,t4 restart stream    # restart each current stream
ott t1,t2,t3,t4 caps
ott t1,t2,t3,t4                   # available controls and readable status for each
ott -j t1,t2,t3,t4 status
ott t1,t2,t3,t4 provider m3u 1
ott t1,t2,t3,t4 plex shuffle "Family videos"
ott t1,t2,t3,t4 settings
ott t1,t2,t3,t4 setting bufSize 3
ott t1,t2,t3,t4 load Home plan    # inspect a configured private preset
ott t1,t2,t3,t4 load Home apply   # separate default checkpoint per player
```

Keep the list in one shell argument: `t1,t2,t3,t4`, or quote it when adding
spaces (`"t1, t2, t3, t4"`). Up to 64 input names are accepted in one list;
repeated names count toward this limit. Names and UUIDs are resolved locally
before any player command is sent. Empty entries, unknown or ambiguous names,
invalid ordinary command arguments and conflicting explicit output paths fail preflight.
Aliases for the same UUID are deduplicated, retaining the first name and list
order. An exact existing alias or UUID containing a comma keeps its single-player
meaning; use different aliases to express a list.

Commands run in order. A failure on one player does not prevent execution on the
remaining players. There is no group rollback or automatic replay of failed or
uncertain requests. Each command retains its normal per-player timeout and any
specific options such as update `--wait`; a group can therefore take longer than
one timeout. Capabilities, settings locks, native bindings and local permission
requirements are checked independently by the existing command. Random choices
are independent, and the players need not have matching providers or catalogues.
The group is not an atomic or synchronized playback operation.

Session, operation, category, favorite and media IDs are passed unchanged to each
target. For example, `perf stop SESSION_ID` only stops that ID where it exists;
it does not select each player's current session. Plex IDs identify items in
that player's configured Plex server. Use `caps`, `perf status`, `plex status`
or another appropriate read command to inspect individual outcomes.

### Group results and interruption

Text output labels each player's result. `-j` / `--json` emits one aggregate
object containing `action`, ordered `results`, `succeeded` and `failed`. Every
result has `player` and `ok`; it can also contain `data`, `error`, `warnings` or
`output`. General group commands include the original handler's `exit_code`.
The existing provider and notification result formats retain their established
fields without adding `exit_code`. Keep stdout separate from stderr when parsing
JSON:

```sh
ott -j --receipt t1,t2,t3,t4 R > random-results.json 2> random-receipts.log
ott -t 60 -j t1,t2,t3,t4 doctor
```

`--receipt` keeps one safe receipt per RPC on stderr and adds the target `player`;
human warnings may also appear there. A multi-step command can produce several
receipts for one player. An acknowledgement does not prove visible playback or
completed application restart.

The aggregate exit code uses this precedence: `130` (interrupted), `1` (error),
`2` (failed diagnostic check), `3` (partial or unknown result), then `0` (all
succeeded). Consult each result for its command-specific meaning. Ctrl-C stops
the remaining player commands; unstarted targets are reported as interrupted.
It cannot recall a command already accepted by a player. Inspect uncertain
outcomes before explicitly retrying only the affected targets.

Group capture retains at most 4 MiB of stdout and 256 KiB of stderr per player,
and 16 MiB of captured output across the group. Overflow is reported as incomplete
output; a command may already have executed and is not replayed. Use narrower catalogue
pages or a single-player command when a large result exceeds these limits.

### Separate files and checkpoints

Explicit output paths receive a player suffix. A file suffix goes before the
last extension; a directory suffix goes after its complete name:

```sh
ott t1,t2 shot -o screen.png                    # screen.t1.png, screen.t2.png
ott t1,t2 android screenshot -o native.png      # native.t1.png, native.t2.png
ott t1,t2 profile export 1 profile.json         # profile.t1.json, profile.t2.json
ott t1,t2 bundle --out ./bundle                 # ./bundle.t1, ./bundle.t2
ott t1,t2 test run health --report ./health     # ./health.t1, ./health.t2
ott t1,t2 perf export SESSION_ID --out ./perf    # ./perf.t1, ./perf.t2
ott -t 240 t1,t2 perf trial notifications --out ./trial
ott t1,t2 load Home apply --checkpoint /private/checkpoint.json
ott t1,t2 load Home resume --checkpoint /private/checkpoint.json
```

The checkpoint examples use `/private/checkpoint.t1.json` and
`/private/checkpoint.t2.json`; substitute an actual private directory. Resume
uses the same expanded paths and still checks the server, device, preset and
preset revision recorded in each checkpoint. Retain the same target names when
resuming an explicitly named checkpoint through a list. To resume only one player
with single-player syntax, pass its actual expanded filename:
`ott t2 load Home resume --checkpoint /private/checkpoint.t2.json`.
Defaults remain unchanged: screenshots already have unique filenames, and
default checkpoints include the device identity.

Simple aliases retain their names in the suffix, as in `screen.t1.png`.
The name component uses up to 48 ASCII letters, digits, underscores or hyphens,
replacing other characters with underscores. Names requiring replacement or truncation
also receive a 12-hex-digit suffix derived from the device UUID, independent of
which other players are in the list. Remaining filename collisions fail
preflight rather than sharing an output. Retain the same alias when resuming.
New explicit destinations must not already exist; their parent directories must
exist, except for checkpoint directories created by the checkpoint handler.
Existing resume checkpoints are checked rather than replaced. The ordinary
private-file and no-overwrite protections still apply.

Only output arguments are expanded: screenshot `-o` / `--output`, `profile export`
files, `bundle` and `perf export` / `perf trial` `--out`, test `--report`, and
explicit load `--checkpoint`. Input paths such as `provider-config`,
`profile-config`, `profile import`, Plex `--token-file` and settings `--value-file`
are passed unchanged to each player command.

### Scope and exceptions

Player lists also cover native Android commands and application
updates; each target still needs its own native binding or update capability.
`android bind NATIVE_ALIAS` accepts only one distinct player because a native
queue cannot be shared by multiple players. Bind them individually first.
`watch` retains its existing finite group collector and concurrency settings.

Controller and local-management commands keep their own syntax: `devices`,
`discover`, `pending`, registration/pairing/alias commands, `ha`, `server`,
`presets`, `resolve`, `diagnostics`, offline `report verify`, and the two-player
`handoff SOURCE TARGET`. They are not repeated for a player list. Global command
names after a player selector remain ordinary search text where the single-player
parser allows it; use `play TITLE` to explicitly search a reserved player-command
name.

## Commands

```sh
ott tv                      # available control commands, then readable player status
ott tv 12                   # one-based channel number from s
ott tv prev                 # previous channel in the current category; wraps
ott tv previous             # full spelling of prev
ott tv next                 # next channel in the current category; wraps
ott tv +15                  # forward 15 positions in that category; wraps
ott tv -15                  # back 15 positions in that category; wraps
ott tv news                 # list name matches and play a random matching channel
ott tv "РЕН"                # short case-insensitive channel-name fragment
ott tv "РЕН ТВ HD"          # longer channel-name query; same matching rules
ott tv play s               # channel named s, which is also a command
ott tv s                    # selected media category; full catalogue on legacy players
ott tv s HD                 # filter by channel name
ott tv categories           # media categories; selected category marked
ott tv cat "Tom and Jerry"  # browse a category without starting playback
ott tv cat all               # browse all videos
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
ott t1 provider m3u         # switch one player
ott t4 provider m3u 1       # switch, then play channel number 1
ott t4 provider m3u random  # switch, then choose a random channel (alias: R)
ott t4 provider m3u РЕН     # prefer an exact title, otherwise random partial match
ott t1,t2,t3,t4 provider m3u # switch four players, with one result per player
ott t1,t2,t3,t4 prov m3u     # equivalent short command
ott tv provider-config ~/private/xtream.json
ott tv playlist https://example.com/list.m3u
ott tv R                    # safe random channel from All (alias: r or random)
ott tv random 1 10
ott tv msg Hello
ott tv caps                 # version, runtime identity and supported controls
ott tv key enter            # one named OK/select input
ott tv input channel_up     # supported input, subject to local UI restrictions
ott tv pause                # pause supported archive/VOD playback
ott tv resume
ott tv seek 90.5            # absolute position in seconds; supported VOD only
ott tv aspect               # current Fit/Fill mode and saved-setting readback
ott tv aspect fit           # show the complete picture, with bars if needed
ott tv aspect fill          # proportional crop to fill the screen
ott tv aspect "Fill screen" # full mode name, case-insensitive
ott tv restart             # reload the player page after acknowledgement
ott tv restart player      # explicit equivalent of restart
ott tv restart stream      # restart only the current stream
ott tv reload               # reload the player page after acknowledgement
ott tv restart app          # relaunch a supported native app
ott tv standby              # enter player standby
ott tv go                   # leave player standby and start configured playback
ott tv wake                 # same operation as go
ott tv stop                 # standby: stop playback while remaining connected
ott tv start                # wake: start according to the player's startup settings
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
- `categories` / `cats`, and `category` / `cat`
- `p` / `programs` / `programmes`
- `prev` / `previous`; `next`
- `v` / `vol` / `volume`
- `vp` / `vportal`, and `vpr` / `vportal-random`
- `msg` / `message`
- `profile` / `prof`, and `profiles` / `profs`
- `provider` / `prov`, and `providers` / `provs`
- `capabilities` / `caps`, `input` / `key`, and `screenshot` / `shot`
- `aspect` / `aspect-ratio`
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
The `vp` / `vportal` setter requires the standalone VPortal provider.
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
an exact full-name match, that channel is selected directly. Otherwise the
player chooses an eligible match using the random policy below, after the CLI
validates the complete returned list. Duplicate exact names take precedence
over longer partial matches. `ott iphone s РЕН` only lists matches.
Numeric queries such as `ott tv 12` retain a single playback request.

Text queries print `NUMBER: NAME` rows to stdout and the selected channel's
switch acknowledgement to stderr. When multiple matches require a random choice, stderr also reports
`Randomly selected channel: NUMBER: NAME`; selection alone does not confirm
that the player accepted the switch. With `--json`, stdout contains the full
`channels` list and a `playback` object containing the player acknowledgement
or an `error` if switching fails. No matches means no playback request. An
invalid list or failed search never triggers a switch. The playback response
must confirm the selected number, ID and name before the CLI reports success.
A failed or uncertain switch exits with code 1 and is never retried automatically.
Keep the provider/catalogue unchanged between search and playback; a mismatching
acknowledgement reports an error but cannot undo a switch that already happened.
The numbers used by `s` and numeric `play` refer to the provider's full
catalogue, including when a media category filters the displayed rows.
Category positions are not channel numbers. `R`, `r` and `random` use the
provider's complete **All** catalogue, regardless of the displayed category.
The player excludes names containing `[AceStream]` and applies its configured
selection restrictions, including during an active local access session. This
does not remove entries from the catalogue or alter manual exact-name, number
or ID selection.

When both eligible pools are nonempty, random selection chooses numbers 1–1000
with 80% probability and higher numbers with 20%, uniformly within the chosen
pool. If only one pool is available, it supplies every choice. An explicit
`random FROM TO` range is inclusive and uniformly sampled, without the default
weighting or any fallback outside that range. No eligible channel means an
error without playback. Eligibility and source identity are checked again by
the player when playback is admitted; the CLI does not infer protection from
channel names or cache private access settings.

Random selection, including partial or ambiguous channel-name selection, needs
`capabilities.random.version = 1` and an updated controller. Older versions fail
explicitly; the CLI never falls back to an unfiltered random command. Unique
exact names, numbers and IDs retain their existing manual playback path.

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
of being silently skipped. The command's in-memory queue is not restored after
restarting the player. With the standalone VPortal provider selected, use
[`kiosk on`](#kiosk-mode) after playback starts to persist and repeat that selection.

## Media categories and stable video selection

On compatible Plex and standalone VPortal players, videos are available in
the **All** category without first choosing folders. Libraries, sections,
folders and series also appear as categories. A video can belong to several
categories; **All** contains it once. This does not add VPortal to the M3U
provider.

Update the controller, CLI and player together. Check `ott tv caps` for the
`categories` capability; the pinned CLI installation above includes these
commands. Unsupported players retain their existing `s` and name-play behavior.
Category commands explain when an update is needed. A network failure or invalid
capability response is an error,
not a reason to retry a command using a different protocol.

```sh
ott tv cats                         # short form: list categories
ott tv categories list              # explicit long form
ott tv category "Tom and Jerry"      # select one unambiguous category name
ott tv categories select "Tom and Jerry"  # equivalent long form
ott tv category all                 # return to All
ott --json tv categories            # inspect opaque category IDs
ott tv category id:CATEGORY_ID      # use the exact ID returned above
ott tv s                            # list videos in the browsing category
ott tv s "Пикник"                    # filter that category without playing
ott tv play "Пикник"                 # choose among matching titles and play
ott --json tv s                      # inspect opaque stable video IDs
ott tv play id:VIDEO_ID              # exact ID from this category's list
```

For a large library, request a bounded page or narrow the title filter:

```sh
ott tv s --limit 200                      # first page in the selected category
ott tv s --limit 200 --offset 200         # skip the first 200 matching videos
ott tv s --limit 50 "Tom and Jerry"       # one page of matching titles
ott tv s --limit 50 -- "--special title" # -- ends option parsing
ott -t 300 tv s                          # allow up to five minutes for the complete list
```

`s` without `--limit` still reads the complete matching list. The default
timeout is 45 seconds and the maximum is 300; a large list can take several
minutes. If that budget is insufficient, use bounded pages or a narrower
search. Long reads need an updated player that keeps an actively read catalog
receipt valid. The CLI waits within the same timeout when the controller's
retained request results temporarily fill its queue; it retries only rejected
read submissions, never category changes or playback.

`--limit` accepts 1–200, subject to the player's advertised maximum.
`--offset` accepts 0–1000000, defaults to zero, and requires `--limit`.
Offsets count matching rows in the selected category, not global channel
numbers. Returned channel numbers remain global. Text output reports the
number returned, total matches and whether more exist, then prints a next-page
command. JSON preserves `offset`, `total`, `has_more` and only the requested
`channels` page. An intentional page is successful even when `has_more` is
true; `loading` still marks incomplete discovery and exits with code 3.

Each invocation reads the current catalog. A nonzero page request first
obtains a category-bound receipt, then reads the requested offset using that
same snapshot. Separate next-page commands may see catalog edits or a changed
browsing category; do not combine them as if they were one frozen export.
Use the complete listing for a single-snapshot export. Legacy players reject
explicit paging with update guidance, while their ordinary `s` stays unchanged.
Title-based playback always reads all matching pages before choosing a video.

Replace `CATEGORY_ID` and `VIDEO_ID` with returned values, retaining their
case and punctuation. Names support case-insensitive Unicode matching:
an exact name takes precedence, otherwise a unique name fragment selects
the category.
Duplicate or ambiguous category names require an exact ID. A category
named `List` or `Status` can be addressed as `category select List` or by ID;
use an ID for a real category named `All`. `play cat` still plays a video
whose title is the command name `cat`.

Selecting a category only changes browsing. It does not start a video,
replace an explicit Plex queue, or change the category currently playing.
`next`, `prev` and signed offsets continue to use their existing playback
context; `R`/`random` uses All. Starting a video from the selected category enters that
category through the normal player admission and resume path. Local kiosk,
parental and busy-state restrictions still apply. Listing is read-only;
category selection does not unlock a restricted player.

The CLI reads pages from one catalogue snapshot. A title-based switch uses
the returned video ID and snapshot receipt, so a provider/profile change,
reload or catalogue edit cannot redirect the switch to a different video.
Receipts expire: repeat the listing after a stale-catalogue error. Failed or
uncertain switches are never repeated automatically. Numeric commands such
as `ott tv 12` keep their existing global-number behavior. On updated media
players, a number belonging to the browsing category starts there, so
subsequent `next`/`prev` stay in that category. A global number outside it
can still select a video through the normal catalogue fallback.

Catalogue discovery can continue while videos are already available. A
listing marked incomplete reports only loaded items and exits with code 3;
repeat it after discovery finishes. Name-based category selection and
title-based playback reject incomplete results. An explicit loaded ID can
be used without guessing from a partial name search. A changed snapshot
during pagination requires a fresh listing rather than mixing old and new
pages. No matches in an incomplete result does not prove the video is absent.

Troubleshooting:

- No category controls: check the active provider and `caps`, then update
  the player, controller and installed CLI. Reload the player after updating.
- Unexpected videos in `s`: inspect the selected category in `categories`,
  or select `category all`.
- A category switch is rejected: inspect `kiosk status` and the player's
  current UI; do not disable kiosk merely to inspect a catalogue.
- Catalogue changed or expired: wait for discovery to settle, list again,
  and use the newly returned ID. Do not reuse an old numeric position.
- Catalogue discovery failed: check the provider connection and reload its
  catalogue. Already loaded videos can remain available in the player, but
  remote category commands reject the failed partial scan rather than treat
  missing videos as absent.
- Plex queue controls still navigate a previous queue: browsing preserves
  that queue deliberately; start the intended video or use the explicit
  [Plex queue commands](#ordered-plex-queues).

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

## Local playlist resolver

`ott resolve --request-stdin` lets a local launcher such as VL use the same
EPG matching, archive-chain selection and media verification as the remote CLI.
It reads the configured hls-proxy playlist directly and returns candidates to
the launcher. It does not read `cli.json` or `OTT_CONFIG`, connect to the
command server, send a player RPC or start playback. `ott resolve --help` checks
that the command is installed without reading a playlist.

The command accepts exactly one UTF-8 JSON object on stdin, up to 64 KiB:

```json
{
  "version": 1,
  "query": "Tom and Jerry",
  "playlist_url": "https://proxy.example/playlist.m3u",
  "epg_url": "https://epg.example/epg/v1",
  "refresh": false,
  "cache_seconds": 7200,
  "playlist_cache_seconds": 604800,
  "archive_cache_seconds": 604800
}
```

`version`, `query` and `playlist_url` are required. `epg_url` is required only
when no playlist title matches and programme lookup is needed. The query is
limited to 1024 UTF-8 bytes. Cache settings are optional: guide/search TTL is
capped at two hours, playlist and successful archive checks at seven days;
zero disables the corresponding cache; `cache_seconds: 0` disables all caches.
An optional `cache_dir` selects a private
local cache directory. `refresh: true` bypasses caches for this request. Private
provider URLs belong in this stdin object, never in command-line arguments or
shared logs. A launcher should use a subprocess argument list and capture stdout.

Search order is playlist title, currently airing programme, then playable
archive within the channel's retention and 144 hours. Only named HTTP(S)
streams on the playlist's origin are eligible. EPG requests contain channel
metadata only. Matching archives start at the earliest playable programme in
each uninterrupted chain, using the same chain and media checks as remote
player searches. Programme starts/ends and retention limits can invalidate
cached results before their TTL expires.

Success exits with `0` and returns one JSON object, up to 16 MiB:

```json
{"version": 1, "kind": "none", "matches": []}
```

`kind` is `playlist`, `live`, `archive` or `none`. Each match includes `name`,
`group`, `url`, `archive_hours`, `epg_shift`, `tvg_id`, `tvg_name` and `search`.
Programme matches also include `mode`, `programme_start`, `programme_end` and
`details`. The caller lists candidates, selects one and owns playback; the
resolver never chooses or launches a player. Archive URLs use hls-proxy's
`utc`/`lutc` format. Immediately before playback, the launcher must recheck the
programme time/retention and refresh `lutc` to the current Unix time.

Output contains private media URLs, so the resolver refuses terminal stdout.
Capture it through a pipe, or redirect it to a file protected with mode 600.
Do not merge stderr into stdout or print the entire response in user-facing
logs. A cold EPG/archive search may take several minutes; the remote CLI's
`--timeout` does not apply. Launchers should cancel the subprocess when a newer
request supersedes it.

Failures exit with `1` (`130` for an interrupted search) and return
`{"version":1,"error":{"code":"resolution_failed","message":"..."}}`.
Other codes are `invalid_request`, `resolver_unavailable` and `interrupted`.
Messages are fixed English text and never include request values or provider
exceptions. Terminal-output refusal writes a fixed message to stderr without
printing JSON or URLs. An empty successful result is distinct from a failed
or incomplete search.

## Check every registered player

The source checkout includes an acceptance script that discovers the current
registry instead of using a saved list of player names:

```sh
python3 scripts/check_registered_players.py --probe-timeout 15 --timeout 45
python3 scripts/check_registered_players.py --archive-query "Programme title" --output /private/path/search-check.json
```

It reads `/api/devices`, checks each unique device ID once and attaches every
matching local alias. Devices without aliases are included; aliases missing
from the server registry appear separately. No personal device names or IDs are
embedded in the script. It uses the same configuration path as `ott`, including
`OTT_CONFIG` and `--config`.

Catalogue and channel-list probes use up to four workers (`--workers 1` through
`4`) and their own 15-second RPC deadline. A catalogue timeout is reported as
`unresponsive`, with further presence probes skipped. Unsupported RPCs, empty
catalogues and transport errors remain distinct. Neither an old `last_seen`
timestamp nor a timeout proves that a device is offline. Status/capability
responses do not gate catalogue checks.

For available catalogues the script calls the real shared current-programme
search with an empty query, requiring complete EPG coverage. `--timeout` supplies
the deeper RPC deadline and aggregate current-EPG budget. Optional
`--archive-query` also exercises the common archive search and availability
checks; those run sequentially to respect provider connection limits. A current
match is reported as `live_match`, never as successful archive coverage. Reports
include archive-resolution request counts and identify results returned entirely
from cached availability checks. `--refresh` bypasses archive-search caches;
cold history searches can take several minutes.

A read-only RPC allowlist prevents playback, restarts, profile/provider changes
and other mutations, including accidental calls from search helpers. JSON output
contains only identifiers, aliases, fixed statuses, counts and timings. It omits
media URLs, catalogue receipts, credentials, request queries and raw exceptions.
`--output` additionally writes that report atomically using private permissions
on macOS/Linux; use a private directory with suitable ACLs on Windows.

Exit `0` means every requested path was exercised successfully. Exit `3` marks
incomplete coverage such as unavailable/unsupported players, empty catalogues,
no archive matches, stale aliases or an empty registry. Exit `1` marks setup,
invalid-response or search failures, and `130` an interrupted check. An omitted
archive query is reported as `not_requested` and does not claim archive coverage.

## Provider settings

For individual application/provider preferences, use `ott f settings` to list
the runtime schema, `ott f setting bufSize` to read, and `ott f setting bufSize 3`
or `ott f setting players hls.js` to save with revision checks. See the
[typed settings guide](player-settings.md) for scopes, limits, effect timing,
private value files and recovery. Install `player_settings.py` beside `ott.py`.
Use `settings patch command_server|local_http [--value-file FILE]` to inspect or
atomically change a connection. Use `setting plexFolderPaths` for the full Plex
folder selection, with `add PATH TITLE`, `remove PATH`, or `clear` (all libraries).

A `provider-config` file contains `provider` and `settings`. Except for the new
bound VPortal slot API, select the intended provider with `provider` first.
VPortal files contain `profile`, `vportal`, and optional `name`; they can save an
inactive slot without selecting VPortal. Supplied fields are updated; others are
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
For other providers, use `settings` and the live typed schema when advertised;
only preferences absent from that schema require their local workflow.

## Plex libraries by name

Play items from a movie or show library using the player's saved Plex connection:

```sh
ott l plex libraries                         # list library IDs, names and types
ott l plex shuffle "Tom and Jerry"            # shuffle up to 500 library items once
ott l plex library "Tom and Jerry"            # play in library order
ott l plex library --preview "Tom and Jerry"  # check without changing playback
ott l plex library --shuffle --preview id:16  # preview one new shuffled order
ott l plex library --shuffle id:16            # explicit library ID, new shuffle
```

Large libraries with hundreds of episodes can take longer than a short 10–15
second timeout to collect. The CLI already defaults to 45 seconds, so the
explicit option below is optional:

```sh
ott -t 45 l plex library --shuffle --preview "Tom and Jerry"  # check without changing playback
ott -t 45 l plex shuffle "Tom and Jerry"                     # start a new shuffled queue
```

`-t 45` means **wait up to 45 seconds for the command response**; it neither
limits video playback to 45 seconds nor adds a 45-second delay. It is equivalent
to `--timeout 45` and must precede the player alias (`l` here). For these library
commands, capability discovery and the library request share that one budget;
the player collects the library before preparing the queue. The required time
depends on the library, server and network, so 45 seconds is not a guarantee.
If a playback request times out, inspect `ott l plex status` before retrying:
the request may already have been accepted. A new `plex shuffle` invocation
creates a new order and starts a new queue.

`plex shuffle NAME` is short for `plex library --shuffle NAME`. Command tokens
and options are case-insensitive. The player first matches an exact library
title ignoring case, then a unique title substring. Ambiguous matches fail;
use `plex libraries` and `id:NUMBER` to choose explicitly. Bare numbers remain
title queries here; `id:16` identifies a library, while `plex play 16` identifies
one media item. Use `--` before a title beginning with a hyphen.

These commands select Plex **libraries** (movie or show sections), not a series
inside another library or a filesystem folder. For example, a dedicated
“Tom and Jerry” library is independent from a series with that title in “TV Shows”.
Show libraries include their episodes. Lookup happens on the player using its
own saved Plex server and token; local CLI presets are not used or applied.

Discovery supports servers with up to 1000 library sections and lists only
movie/show libraries. Up to 500 items can be queued. With updated CLI, controller
and player support, a larger library uses the first 500 items in library order
and prints this warning before the usual queue result:

```text
Warning: The Plex library exceeds the 500-item queue limit; using 500 items.
```

For example, `ott -t 45 lg2 plex shuffle MultCocomelonALl` starts a shuffled
500-item queue when that library exceeds the limit. Shuffle randomizes the
selected 500, not a sample of the whole oversized library. Remaining items are
not appended automatically. Exactly 500 items need no warning. Preview applies
the same limit but does not start playback. `--json` remains JSON-only and adds
`"truncated": true` to the successful library result when the limit was applied;
that field is absent for a complete library.

The player advertises this behavior separately as
`plex_library_truncation: {"version": 1}` alongside its 500-item `plex_library`
capability. The CLI opts in automatically only when advertised. Install the
updated controller before deploying the new player and CLI: older controllers
reject the new opt-in request, and older players keep reporting an oversized
library error. A missing, ambiguous, malformed or incompletely fetched selected
set still fails without starting a queue; truncation is not a retry after an
error. The selected library and generated ID order are returned in `--json`
output. A shuffled preview and a later play are
separate requests and can produce different orders; to reuse a preview's exact
order, pass its `queue.ids` to the existing `plex play ID...` command.

Install a feature-bearing CLI, controller and player advertising `plex_library`.
The pinned CLI installation example above includes this extension; support
for `plex_queue` alone on the target player is insufficient. `ott l` lists these commands
when the connected player supports them. The CLI checks capabilities and sends
one runtime-bound library request within a single `--timeout` budget. It never
falls back to another Plex server, channel search or repeated playback request.
If acceptance is uncertain, inspect `plex status` before deciding to play again.

Preview does not replace current playback. Play reuses the existing Plex queue,
including parental and kiosk checks. It does not unlock controls, enable repeat
or change saved settings. `preparing` confirms acceptance and preparation, not
decoder playback. Existing kiosk policy determines whether the queue repeats;
shuffle changes the order once, not on each loop.

## Ordered Plex queues

Use the Plex item IDs from the configured Plex library. The player uses its
saved Plex server and token; the command carries only IDs and a runtime identity.
Install versions of the CLI, controller and player that advertise `plex_queue`
in `ott PLAYER caps`. Installing only the CLI does not update the controller or TV.
Existing `plex setup`, `server`, `token` and `token-file` commands keep their
configuration meaning.

```sh
ott l plex preview 78777 78776 78775  # read-only readiness check, in this exact order
ott l plex play 78777 78776 78775     # replace the queue and start the first film at 0
ott l plex play --shuffle 78777 78776 78775 # shuffle the supplied IDs once
ott l plex queue                     # same as plex status; does not start anything
ott l plex status
ott l plex next                      # next item, starting at 0
ott l plex prev                      # previous item, starting at 0
ott l plex previous                  # long spelling of prev
ott l plex stop                      # stop playback and clear the queue
ott --json l plex queue              # bounded metadata, no stream URLs or tokens
```

`play` and `preview` accept 1–500 positive decimal IDs, each at most 20 digits,
without signs, leading zeros, titles or URLs. Order and duplicate IDs are
preserved. Command spelling is case-insensitive. Preview checks the saved
configuration and every requested item without changing the provider, queue or
current playback. It reports titles in the submitted order. Readiness does not
guarantee later network availability or decoder compatibility.

Playback starts at the first item from zero. `play --shuffle` shuffles all
supplied IDs once in the CLI; ordinary `play` preserves the supplied order.
Natural completion advances through the list and stops after the last item
unless existing kiosk policy repeats it. Older players can advertise a lower
item limit; the CLI rejects oversized requests before sending a queue command.
`next` at the last item and `prev` at the first are rejected without wrapping or
stopping the current item. Ordinary `ott l next` and `ott l prev` use a retained
Plex queue atomically inside the player, including while preparing or after it
ends. They do not switch to a TV channel at a queue boundary. `plex stop` clears
the queue; channel navigation otherwise retains its existing category/wrap rules.
Queue state is local to the player runtime and is not a saved Plex playlist.

`preparing` means the request was accepted and preparation is in progress, not
that a decoder is playing. Query `plex status` for `playing`, `paused`, `ended`
or `error`. `active` means that a nonempty queue still owns navigation, including
in `ended` or `error`; it does not by itself mean media is playing. The machine
`index` is zero-based; the human summary shows position 1/N.

The CLI reads capabilities and sends one runtime-bound queue request within the
same `--timeout` budget. The player rejects stale runtimes and invalidates pending
preparation on request expiry or context change. The server TTL is independent
of the CLI wait: a timeout can leave acceptance uncertain and the request may
still execute before its server deadline. There is no automatic replay. Inspect
`plex status` before deciding whether to send another command; a missing receipt
does not prove that playback never started.

### Plex queue troubleshooting

- For a missing or ambiguous library, use `plex libraries`, then `plex library
  --preview id:NUMBER`. A series title inside a library is not a library name.
- An incomplete library is not started. Check that the library
  is stable and reachable; use an explicit smaller ID queue when needed.
- An oversized library should start or preview 500 items with a warning on
  updated installations. If it still reports `no partial queue was started`,
  check for `plex_library_truncation` in `caps` and update the player and CLI.
  If the controller rejects the opt-in request, update the controller as well.
- `plex_library` requires newer support than `plex_queue`. Updating only the
  Python CLI cannot add library lookup to a TV that does not advertise it.

- If the operation is missing or unsupported, update all three components and
  confirm `plex_queue.operations` in `caps` on the exact target player.
- If configuration or access is rejected, check the player's saved Plex server
  and token, then use `plex preview` to verify readiness without interrupting it.
- If an item is unavailable, check the IDs against that server's library. IDs
  belong to one Plex server; a title or stream URL is not an ID.
- If preparation fails or the runtime changes, read `plex status`. Do not repeat
  an uncertain `play`, `next` or `prev` automatically.
- Parental access, kiosk policy, network restrictions and decoder availability
  can still reject playback. A successful preview never unlocks those controls.

## Favorites across profiles

Save the item currently playing on the target player together with its active
provider and profile. You do not need to supply a profile number, channel ID,
movie ID or playback URL:

```sh
ott l fav add                       # save the current channel or supported video
ott l fav add "Вечерние мультфильмы" # give the entry a recognizable name
ott l fav                           # list entries, IDs and their saved profiles
ott l fav play "Вечерние мультфильмы"
ott l fav play fav-abc123            # use the actual ID printed by fav
ott l fav rm fav-abc123              # remove that saved entry
```

`favorite`, `favorites`, `favourite`, `favourites` and `favs` are aliases for
`fav`. `fav current [NAME]` aliases `fav add [NAME]`; `fav list` aliases the bare
`fav`; `fav remove` and `fav rm` are equivalent. A bare selector, such as
`ott l fav "Вечерние мультфильмы"`, is shorthand for `fav play`. To play an entry
whose name is a command word, use `fav play add` rather than `fav add`.
An explicit `id:fav-abc123` selector is also accepted. Names are matched on the
player, with an exact case-insensitive name preferred over a unique substring;
ambiguous names fail and can be resolved using the printed ID.

Adding the same item from the same profile/source again preserves its entry ID.
An explicit new name renames that entry; omitting the name keeps the old one.
Adding also uses the player's existing channel/media favorites storage.
`fav remove` removes the cross-profile bookmark only; it does not remove the
item from the provider's own favorites list.

Favorites live in the target player's persistent storage. They survive normal
player restarts and are shared across that installation's provider profiles;
they are not copied to every paired device or stored in the CLI's local preset.
Clearing the player's site/app data also clears these entries. Up to 100 entries
are supported. Labels and selectors allow 1–160 UTF-8 bytes, so a non-ASCII
character can occupy more than one byte.

Selecting a favorite first restores the saved provider/profile and waits for
its catalog to finish loading. It then selects the saved provider item and
resolves a fresh playable link. A temporary stream URL is not used as its
identity. A replaced profile source or an unavailable item fails instead of
silently playing the same channel number from another playlist or a similarly
named movie. An arbitrary URL, archive programme or item without a durable provider reference
cannot be added. Live channels with stable IDs and supported provider VOD items
are eligible; a Plex item remains bound to its Plex source. Numbered profile
restoration supports M3U, VPortal and Stalker; other providers retain their
single saved configuration. Existing kiosk and parental restrictions continue to apply.

The CLI discovers support first and sends one `favorites` request bound to that
player runtime. It does not read a potentially stale status snapshot and compose
a second profile/item selection. An updated CLI, controller and player with
`favorites` in `ott l caps` are required; `ott l` lists the operations currently
available. The entire capability-and-command operation shares `-t`/`--timeout`:

```sh
ott -t 60 l fav play "Вечерние мультфильмы"
ott --json l fav
ott --receipt l fav play fav-abc123
```

`Favorite playback dispatched` means the saved profile loaded and the item was
handed to the playback path after resolution. It does not establish that video
or audio is visible on the physical device. Inspect `ott l` or read a returned
request ID with `ott l operation REQUEST_ID` if playback is uncertain. A timeout
or lost acknowledgement never triggers an automatic replay.

Troubleshooting:

- **No current playable item:** start the desired channel/video on that player,
  then run `fav add`. The command does not guess from a highlighted menu row.
- **Unsupported item:** the active provider cannot offer a stable reference for
  that item. Saving a transient URL would not make it a reliable favorite.
- **Saved profile changed or unavailable:** restore its original source or save
  the desired item again under the new source. The CLI does not overwrite profile
  credentials to force a match.
- **Several favorites match:** list `fav`, then use the exact `fav-…` ID.
- **Storage unavailable:** inspect the player's storage/site-data settings and
  available space. A failed write is not reported as saved.
- **Timeout or player changed:** inspect the current state before issuing another
  playback request. Increase `-t` for a slow provider only on a new deliberate
  command; a timed-out request may already have changed the profile.

## Selecting a provider

Use a provider's ID, zero-based index or case-insensitive name. The provider list
marks the current selection with `*` and includes only providers allowed on the
target platform:

```sh
ott d1 providers
ott d1 provider m3u
ott d1 prov m3u
ott d1 profiles        # * marks the active profile; names are shown
ott d1 profile 1       # select the first profile, after checking its name
ott d1
```

`prov` aliases `provider`; `provs` aliases `providers`. Selecting a provider is
separate from selecting one of its numbered profiles. If status already reports
`"provider": "m3u"` with zero channels, M3U is selected: inspect `ott d1 profiles`
and select/configure the intended profile instead of repeatedly switching providers.
For example, `* 1: Home 1` means profile 1 named Home 1 is already active;
`playlist: set` means a URL is saved, not that its channels loaded successfully.
Provider changes remain subject to the player's settings lock and platform policy.

## Switch provider and start playback

Add a channel selector after the provider to switch and then request playback:

```sh
ott t4 provider m3u              # switch only
ott t4 provider m3u 1            # play channel number 1 after loading
ott t4 provider m3u R            # choose a random channel
ott t4 provider m3u random       # same as R or r
ott t4 provider m3u РЕН          # exact title first, otherwise random partial match
ott t4 prov m3u "РЕН ТВ HD"      # short command, complete channel title
ott t1,t2,t3,t4 provider m3u 1    # apply independently to all four players
```

The selector works with each configured provider's playable catalogue, not only
M3U. It searches the provider's complete eligible catalogue; for Plex and VPortal
this uses **All**, even if a narrower category was previously selected. Profiles
and provider credentials remain unchanged. A positive integer selects the
catalogue's channel number, as shown by `s` with All selected;
provider indices shown by `providers` remain zero-based. `R`, `r` and `random`
are case-insensitive aliases for the same safe, weighted random selection
described above. A title selects a complete,
case-insensitive name match in preference to partial matches. If several exact
matches exist, one is chosen at random; if no exact match exists, one of the
partial matches is chosen at random. No match is an error and sends no playback
request.

The command waits for the requested provider and its complete catalogue within
`--timeout`, then sends playback once using the catalogue's identity. If the player
is in standby, the explicit playback selector wakes it after the provider switch;
a provider-only command keeps its existing standby behavior. It stops
if the provider or catalogue changes during selection. A provider switch can
already have succeeded when loading or playback fails; the command does not
roll it back or replay an uncertain request. Playback acknowledgement means
the player dispatched the request, not that a picture has appeared.

The player and controller must support catalogue-bound playback; random or
ambiguous-name selection also requires the version-1 random capability. Older versions
that lack it are reported as unsupported; the CLI does not substitute an
unbound channel number. The legacy TV/Local videos catalogue API is limited to
10,000 entries and its response-size limit; larger catalogues fail explicitly.
Plex/VPortal use the paginated media catalogue. Increase the per-player budget
with `-t 300` before the player names when a configured provider loads slowly.

Complete provider names and unambiguous name fragments keep their existing
meaning. Use `--` to make the boundary explicit when a provider name could be
confused with a provider plus a channel selector:

```sh
ott t4 provider "Local videos" -- random
ott t4 provider m3u -- "РЕН ТВ HD"
```

## Switch providers on several players

Put the player names in one comma-separated argument. The single-player command
and the `prov` alias remain available:

```sh
ott t1 provider m3u
ott t1,t2,t3,t4 provider m3u
ott t1,t2,t3,t4 prov m3u
ott t1,t2,t3,t4 provider m3u random
ott -t 10 --receipt t1,t2,t3,t4 provider m3u
ott --json t1,t2,t3,t4 provider m3u
```

The CLI resolves every name before sending any provider change. An unknown name
or an empty member such as `t1,,t2` stops the group without sending a command.
Aliases that resolve to the same device are sent only once, using the first name
in the list. A complete, literal alias containing a comma in an existing manual
configuration keeps its single-player meaning; use separate aliases or device
IDs to address a group in that case.

Requests run in order. `--timeout` applies separately to the complete operation
on each distinct player (including optional catalogue loading and playback),
so four unresponsive players can take up to four response timeouts. Text output
identifies each player's result as it completes; `--json` returns one aggregate
object with `action`, ordered `results`, `succeeded` and `failed`. Each result has
`player` and `ok`, with acknowledged `data` and/or an `error`. When a provider
switch succeeds but the following step fails, both fields are present: `data`
retains the provider acknowledgement and `playback` reports the failed `stage`,
`error`, and whether playback was dispatched (`false` if not sent or explicitly
rejected, `null` if its outcome is unknown). Successful playback has
`stage:"completed"`, `dispatched:true` and the selected channel.

A runtime failure on one player does not prevent requests to the remaining
players. Exit status is zero only when every distinct target acknowledges the
provider switch and, when requested, playback; errors return one and interruption
returns 130. Requests are not retried and successful changes are not rolled back after another target
fails.

An acknowledgement confirms that the change was dispatched, not that a playlist
loaded or playback started. Check the individual player's status and profiles
if M3U has no channels. The same list syntax also works with the other
[player commands](#run-a-command-on-several-players), including random playback,
volume, standby, restarts, settings and diagnostics.

## Local folders and shuffled playback

Use `local` for files connected to a player. The old `offline` command spelling
and provider identifier remain compatible with saved configurations. The label
in the player is **Local videos**. A NAS or server source still requires a network
connection; the name does not promise that every source works disconnected.

```sh
ott t1 local sources
ott t1 local shuffle Movies
ott t1 local play Movies
ott t1 local connect /Volumes/data/Movies/
ott t1 local shuffle /Volumes/data/Movies/
ott t1 local sources --paths
ott t1 local sources --path /Volumes/data/Movies/ --paths
ott t1,t2 local shuffle Movies
ott t1 provider local
```

`Movies` is the exact name or source ID of a connected source on the target
player. A duplicate name is rejected; use the ID printed by `local sources`.
Literal colons are accepted, for example `ott t1 local shuffle "Kids: Cartoons"`,
when that folder exists on a filesystem permitting the name. Names are limited
to 512 UTF-8 bytes and cannot contain directory separators or control characters.
`shuffle` uses only that source, randomizes its complete playable list once and
starts its first item. `play` uses the source order. This differs from
`provider local R`, which selects just one random item from the current catalogue.
The normal player repeat setting controls what happens at the end of the list.
The selected folder and a compact shuffle seed are saved. Reloading the player
restores the same order while the folder's contents are unchanged; adding or
removing files produces a new deterministic order from that seed. Running
`local shuffle` again creates a fresh shuffle.
The maximum is 50,000 playable items per source, or the lower `max_items` limit
advertised by the player. A larger source is rejected without starting a partial
queue. Local playback does not send the item list through the control server.

An absolute path refers to the **target player's filesystem**, not the working
directory of the terminal running `ott`. On a capable native player it connects
that directory, reuses an existing exact binding where possible, and then plays
it. `local connect` performs the connection without starting playback. Relative
paths such as `./Movies` are not accepted. Quote names and paths containing spaces.
The CLI does not upload or copy video files.

Browser players can use sources already selected and permitted in their local
file picker. A remote path cannot grant browser filesystem access. Capabilities
must advertise `local_media` and the requested operation; absolute paths and
`local sources --paths` additionally require `absolute_paths: true`. Plain source
listing omits private filesystem paths. Use `--paths` only when you need them for
a private local configuration; do not paste that output into public reports.
`sources --path PATH` asks the native player to resolve the path and list only
its matching connected source. This also recognizes operating-system aliases
such as `/var` and `/private/var`. It does not connect the folder or start
playback. An existing readable directory with no binding returns an empty list;
invalid or unreadable paths return an error. `--path` requires native path
support and does not imply `--paths` disclosure.

The request is tied to the discovered player runtime and is sent once. An
unavailable source, incomplete scan, empty folder or excessive catalogue returns
an error rather than playing unrelated files or silently truncating the list.
A timeout is an uncertain outcome, not permission to replay a shuffle. Read
`local sources` and player status before another mutation. An accepted response
does not prove that the decoder is rendering video. Update CLI, controller and
player together if the command is reported unsupported.

## Local videos profiles

Use `provider local` (display name **Local videos**, compatible wire ID `offline`) for files or folders connected
on the player. Install matching player and controller versions supporting
`offline_profiles` before using the new save/import/export commands. `ott PLAYER`
shows the commands currently available; `ott PLAYER caps` reports their capability.

```sh
ott d1 providers
ott d1 provider local
ott d1 profiles
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
# The original selector and short command still work:
ott d1 profile 1
ott d1 prof load 1
```

Connect a folder or files in **Local videos** on the player and select the desired
video before `profile save`. Wait for folder scanning to finish before saving or
binding a profile. Save captures the connected sources and selected file
in a numbered slot without starting playback. The optional name replaces the slot
name. Load selects that saved configuration; it is a playback request, not proof
that frames are rendering. Loading follows player settings and local access state.
There are 15 slots; `profiles` displays the active slot, source count, selected file
and whether local binding is required. M3U and standalone VPortal use their
provider-specific `profile-config` settings formats, and
`ott PLAYER load PRESET` remains the separate named-setup command.

Export writes a new, private JSON file (permissions `0600`) atomically; it refuses
to replace an existing file. The portable document contains only the profile name,
source names/kinds, selected relative filename, and an opaque binding identifier.
It contains **no videos, URLs, filesystem paths, native references, iOS bookmarks,
Android grants or browser FileSystemHandles**. The binding identifier is only a
lookup key into that installation's private bindings; it grants no filesystem
access. Source/selected names themselves may be personal information.

Import saves into an **inactive** slot and does not start playback. On the same
installation, the original binding can be reused only while its matching sources
and local permissions still exist. On another device, browser profile or origin,
first run `ott PLAYER profile load N` for the imported slot. This selects the
profile and opens local recovery when its binding is unavailable. In
**Settings → Provider → Local videos**, connect the original files/folder,
wait for scanning to finish, then choose
**Bind loaded profile to connected sources**.
The binding action is available only for a loaded profile. An import never guesses
a folder by name or recreates an OS or browser permission. Browser permission
prompts require local interaction. After binding, load the same profile again;
the exact selected file must still exist.

Portable documents are version 1, at most 12 KiB compact JSON, with at most 32
sources. Unknown fields, credentials/access objects, absolute or traversal paths,
invalid source references and duplicate JSON keys are rejected before a command
is sent. This older portable format also rejects source names beginning with a
URI-like prefix such as `Kids:`; use the Local folder commands and named preset
bindings for those literal names. The complete request must fit the controller's
16 KiB limit.

If the command is unavailable, select `local`, check `caps`, update both player
and controller and unlock settings/kiosk locally as appropriate. A “binding
required” result means the profile is saved but the player needs local access.
If a mutation times out or its acknowledgement is invalid, inspect `profiles`
before retrying; the CLI never automatically repeats the change.

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
ott tv profile-config 3 /private/profile.json
```

`profiles` lists all 15 slots in order, marking the active slot with `*`. It shows
the name, archive depth in hours and whether a playlist is configured.
URLs, provider keys and credentials are omitted from both normal and JSON output.
An existing history value that cannot be validated appears as `unknown` (`null`
in JSON). `profile N` selects that slot; it reports a switch request, not completed
provider loading or visible playback.

Use `profile-config N FILE.json` to update several settings together. Its contents
are the settings object itself, without a `provider` or `settings` wrapper:

```json
{"name":"Cinema","playlist":"https://provider.example/list.m3u","history_hours":168}
```

M3U settings accept only `name`, `playlist` and `history_hours`. VPortal uses its
own provider and profile storage. To configure it, select `provider vportal`,
then use `profile N vportal LINK` or a private `profile-config` file containing
`name` and `vportal`. The complete cabinet link has the form
`portal::[key:YOUR_KEY]https://provider.example/api/v1/`. Direct VPortal settings
updates while M3U is selected are rejected before mutation. Previously saved
portal links in M3U device profiles are inactive and remain stored.

Only supplied fields change. Editing an inactive profile leaves the active
playlist running. Renaming a profile does not reload channels. Changing
the active playlist or its archive depth reloads channels once.
Profile numbers are integers 1–15; `history_hours`
is an integer 0–8760. Archive depth tells the player what history to offer; the
provider must still supply that history. Names are limited to 256 UTF-8 bytes,
playlist URLs to 8192 bytes, with no C0 or DEL control
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
replace the example addresses and credential placeholders before use:

```json
"presets": {
  "local": {
    "m3u": [
      {
        "number": 1,
        "name": "Local 1",
        "playlist": "http://127.0.0.1:8090",
        "history_hours": 144
      },
      {
        "number": 2,
        "name": "Local 2",
        "playlist": "http://127.0.0.1:8090",
        "history_hours": 144
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
        "playlist": "https://playlist.example.com/channels.m3u",
        "history_hours": 144
      },
      {
        "number": 2,
        "name": "Home 2",
        "playlist": "https://playlist.example.com/channels.m3u",
        "history_hours": 144
      }
    ],
    "plex": {"server": "http://nas.example:32400", "token": "YOUR_PLEX_TOKEN"},
    "active_profile": 1
  }
}
```

`local` illustrates a playlist service on the player's own loopback interface.
`home` illustrates a service reachable from a TV or phone over HTTPS. Use URLs
reachable from the target player, not just from the computer running the CLI.
Both presets can use the same Plex credentials. The Plex address
must be reachable from each target device.
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
must include `number`, `name`, `playlist` and `history_hours`; numbers
must be unique integers from 1 to 15. `active_profile` must refer to an included
slot. Playlist URLs must be nonempty HTTP(S) addresses. Use IPv4 addresses in
four decimal octets (for example, `127.0.0.1`), not shortened or hexadecimal
forms. A nonempty legacy `vportal` string in an M3U setup entry is normalized
into the same numbered **dedicated VPortal slot**, preserving that slot's name.
It participates in comparisons and the setup fingerprint and is never sent as
an M3U field. An optional explicit `vportal` list accepts `number`, `vportal` and
optional `name`. Equal links deduplicate; conflicting explicit/legacy links fail
before writes. The local file is not rewritten. Old checkpoints that omitted
these required VPortal settings fail with a revision mismatch. See
[VPortal migration and examples](player-settings.md#independent-vportal-configuration).
Plex requires both `server` and `token`.
The existing provider length and request-size limits apply.

When VPortal slots are present, loading preflights all of them and performs
source-bound saves without switching provider or opening UI. Old players fail
before the first preset mutation. Loading then selects Plex, waits for its settings handler, saves its credentials,
then selects M3U and waits for the profile handler. It saves inactive slots
first and selects `active_profile`. If the previously active slot also needs
updating, it saves that slot after switching, avoiding an unnecessary playlist
reload. Finally, it checks the resulting M3U metadata and exact VPortal fields.
Unlisted M3U and VPortal slots remain
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
with remote M3U profile and Plex settings support. Presets containing local
folders also require the new `local_media` capability on the player and an
updated controller and CLI.

### Include local folders in a preset

The name of a CLI preset (for example `local`) is independent of its provider
sections. Add an optional `local` section inside that preset to deliver the
same connected folders along with its M3U/Plex settings:

```json
"local": {
  "sources": [
    {"name": "Movies", "path": "/Volumes/data/Movies"},
    {"name": "Kids"}
  ]
}
```

Each of the 1–32 entries describes a folder. Names must be unique. `path` is an
optional absolute path on the **target player**, not the CLI computer. Keep
this private configuration out of repositories and public diagnostics. Inspect
the source player's configuration explicitly with:

```sh
ott t1 local sources --paths
ott t1 load local plan
ott t1 load local
ott t1 load local verify
```

On a native player supporting absolute paths, loading connects that exact path
and verifies its folder identity. On browsers and other players without this
capability, loading can only reuse a folder already connected locally under the
exact name. It never transfers an OS permission, uploads media or substitutes an
unrelated directory. A missing or ambiguous browser folder stops the load before
provider settings are written. A native path that is missing, inaccessible or
overlaps a different connected root is reported as an error.

Folder connections are applied before the other provider settings and verified
again after the load. Other connected folders are retained. Loading a preset
does not start a local movie or shuffle the library; the existing final M3U
profile selection remains unchanged. Use `local shuffle Movies` afterwards to
play only that folder. The resume journal includes folder connection steps and
checks their actual state before repeating a confirmed operation. A lost reply
does not cause a blind retry; earlier successful connections remain available.

Each comparison phase reads a fresh source list once and reuses it for folders
whose canonical paths are already present. Other path spellings are resolved on
the target player. New connections still use fresh capability checks and
readback; the final verification reads the resulting state again.

The `local` preset section delivers folder configuration. The separate numbered
Local videos profile import/export commands still describe a saved selection
and its private binding; assigning a profile the name `Movies` does not select
or grant access to a folder with that name.

## Restarting playback or the player

`ott tv restart` requests a full player reload. `ott tv restart player` and
`ott tv restart p` are explicit equivalents. `ott tv restart stream` (or
`restart s`) restarts only the current stream through the playback backend.
Bare `restart` previously defaulted to the stream; scripts needing that behavior
must use the explicit `restart stream` command. Neither operation selects
another provider or profile. Backends
that cannot perform the requested operation return an explicit unsupported result.
Reload/restart is not an installation command. For a native app, install the
updated package first; for a web player, update the files at its actual serving
location. See [the installation update guide](#update-the-installation-used-by-the-target-player).

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
request app exit, never standby. `standby` and `go` are separate player-state
operations; `go` (also `wake` or `start`) cannot contact an offline or powered-off player.

### Stop playback while keeping the player online

For a player that stays on all day:

```sh
ott d1 standby          # stop playback and enter player standby; stay connected
ott d1 go               # leave standby and start configured playback
ott d1 stop             # same as standby
ott d1 start            # same as go; wake is also accepted
ott d1                  # available controls first, then the reported state
ott d1 1                # select channel 1 after starting
ott d1 restart stream   # reopen the current stream when one is active
ott d1 restart          # reload the entire player; restart player is equivalent
```

`standby` and `go` use the existing acknowledged standby/wake operations.
`stop` remains an alias for `standby`; `start` and `wake` remain aliases for `go`.
These names are case-insensitive and accept no extra arguments. The wire
operation is still `wake`, so `go` also works with older players advertising it.
They do not shut down the device or disconnect remote control. `pause`/`resume`
remain separate VOD controls; pausing can keep downloading buffered video.
`go` follows the player's startup settings, so it does not promise to resume
the same VOD at the saved position. Updated players save standby locally and
retain it after reload; `go` clears it. Older players can lose standby on reload.
Remote command polling remains active so the player can receive `go` and
diagnostics. Player standby does not suspend the operating system or network
activity owned by unrelated apps.

Updated players report `player.standby` and
`player.background: {"state":"suspended","pending":0}` in `ott -j d1`.
Plain `ott d1` includes these fields in the summary after the available controls.
Standby on means playback is stopped; standby off alone does not confirm that
video is playing. `suspending` means
cancellation is still in progress; `suspended` with standby on and zero pending
means the player reports its managed foreground and background work drained.
`unsupported` means its native host cannot establish that state; `error` means
the attempt failed. A null pending count means unknown, not zero. Older players
without these fields remain controllable, but cannot confirm background
quiescence. This snapshot is not a measurement of all host network traffic.

Use a current player: older builds can leave PiP or list-preview playback running
when entering standby, or complete pending Plex preparation afterward. Updated
players cancel that work and stop both media lanes. Kiosk and settings locks
still apply; `ott d1` lists standby/go only when standby/wake are advertised.
An accepted response is not a claim that teardown or startup already completed.
To play channels literally named `standby`, `go`, `stop`, `start` or `wake`, use
the explicit `play` prefix, for example `ott d1 play go` or `ott d1 play stop`.
Longer channel names such as `Goal` are not treated as command aliases.

A separately provisioned native Android agent also accepts
`ott d1 android standby` and `ott d1 android go`, with `stop`, `start` and `wake`
aliases. These address the bound native queue and retain native receipts; they
do not silently fall back to the web player's queue.

Lifecycle availability is platform-specific. The player must advertise and
implement the operation; an unavailable native exit, relaunch or OS reboot
returns unsupported. None of these commands falls back to another operation.
Existing `restart stream/player` retains its original request/ACK shape;
new lifecycle requests return `accepted: true`, `dispatched: false` and
`effect: "lifecycle-after-ack"`. The effect runs only after the player receives
the controller ACK, with current local restrictions checked again.

## Remote screenshots

```sh
ott tv                      # shows screenshot availability or browser source guidance
ott tv caps                 # machine-readable screenshot state and source
ott tv screenshot           # save one PNG with a unique name in the current directory
ott tv shot                 # exact short alias
ott tv shot -o living-room.png
ott tv screenshot --output /absolute/existing/directory/living-room.png
ott --timeout 60 --json tv shot -o incident.png
```

Install screenshot-capable versions of **the CLI, controller and the target player**.
The earlier stable controller v0.1.0 and player v1.1.52 do not implement this
operation. Updating the controller alone cannot add a native capture adapter to
an installed app. Follow [the installation update guide](#update-the-installation-used-by-the-target-player).
Use the existing [device registration and pairing](#register-and-connect-a-player);
there is no extra screenshot account, listening port or screenshot registration.
The screenshot connection requires an **HTTPS controller**, or HTTP on loopback
(`localhost`, `127.0.0.1`, `[::1]`). Plain HTTP to another LAN host cannot carry
screenshots, even when ordinary remote controls work over it.
The CLI enforces the same HTTPS/loopback policy on its configured `server`
download address before sending a request; a secure player upload does not
make a plain-HTTP CLI download private.

Enabling the player's Remote control connection authorizes this controller to
take supported screenshots. Native capture becomes ready automatically when
the connection and adapter are available, including after startup or reload;
there is no separate screenshot switch or ten-minute permission expiry. Only
connect to a controller you trust with full access to the player. Images can
include player settings, PIN screens, provider credentials and other visible
private data. The player does not mask those screens or refuse capture solely
because its page is in the background. The OS can still suspend the app or
restrict what its native adapter can capture.

In a supported desktop browser, open **Settings → Remote control → Select
screenshot source in browser** locally and choose a source in the browser's
screen-share picker. This browser requirement cannot be bypassed by a remote
command. Cancelling the picker leaves capture unavailable. **Stop browser
sharing**, an explicit disconnect, a controller address/access-code change or
reload releases the source; select it again before a new browser capture.
Remote screenshot requests never open the picker themselves. A temporary network
interruption does not itself clear the configured connection or selected source.

Older screenshot-capable players may still show **Allow screenshots for 10
minutes** and require their legacy local permission. Update the actual player
installation to use connection-based authorization; the CLI remains compatible
with the older capability state.

`caps.screenshot` reports `state: "ready"`, `"permission_required"` or
`"unsupported"`, plus `source` (or `null` when unavailable). `permission_required`
is the compatible wire name for a browser source still to be selected or a
connection that is not enabled; older players also use it for their local grant.
Source labels mean:

- `player-view`: the native player web view; native window chrome is excluded.
- `player-window`: the app window, including its native surfaces when supported.
- `browser-tab`: the browser tab selected in the local screen-share picker.
- `window`: a window selected in that picker.
- `display`: a whole display selected in that picker.

Availability depends on the installed platform adapter, OS and browser. An
Android app or LG TV must explicitly report support; a generic Capacitor or
browser build alone does not imply that screenshots work. Protected video and
hardware video surfaces may be blank. The receipt reports `video: "unknown"`
or `"excluded"`; a screenshot does not establish that video is decoding or
that every video surface was captured. An unsupported adapter has no simulated
DOM-image fallback.

The CLI reads capabilities and submits **one** screenshot request, bound to the
reported player runtime, within the single `--timeout` budget. A reload between
those requests invalidates that request; read the new capabilities and issue a
deliberate new command. In a browser, select the source again after reload. The
image is a PNG no larger than 1280×720 or 1 MiB. Both controller
and CLI validate the PNG and its metadata before accepting it. The existing
2 MiB response transport limit is unchanged.

By default a name such as `ott-tv-20261006T120000Z-a1b2c3d4.png` is generated.
`-o` and `--output` choose a **local** file; its directory must already exist.
Existing files, symlinks, symlinked parent directories and `..` path traversal
are rejected. Saving uses a private temporary file and an exclusive atomic
installation, so another process creating the destination cannot be overwritten.
Files use mode `0600` on Unix. On Windows, keep the containing directory's ACL
restricted to your account. No output path is sent to the player. `--json`
prints the local path, byte count, dimensions, capture time, runtime, source and
video limitation; it never prints the image/base64 data.

Screenshots use the authenticated command request/result API: the CLI's existing
administrator credential queues/reads the request and the device credential
uploads its response. A diagnostics-only operator token cannot request images.
The server keeps the receipt only in bounded memory for 60 seconds, with
`Cache-Control: no-store`; it does not save images to disk or log them. The player
drops queued/cached image bytes on disconnect, connection changes, browser sharing
stop, reload or when the
original request expires, and in all cases within 60 seconds of completing the
capture. Small rejection receipts prevent the same delivery from recapturing.
Revocation cannot retract an image already sent or already in flight: an accepted
server receipt retains its separate 60-second TTL. Local PNG files remain until
you remove them. Disconnecting/revoking the device uses
[the existing revocation procedure](#disconnect-deregister-or-revoke-a-player).
The enabled player connection authorizes both screenshots and diagnostics, but
their server APIs and credentials remain separate: an administrator can request
images, while diagnostic operators need their configured protocol-2 scopes.

## Capabilities, input and playback control

`ott tv`, `ott tv status` and `ott tv st` show commands for the controls that the
device currently advertises first, including `ott tv restart` when page reload
is available, then a readable player summary. They make two read-only requests
within one `--timeout` budget. `--json` retains the status fields and adds a validated
`capabilities` object. If that secondary request fails, is unsupported or belongs
to a different player runtime, status still succeeds with `capabilities: null`
and a `capabilities_error` explanation; text output shows the same limitation.
Unavailable controls are never inferred from another platform. Volume-only
queries (`v`, `vol`, `volume`) remain a single request with compact output.

The summary describes only fields actually reported by the player. Missing
values remain unknown or not reported; they do not become zero, off or stopped.
The status response does not provide a confirmed current programme or video
title. A provider, channel count or saved kiosk target describes configuration,
not proof of current playback. A kiosk target can remain saved while the player
is in standby. For a closer playback observation, use
`ott tv inspect --view media` or `ott tv doctor` when advertised; neither proves
that a physical screen or speaker is working.

When status identifies a provider and capabilities were read successfully, the
same list also includes the existing `providers`, `provider m3u` and
`provider ID_OR_NAME` commands. For active M3U or VPortal it includes `profiles`
and `profile N`, with a concrete `profile 1` example. Empty M3U catalogs also
show a reminder to inspect the active profile. These hints do not add discovery requests or
change the capabilities protocol; the player checks settings locks and platform
policy when the command runs. They remain visible with an empty or loading catalog.
When status reports kiosk enabled, listings remain visible but switching hints
are replaced with `ott tv kiosk off` guidance; displaying status never disables kiosk.

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

### Aspect ratio: Fit and Fill

Use `aspect` to read the current picture mode and its saved-setting state:

```sh
ott f10 aspect                 # read only; does not cycle the mode
ott f10 aspect get             # same read operation
ott f10 aspect status          # same read operation
ott f10 aspect fit             # Fit to screen: whole picture, bars if needed
ott f10 aspect fill            # Fill screen: preserve proportions, crop edges
ott f10 aspect "Fit to screen" # full name, case-insensitive
ott f10 aspect Fill screen     # full name also works as separate words
ott --json f10 aspect          # mode, saved_mode and persisted
ott --receipt f10 aspect fill  # retain the capability and set-request IDs
```

`aspect-ratio` is the full command alias. `f10` is an example registered-player
alias. `fit` uses contain; `fill` uses
cover. Fill never stretches the image. These explicit modes are separate from
`key aspect`, which retains the player's ordinary aspect-button behavior.
Only the documented names are accepted; use `play aspect` to search a channel
with that reserved name.

The CLI first reads `caps.aspect`, checks the requested operation and mode, and
binds the request to that page's runtime. Both steps share one `--timeout`
budget. The controller checks that success and rejection responses belong to
the same runtime and operation. A missing/unsupported capability or a read-only
capability rejects the change before any aspect mutation is sent. It never substitutes keypresses or
cycles through modes. The pinned CLI installation example in this guide includes
aspect support. Update both the controller and the target player too.

A successful read has this shape:

```json
{
  "version": 1,
  "runtime": "page-123-abcd",
  "operation": "get",
  "mode": "fill",
  "saved_mode": "fill",
  "persisted": true
}
```

`mode` is the current setting. `saved_mode` comes from persistent-storage
readback; `null` means no explicit saved mode was found or storage could not be
read. `persisted` is true only when a non-null saved mode matches the current
mode. A setting retained only in memory is not reported as persistent.

A set result reports `accepted: true`, `dispatched: false` and
`effect: "aspect-after-ack"`. It confirms acceptance, not application or saving.
Read `ott f10 aspect` again after the change to check both. The player rechecks
its runtime, media target and local access policy before applying an acknowledged
change. A navigation, reload, PIN prompt or kiosk transition can invalidate it.
On timeout or a malformed response the CLI does not resend the mutation; read
the current state before deciding whether to issue a new request.

To inspect a set request without repeating it, take the `request_id` from the
`aspect` line emitted by `--receipt` and run:

```sh
ott f10 operation REQUEST_ID --lane web --json
ott f10 aspect
```

Replace `REQUEST_ID` with that actual ID. The operation journal records aspect
sets, not reads. `accepted` or `invoked` with `handler_completed` evidence does
not prove the picture changed or the mode was saved; use the aspect readback
for current and persistent settings. An expired/missing journal entry is not
permission to replay a set.

Persistence follows the player's existing scope: a live channel keeps its own
setting, while VOD uses the shared media setting for subsequent media. Changing
one live channel does not override every channel. A saved setting is reused by
that scope after restart. Pixel output and physical-display presentation are not
proved by the readback.

Kiosk, parental locks and protected input continue to restrict changes. A
compatible player can advertise `get` alone while locked, allowing inspection
without granting `set`. If support is missing, check the player and controller
versions and the active playback engine. If `persisted` is false, check storage
availability on the player instead of repeatedly sending `fill`.

### Previous and next channel

When a Plex queue is retained, `prev` and `next` address that queue, without
wrapping or falling back to TV at its boundaries. This includes preparing,
ended and error states; clear it with `plex stop`. The channel rules below apply
when no Plex queue owns navigation. See [ordered Plex queues](#ordered-plex-queues).

```sh
ott l prev                 # previous channel on the player registered as l
ott l previous             # same command, full spelling
ott l next                 # next channel
ott --json l prev          # operation, dispatched and selected channel metadata
ott t1 +15                 # forward 15 positions in t1's playing category
ott t1 -15                 # back 15 positions, wrapping at either end
ott --json t1 -15          # also confirms the requested offset: -15
```

These commands move through the **currently playing category or favourites**,
with wrap from first to last and last to first. `prev` and `previous` move one
position backward; `next` moves one forward. `prev` means the
preceding entry in that list, not the previously watched channel. The player
uses its current playback selection when handling the request; a different
category being browsed in the open channel list does not change this order.
An admitted switch closes that list. A single-channel category selects that
same channel. Each command accepts no further arguments.

`+N` moves forward N positions and `-N` moves backward N positions, in one
atomic request. For example, in a 10-channel category, `+15` from position 8
selects position 3 and `-15` from position 3 selects position 8. Only the final
channel is selected; the player does not play the intermediate entries. A
whole number of complete laps selects the current channel again. These are
positions within the playing category, not channel numbers from `s`.

Offsets must use an ASCII `+` or `-` followed by decimal digits, with a value
from -9007199254740991 to +9007199254740991 excluding zero. `+0` and `-0`,
fractions, exponent notation and out-of-range offsets are local errors that
send no playback request. Leading zeros are accepted. Use `ott t1 15` for
absolute channel number 15 and `ott t1 v +15` for a relative volume increase;
neither is a channel offset. A negative offset needs no `--` before it. Names
such as `+HD` and `-Новости` keep normal channel search; use `play TITLE` to
escape a numeric-looking signed channel query.

`prev`/`previous`/`next` require CLI/controller v0.1.0-beta.42 or newer and player
frontend v1.1.52-beta.54 or newer on the target instance. The player advertises
`previous_channel` and `next_channel` in `caps.playback`;
bare `ott l` shows the corresponding `prev` and `next` commands. Signed
offsets require CLI/controller v0.1.0-beta.43 or newer and player frontend
v1.1.52-beta.55 or newer. Use CLI v0.1.0-beta.44 or newer for the Unicode
numeric-input validation fix. Bare status shows `+N` and `-N` when the player
advertises `step_channel`.
The CLI/controller and the target frontend must support the requested
operation. An unloaded or stale
channel selection, protected UI/PIN, standby, kiosk or settings lock can reject
the request. Unlock locally and retry only after checking the result of the
first request. These operations never fall back to UI keypresses: `key ch+`
and `key ch-` retain the normal remote-key meaning, which can paginate an open
list instead of changing playback.

JSON contains `operation`, `dispatched: true` and `channel: {id, number, name}`;
signed offsets also include the exact requested integer `offset`. The number
is from `s`, while the step follows the current category. A receipt
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
to the controller with its device credentials. Live-TV locking requires a loaded
channel catalogue. With the standalone VPortal or Plex provider, start the
required video/queue instead; a TV playlist is not required.

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
restarts remain available. In the web command API, only the `kiosk` request
changes this policy; the separate native maintenance lane is described below.

Use `ott tv kiosk on --strict [CHANNEL]` to let a short tap or the Info key display
the video footer for five seconds. Updated core players allow tapping or dragging
the footer timeline to seek within the current VPortal/Plex episode, while the
queue and strict lock remain in place. This requires a known finite duration;
older players and channel kiosks retain a read-only footer. The CLI receipt
confirms the strict policy, not support for local timeline seeking.

For example, with a saved Plex profile and the Plex provider selected:

```sh
ott f10 plex play 78777 78776 78775
ott f10 kiosk on --strict
# On the player: tap to show the footer, then tap or drag its timeline.
ott f10 kiosk off           # release the lock before replacing the queue
```

The seek gesture is cancelled if the episode, source, player or kiosk policy
changes, or if the gesture becomes a vertical swipe or uses multiple fingers.
Seeking stops at least one second before the end; normal playback can still
advance to the next episode afterwards. Local pause, keyboard/remote seeking,
volume/mute, menus and player exit remain blocked. Swipes and long presses outside
the timeline are blocked, and repeated taps do not expand details. Stopping the
current diagnostic capture remains available locally; it does not disconnect
the remote controller. Remote volume/mute and recovery are unchanged.

With an existing lock, `kiosk on --strict` upgrades it without changing the target;
with no TV lock it waits for the first UI selection. `kiosk set CHANNEL` preserves
strictness, or add `--strict` to upgrade during replacement. `kiosk off` releases
the lock remotely. The policy, including strictness, survives reloads. Old players
that omit or ignore the strict flag cannot produce a successful strict CLI receipt.
While `state` is `waiting`, strict input blocking is only armed: channel
selection in the player remains available until that selection establishes the lock.

For the standalone VPortal provider, select the configured profile, then run
`ott tv vp "tom and jerry"` followed by `ott tv kiosk on --strict`. This locks the
current repeating episode queue rather than arming a future TV selection.
The `vp` command searches the active standalone VPortal source; M3U no longer
loads portal links. Unlike TV kiosk name matching, `vp` collects all matching titles.
For example, to replace a lock with content from an already configured slot:

```sh
ott tv kiosk off
ott tv provider vportal
ott tv profiles
ott tv profile 2
ott tv vp --list "tom and jerry"
ott tv vp "tom and jerry"
ott tv kiosk on --strict
ott tv kiosk status
```

Use your configured slot number. `kiosk on/set QUERY` selects TV channels and
is rejected by the standalone VPortal/Plex providers. To choose another title
or profile, release the lock first and repeat the selection sequence.

A web page on here.now cannot block Android Home, Recents or browser navigation
outside the page. Android app pinning with a PIN, or managed-device Lock Task,
is needed to restrict exit from the browser.

Legacy wrappers may intercept touch before it reaches the web player. A wrapper
that only requests the information footer without forwarding timeline gestures
retains a read-only footer, even with an updated player. Native touch interception
does not by itself establish Android system-exit protection; check the wrapper's
documented capabilities separately.

The client retries the same channel every ten seconds without playback progress,
resolving its stream URL again. Healthy playback continues uninterrupted. The lock
uses channel and source identities, so list reordering or a missing channel never
selects an unrelated stream. Media startup and natural episode transitions have
a 60-second grace period. Newer players also use supported decoded-frame counters
to detect some stalls and can reload the player after repeated failed stream
recovery, with a ten-minute reload cooldown; offline recovery waits for the
network. See the [player recovery contract](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/remote-command-server.md#kiosk-mode)
for those version-dependent checks. Policy persists across page reloads and controller
outages, and is excluded from portable settings. The running page owns recovery;
this does not relaunch a crashed process or wake a suspended device.

The separately provisioned [Android maintenance agent](../native/android-agent/README.md)
can restart the application independently of its WebView. Its authenticated
`android queue next/prev` and `queue stop` operations can move within or release
the queue; ordinary remote playback commands remain subject to the kiosk lock.

`status` reports `off`, `waiting` or `locked`, channel/provider metadata,
`retry_seconds: 10`, retry count and observed health. A successful mutation means
policy was stored and any requested launch attempted, not that visible playback
was verified. Delivery and uncertain-result handling match the other CLI requests;
do not repeat an uncertain mutation blindly. Inspect `kiosk status` first.
On players that report them, `--json` also retains `startup_grace_seconds: 60`
for Plex/VPortal media queues, including older channel-backed queues, and
`screen_suspended`. A true `screen_suspended` reports the player's screen policy
suspending playback while the device screen is off or the app is in the
background. Offline playback can report this while kiosk is `waiting` or `off`:
`kiosk off` releases the kiosk lock but does not wake the screen or override that
provider's background policy. The returned `provider` belongs to the kiosk policy
and is `null` when that policy is off. The ordinary text status explains screen
suspension instead of leaving only `health: waiting`. The CLI still requires the
requested enabled/disabled state and, for `--strict`, explicit `strict: true`.
Absence of these optional fields on older players does not establish their state.

Direct API example, submitted to the existing authenticated `/api/requests`
route for the selected device:

```json
{"action":"kiosk","params":{"mode":"on","query":"12"}}
```

`status` and `off` accept only `mode`; `on` optionally accepts `query`, and `set`
requires it. Both `on` and `set` accept optional boolean `strict`. Omission
preserves the existing mode (ordinary for new locks); explicit `false` downgrades
an existing lock without releasing its target. Updated status receipts include
boolean `strict`. Queries contain 1–1024 UTF-8 bytes without control characters.

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
For read-only collection with your existing aliases, start with the
[workbench quick start](workbench.md#quick-start) and its
[troubleshooting](workbench.md#troubleshooting). For structured capture use the
[diagnostics workflow](diagnostics-cli.md), which requires separate operator
scopes and an enabled player connection.

### `ott: command not found`, wrong installation or missing Python module

Add `~/.local/bin` to the current shell's PATH and its startup file, then reopen
the terminal. Inspect `command -v ott` (or `type -a ott` in bash/zsh) for an older
installation taking precedence. Check the symlink target still exists. Use
`python3 /absolute/path/to/cli/ott.py --help` to separate PATH/executable problems
from Python problems. Keep all CLI Python files from the same source version in
the resolved target directory. A copied `ott.py` alone is not a complete install.
On Windows use the `py -3 ...` invocation from the installation section.
Stable CLI v0.1.0 does not provide `doctor`, `inspect`, `operation`, `bundle`,
`test`, `resolve` or `report verify`; install the
[reviewed source revision](#choose-the-cli-source-revision), then invoke its
script directly. Adding only the missing module to an old checkout is unsupported.

### A player list reports that only provider or notifications is supported

`Player lists support provider/prov or notifications; use ott t1,t2,t3,t4 provider m3u or notifications off`
comes from an older local CLI. It is not a rejection from the player. Install
the [reviewed CLI source](#choose-the-cli-source-revision), keep all its Python
modules together, then check `type -a ott` and invoke that checkout's `cli/ott.py`
directly if necessary. Updating only the Go server does not update this script.

With the updated CLI, start with `ott t1,t2,t3,t4 caps`, then retry the desired
command such as `ott t1,t2,t3,t4 R`. Individual unsupported or offline players
appear in the group result. Do not repeat the whole group after a partial
mutation; inspect results and retry only the intended failed targets. If an
export path already exists, choose a new base path and check the per-player
filenames described in [Separate files and checkpoints](#separate-files-and-checkpoints).

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

### A screenshot is unavailable, rejected or cannot be saved

- **No screenshot capability / unsupported:** update CLI, controller and the
  actual player installation. Older players omit the field. Do not repeatedly
  request captures or assume every TV/browser/native package supports them.
- **Permission required:** confirm Remote control is enabled. In a browser, use
  **Select screenshot source in browser** and finish its local picker; select
  again after reload, disconnect or stopping sharing. Older players may still
  require their legacy local screenshot grant until updated.
- **Rejected after readiness:** the connection, runtime or selected source may
  have changed, another capture may be running, or the OS/adapter may be unable
  to capture the current surface. Read `caps` and inspect the player before
  issuing a new request. Settings/PIN screens and background visibility are not
  additional application permission gates in updated players.
- **Invalid PNG/runtime/source:** no file is saved. Check matching released
  versions and inspect the player locally; malformed remote data is not printed.
- **Existing/unsafe output path:** choose another filename in an existing real
  directory. Existing files and symlinks are never overwritten. Check directory
  permissions and free disk space when saving fails.
- **Timeout:** an accepted capture may still complete before the request expires.
  The CLI does not repeat the capture automatically. Check connectivity before
  manually trying again; increasing `--timeout` does not select a browser source
  or bypass an OS capture restriction.
- **Black or missing video:** inspect the receipt's source and `video` fields.
  Native/protected video surfaces can be absent even when the surrounding UI is
  captured. Use playback diagnostics to investigate decoding separately.

### A control, provider setting or channel change is rejected

For `prev`/`previous`/`next`, first read `ott --json tv caps` and
`ott --json tv status`. Check the target UUID, frontend version and advertised
operations. A controller on v0.1.0-beta.42 or newer can still receive a rejection
from a player older than v1.1.52-beta.54; the CLI's generic rejection message
does not distinguish this from a local restriction. A newer here.now site does
not establish that a local web player or native app has been updated. Follow
[the installation update guide](#update-the-installation-used-by-the-target-player)
for that instance. A timeout means its current version could not be verified;
increasing the timeout or repeatedly issuing a channel change does not update it.

For `+N`/`-N`, check CLI/controller v0.1.0-beta.43 or newer and player frontend
v1.1.52-beta.55 or newer; the reviewed CLI source above also includes the
numeric-input validation added in stable v0.1.0. The target's `caps.playback` must
advertise `step_channel`. Support for `previous_channel` or
`next_channel` alone does not imply support for arbitrary offsets. Update the
installation actually used by that player; reload/restart does not install a
new embedded native frontend.

Read `caps` for currently supported lifecycle/input/playback operations. Ordinary
browsers cannot perform native app exit/relaunch or an OS reboot; OS reboot is
currently unsupported by every shipped player platform. `go` (alias `wake`) only addresses
player standby while connected, not a powered-off machine. Live streams do not
support the archive/VOD `pause`/`resume` controls or VOD seeking.

Check `kiosk status`, parental/settings locks, the active provider and whether
its catalogue has finished loading. Use local interaction for PIN entry and the
browser capture source picker.
Select M3U before managing its profiles; select the matching provider before
applying `provider-config` or Plex settings. Disabling kiosk or changing a lock
is a deliberate policy change, not an automatic recovery step. Successful settings
storage does not prove provider login, media availability or visible playback.

### A search is empty, incomplete, or plays an unexpected match

Use `ott tv s "РЕН"` or `ott tv p --list "Кино"` to inspect results without
switching. Channel queries use literal case-insensitive substrings: a full title
can still list a longer title. A unique exact name wins; partial or duplicate
exact names use the player's safe random policy. Kiosk title commands keep their
separate deterministic policy. Use the displayed catalogue number
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
Keep the original name even if the updated CLI reserves it for a command: a
matching legacy journal can still be resumed, but a different name cannot resume
that operation. Add a usable alias only after recovery finishes.
Check context, namespace, Secret, Deployment and Pod events. Connect the player
only after registration finishes successfully.

### Independent Android agent

For legacy tablets connected to a Linux host by USB, the separate
[USB recovery supervisor](android-usb-recovery.md) can dismiss a verified
non-secure swipe lockscreen after reboot. It is an explicit ADB backend,
not an `ott NAME android` command or an ARTEMIS helper.

`ott NAME android bind NATIVE_ALIAS` binds a separately provisioned native-agent
queue. Do not reuse a WebView device token. `android status`, `logs`, `screenshot`,
`recover`, `restart`, `reload` and `reboot` then use that independent channel.
`android queue play ID...` starts the exact VPortal IDs in a looping kiosk;
`queue status|next|prev|restart|stop` manages it. `queue stop` exits kiosk.

The experimental API 19 agent requires one-time root USB installation; commands
are unavailable until that installation and binding have succeeded. A published
server/CLI does not install the agent. Native restart/capture work independently
of the WebView; queue/playback commands need a responsive trusted player page.
See [installation, acceptance and signed updates](../native/android-agent/README.md).

### Signed KitKat updates over Wi-Fi

Once the separate native agent and its trusted update key have been installed,
use its existing connection to update the agent or `play.ott.kitkat` wrapper:

```sh
ott a1 android status
ott --receipt a1 android update https://updates.example/immutable-release/signed.json ENVELOPE_SHA256
ott a1 android status
ott a1 android operation REQUEST_ID
```

`ENVELOPE_SHA256` is the digest of the exact signed JSON bytes, not the APK or
agent digest. The signed manifest separately contains the payload digest and
size. Both HTTPS URLs must return the files directly; redirects are refused.
Use the already trusted signing key and preserve the existing APK signing
identity. The [native update guide](../native/android-agent/README.md#signed-updates-over-wi-fi)
describes packaging, host verification, compatibility and rollback limits.
The 0.1.2 source includes `native/android-agent/prepare_update.py` to prepare a
signed delivery and verify both published files without sending device commands.

`accepted` confirms admission, not installation. Keep the request ID from
`--receipt`, read its outcome without replaying it, and compare fresh native
status with the expected installed version and artifact evidence. Confirm
an intentionally running player reconnects and retains its kiosk/queue state.
For an agent-only update, preserve an intentionally disabled wrapper and any
other selected HOME app instead of starting playback for verification. A self-update can
leave an `unknown` operation receipt even if the replacement is running.
Do not repeat an uncertain update automatically.

With the 0.1.2 agent, `ott --json a1 android status` includes `updates.agent`,
`updates.apk` and `updates.touch_guard`. Compare their fresh `sha256` values to
the prepared payloads, along with the agent `version`, APK `version_name` and
`version_code`. `updates.apk.app_state` distinguishes an enabled from an
intentionally disabled package. `state: unavailable` means no usable evidence
was collected, not that the component is missing or still at the old version;
older agents can omit the entire `updates` section.

`updates.bundle_state` reports `absent`, `pending`, `committed`, `rolled_back`
or `unavailable`. With `updates.recovery_failed: true`, automatic pair recovery
has failed: health/log inspection and a signed agent-only repair remain
available, while app mutations, APK/bundle updates and the watchdog are blocked.
The saved transaction must be repaired, not deleted to bypass the restriction.

No USB or functioning WebView is required for an already provisioned agent to
receive an agent-only update. Network loss, a hung kernel or a failed native agent can
still require local recovery. An APK-only update does not replace the system
touch helper: the new APK's embedded helper must match it exactly, and the app
must be enabled and in the foreground. It has no automatic pair rollback.
The 0.1.2 agent adds a signed `bundle` containing the matching
APK/helper pair. Unlike an agent-only update, that transaction requires an
enabled, foreground, responsive player and refuses an intentionally disabled
app. Update older agents first, then follow the
[bundle compatibility and rollback procedure](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).
The hosted web player, controller and CLI are separate deliveries;
follow [the release and rollout sequence](../native/android-agent/README.md#release-and-rollout).
This root-agent path is distinct from the interactive Capacitor installer below.

### Native operation lookup

For an updated Android agent, use `ott a1 android operation REQUEST_ID` or
`ott a1 operation REQUEST_ID --lane native --json`. These read the durable native
receipt without repeating the operation. `handler_completed` confirms handler
return only; `unknown` must not trigger automatic replay. See
[native evidence and retention](../native/android-agent/README.md#durable-operation-history).

## Capacitor APK updates over Wi-Fi

[Краткая инструкция на русском](android-ota.ru.md).

A running Capacitor player can download and install an official release without
USB. Without `--version`, the command selects the highest numeric beta version
among the 100 releases returned by GitHub, regardless of their listing order.
Use `--version` to select an exact published release, including one outside that
page:

```sh
ott k8a update --variant api22
ott fire8 update beta --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22 --prepare-only
ott k8a update status
```

Bare `ott k8a` lists update examples when the player advertises the `AppUpdate`
capability. The `update`, `beta`, `status`, `prepare` and `install` words are
case-insensitive; APK URLs and SHA-256 values are preserved exactly.
With `--receipt`, each update RPC reports `action: "app_update"` and its request
ID on stderr without including the APK URL. A receipt with `status: "ok"` means
the RPC completed; an accepted install still needs confirmation through
`update status`, and may require a local Android installer prompt.

Use `api22` for Android 5.1/6 (including Fire OS 5), or `full` for Android 7+.
The current updater does not report a reliable API level, so the CLI requires
the variant rather than guessing from an alias. An explicit version can name a
published stable, beta or RC from `open-ott-play/ottplay-foss`; arbitrary download
URLs still use the low-level commands below. The CLI checks the published tag's
commit, release manifest, version plan, APK hash, size and version metadata before
sending the existing download request. Android additionally verifies the actual
APK's package, signature and strictly increasing versionCode. Selecting an older
version fails; selecting the already installed versionCode does nothing and is
not proof of byte-for-byte equality.

Stable releases promoted from an RC retain the original RC manifest and version
plan. The CLI accepts this only for a matching `promote-bytes` plan, published RC
and stable tags pointing to the same source, identical manifest evidence, and
matching APK size/digests. The APK keeps the final package version and original
versionCode; its download URL uses the selected stable tag. For example:

```sh
ott k8c update --version v1.1.60 --variant api22 --prepare-only
```

The command polls preparation, requests installation once, then reports verified
installed versionCode, pending permission/confirmation, or unconfirmed reconnect.
`--wait SECONDS` bounds the entire operation (default 300, maximum 900); metadata
requests are also bounded to 15 seconds each. It never replays an uncertain install.
Run `update status` after completing Android's installer prompt. The CLI does not
automatically check for new releases on a timer or wake a closed/unreachable player.

For a kiosk, first inspect `ott k8a kiosk`, record its target and strict setting,
then explicitly use `ott k8a kiosk off`. Unlock protected settings on the player
if required. Updating does not change PINs or automatically disable kiosk mode.
After installation and playback verification, restore the intended kiosk target
with the ordinary kiosk command; do not assume that a previously locked session
was preserved while it was explicitly disabled for maintenance.

A Capacitor player with the `AppUpdate` plugin supports the normal player queue:

```sh
ott f10 update status
ott f10 update prepare HTTPS_APK_URL SHA256
ott f10 update status
ott f10 update install SHA256
```

Use the signed APK and its exact SHA-256 from the same `ottplay-foss` release.
Wait for `ready` before sending `install`. The player requires a newer version,
matching application ID and matching installed signing certificate. No native
agent binding or USB cable is required after the first compatible APK is installed.

Turn kiosk off and unlock protected settings first. `install` is acknowledged
before the OS installer opens. Android may ask for installation permission and
confirmation on the device; this is not a silent root installation. If status is
`awaiting_permission`, grant permission on the tablet, then send `install` again.
An accepted request does not prove installation: check `update status` after the
player reconnects and confirm its installed version. Downloads and errors expose
only bounded metadata, not URLs, tokens or APK contents.

The existing `android update` command is separate: it addresses a provisioned
native/root agent and continues to use its signed manifest protocol.

### Strong-key HTTPS endpoints

For servers whose whole certificate chain supports OpenSSL security level 3,
set `OTT_TLS_SECURITY_LEVEL=3` when invoking the CLI. This raises (never lowers)
the host's TLS policy for controller, EPG, diagnostics and archive requests.
It rejects RSA-2048 as well as smaller RSA keys; use RSA-3072 or EC-256 and
strong trust anchors. Ordinary CA and hostname verification stays enabled.
See [the complete supported profile](security-design.md#supported-strong-key-cli-configuration).


## Resumable presets and explicit handoff

The optional new workflow is documented in [Reliable control](reliable-control.md)
([Русский](reliable-control.ru.md)). It requires this newer CLI/controller/player
revision, not the historical installation pin above. `plan` validates local
configuration offline; `verify` compares saved player settings without writing.
It does not switch providers. When Local, Plex, VPortal or another provider is
active, the result retains all settings comparisons and reports `different`,
the current `provider`, `expected_provider: "m3u"` and `active_profile: null`.
The selected M3U slot is checked only while M3U is active; another provider's
matching slot number cannot confirm it.
`apply` and `resume` require the controller journal and private checkpoint storage.

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
ott handoff living-room bedroom
```

An uncertain previous settings command prevents mutation until its deadline;
resume then compares current saved values before writing. Handoff v1 accepts
matching-source live/archive references only and does not stop the source player.
The existing `ott tv load Local` continues to use its original acknowledgement
workflow without a journal requirement.

For local files without Wi-Fi, see [Android local media](android-offline-media.md).
Current Android builds use the system file/folder picker and persisted read-only
access to the originals. The files and bundled player must be available on the
device; remote commands still need a connection. Per-device offline startup and
playback acceptance remain separate from implementation support.
