# Control players from the terminal

The CLI uses CPython 3.12 or newer and its standard library; no `pip install` is needed.
Keep all seven files together: `ott.py`, `programme_search.py`, `playlist_search.py`,
`diagnostics.py`, `diagnostics_mcp.py`, `workbench.py` and `report_verify.py`. Python 3.12 is the version used by CI. Commands take a short
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
- [Resolve a playlist for a local launcher](#local-playlist-resolver)
- [Check searches on every registered player](#check-every-registered-player)
- [Provider settings](#provider-settings), [M3U profiles](#m3u-profiles) and [named setups](#named-setups)
- [Restarts](#restarting-playback-or-the-player), [input and playback controls](#capabilities-input-and-playback-control), [screenshots](#remote-screenshots), [kiosk mode](#kiosk-mode)
- [Scoped diagnostics and MCP](diagnostics-cli.md)
- [Read-only doctor, inspect, bundles and test scenarios](workbench.md)
- [Verify saved reports offline](workbench.md#verify-saved-evidence-offline)
- [Troubleshooting](#troubleshooting)

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
contain the Go server, **not** the Python CLI. The examples below install the
reviewed source revision
[`c1758f37a829f72b63ae50a979141a96871981d3`](https://github.com/open-ott-play/ottplay-control-server/commit/c1758f37a829f72b63ae50a979141a96871981d3).
It contains all seven CLI modules, including the local `resolve` command,
[workbench](workbench.md#quick-start) and offline `report verify`.

This is the **0.1.1 development line**, not a claim that a stable 0.1.1 binary
release has shipped. Stable CLI v0.1.0 lacks `resolve`, workbench and offline
verification. A server's `version` output, the repository's `VERSION` file and
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
  ott_revision=c1758f37a829f72b63ae50a979141a96871981d3
  ott_source="$HOME/.local/share/ottplay-control-server-c1758f37"
  python3 --version
  mkdir -p "$HOME/.local/share"
  if [ -e "$ott_source" ] || [ -L "$ott_source" ]; then
    printf '%s\n' "Already exists: $ott_source; inspect it or choose a new directory." >&2
    exit 1
  fi
  git clone --no-checkout https://github.com/open-ott-play/ottplay-control-server.git "$ott_source"
  git -C "$ott_source" checkout --detach "$ott_revision"
  test "$(git -C "$ott_source" rev-parse HEAD)" = "$ott_revision"
  python3 "$ott_source/cli/ott.py" --help
  python3 "$ott_source/cli/ott.py" diagnostics --help
  python3 "$ott_source/cli/ott.py" report verify --help
)
```

Run this installation directly, including when an older `ott` is already on PATH:

```sh
ott_script="$HOME/.local/share/ottplay-control-server-c1758f37/cli/ott.py"
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
  ln -s "$HOME/.local/share/ottplay-control-server-c1758f37/cli/ott.py" "$HOME/.local/bin/ott"
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
archive instead, copy all seven files in `cli/` together; a Git checkout is needed
for the exact `git rev-parse HEAD` verification above.

### Install on Windows

Use PowerShell, Git and CPython 3.12 or newer (`py -3`). This installs the same
reviewed commit in a new directory without replacing an existing checkout or
creating/changing a launcher:

```powershell
$ottRevision = 'c1758f37a829f72b63ae50a979141a96871981d3'
$ottSource = Join-Path $env:LOCALAPPDATA 'ottplay-control-server-c1758f37'
if ($null -ne (Get-Item -LiteralPath $ottSource -Force -ErrorAction SilentlyContinue)) {
    throw "Already exists: $ottSource; inspect it or choose a new directory."
}
py -3 --version
if ($LASTEXITCODE -ne 0) { throw 'Python is unavailable.' }
git clone --no-checkout https://github.com/open-ott-play/ottplay-control-server.git $ottSource
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
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
ott_script="$HOME/.local/share/ottplay-control-server-c1758f37/cli/ott.py"
python3 "$ott_script" a1 doctor --json
```

```powershell
$ottScript = Join-Path $env:LOCALAPPDATA 'ottplay-control-server-c1758f37\cli\ott.py'
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
`ott.before-c1758f37`, stages the replacement beside it, then switches atomically.
It refuses a regular launcher file, a directory and occupied staging/backup paths;
inspect a custom launcher separately. Close other installer/update processes
before running it. This changes no configuration or running service.

```sh
python3 - update <<'PYTHON'
import os
from pathlib import Path
import sys

launcher = Path.home() / ".local/bin/ott"
backup = launcher.with_name("ott.before-c1758f37")
staged = launcher.with_name("ott.switch-c1758f37")
reviewed = Path.home() / ".local/share/ottplay-control-server-c1758f37/cli/ott.py"
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
- `-t SECONDS` / `--timeout SECONDS`: finite value from 1 to 300; default 45.
  Multi-step searches/setups can take longer than one request budget; see their
  sections below. Increasing it does not extend a command's server-side TTL.
- `-j` / `--json`: machine-readable results for player commands and for
  `presets`, `discover`, `pending`, `approve`. `devices`, `add`, `alias` and `pair`
  still print text, even with this option; `pair` includes the private device token.
- `--refresh`: bypass EPG history/search and archive-probe caches on the
  configured central EPG path. It does not reload the player or playlist.
- `--receipt`: write one safe JSON line to stderr for every completed or failed
  player RPC, preserving the command's normal stdout. Each line contains only
  `action`, `request_id`, and `status` (`ok`, `rejected`, `unsupported`, or
  `unknown`). A lost submit reply has `request_id:null` and `status:"unknown"`;
  the CLI does not resend the POST. Multi-step commands emit one line per RPC.
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
six sibling Python files together from one version. Keep `cli.json`, presets
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
ott tv +15                  # forward 15 positions in that category; wraps
ott tv -15                  # back 15 positions in that category; wraps
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
- `capabilities` / `caps`, `input` / `key`, and `screenshot` / `shot`
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
  "query": "Three Cats",
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
ott l plex queue                     # same as plex status; does not start anything
ott l plex status
ott l plex next                      # next item, starting at 0
ott l plex prev                      # previous item, starting at 0
ott l plex previous                  # long spelling of prev
ott l plex stop                      # stop playback and clear the queue
ott --json l plex queue              # bounded metadata, no stream URLs or tokens
```

`play` and `preview` accept 1–100 positive decimal IDs, each at most 20 digits,
without signs, leading zeros, titles or URLs. Order and duplicate IDs are
preserved. Command spelling is case-insensitive. Preview checks the saved
configuration and every requested item without changing the provider, queue or
current playback. It reports titles in the submitted order. Readiness does not
guarantee later network availability or decoder compatibility.

Playback starts at the first item from zero, without shuffle, repeat or resume.
Natural completion advances through the list and stops after the last item.
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
request app exit, never standby. `standby` and `wake` are separate player-state
operations; `wake` cannot contact an offline or powered-off player.

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

Use `ott tv kiosk on --strict [CHANNEL]` to allow only a short tap or the Info key
to display a read-only video footer for five seconds. Local pause, seeking,
volume/mute, menus, player exit, swipes, long presses and multi-touch are blocked.
Repeated taps do not expand details. Stopping the current diagnostic capture remains
available locally; it does not disconnect the remote controller. Remote volume/mute and recovery are unchanged.

With an existing lock, `kiosk on --strict` upgrades it without changing the target;
with no TV lock it waits for the first UI selection. `kiosk set CHANNEL` preserves
strictness, or add `--strict` to upgrade during replacement. `kiosk off` releases
the lock remotely. The policy, including strictness, survives reloads. Old players
that omit or ignore the strict flag cannot produce a successful strict CLI receipt.

For the standalone VPortal provider, select the configured profile, then run
`ott tv vp "три кота"` followed by `ott tv kiosk on --strict`. This locks the
current repeating episode queue rather than arming a future TV selection.

A web page on here.now cannot block Android Home, Recents or browser navigation
outside the page. Android app pinning with a PIN, or managed-device Lock Task,
is needed to restrict exit from the browser.

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
from Python problems. Keep all seven CLI files from the same source version in
the resolved target directory. A copied `ott.py` alone is not a complete install.
On Windows use the `py -3 ...` invocation from the installation section.
Stable CLI v0.1.0 does not provide `doctor`, `inspect`, `operation`, `bundle`,
`test`, `resolve` or `report verify`; install the
[reviewed source revision](#choose-the-cli-source-revision), then invoke its
script directly. Adding only the missing module to an old checkout is unsupported.

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
currently unsupported by every shipped player platform. `wake` only addresses
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

### Independent Android agent

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

### Native operation lookup

For an updated Android agent, use `ott a1 android operation REQUEST_ID` or
`ott a1 operation REQUEST_ID --lane native --json`. These read the durable native
receipt without repeating the operation. `handler_completed` confirms handler
return only; `unknown` must not trigger automatic replay. See
[native evidence and retention](../native/android-agent/README.md#durable-operation-history).

## Capacitor APK updates over Wi-Fi

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
