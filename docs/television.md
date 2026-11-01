# Direct LG television control

The `tv` command controls a bound physical LG webOS television independently of
its OttPlay page. It can read and change **system volume**, wake or switch off
the television, and open the configured OttPlay application or web address.
The controller connects directly to webOS first. An explicitly configured Home
Assistant integration is a fallback.

The existing player commands retain their meanings. `ott l v 25` changes player
volume, `ott l standby` stops the player, `ott l go` wakes the player, and
`ott l restart` reloads its page. Use `ott l tv volume 25`, `ott l tv off` and
`ott l tv go` for the corresponding physical television operations.

## Commands

`l` and `lg2` below are ordinary saved player aliases. Bind their underlying
device IDs to their respective televisions on the controller.

```sh
ott l tv                       # read television state
ott lg2 tv status              # explicit form
ott l tv volume                # read state, including system volume
ott l tv volume 25             # set system volume to 25%
ott l tv volume +5             # increase system volume by 5 percentage points
ott l tv volume -5             # decrease it by 5 percentage points
ott l tv mute on
ott l tv mute off
ott l tv on                    # wake the TV without launching an app
ott l tv wake                  # alias for tv on
ott l tv off                   # switch off the physical television
ott l tv launch                # open its configured app/URL while it is awake
ott -t 60 l tv go               # wake, wait for direct control, then open OttPlay
ott -t 60 lg2 tv start          # alias for tv go
ott l tv apps                  # list applications reported by webOS
ott -t 60 l tv pair             # accept the connection prompt on this TV
ott l tv --help                # local help; no credentials or connection needed
ott -j l tv status             # validated JSON, including the selected transport
```

Absolute volume values are integers from 0 through 100. Relative changes are
nonzero integers from -100 through +100. Mute uses an explicit `on` or `off`;
there is no ambiguous toggle command. TV operations accept `--timeout` from 1
through 120 seconds, applied separately to each group member. Pairing may need
`-t 60`. For a slow cold boot, use `ott -t 90 lg2 tv go` so readiness and app
launch share a sufficient bounded budget. An accepted app launch does not
confirm playback: after the TV has finished starting, read `ott lg2 tv status`
for physical TV state and `ott lg2` for the player. Do not replay `go` merely
because its receipt was accepted rather than confirmed.

All ordinary player groups work with TV commands:

```sh
ott l,lg2 tv volume 20
ott l,lg2 tv off
ott group set televisions l,lg2
ott -t 60 televisions tv go
```

Each distinct player ID is attempted once, in order. One failure does not stop
the other members. Pair televisions individually: a multi-device `tv pair` is
rejected before sending anything. The CLI does not roll back successful group
members or repeat uncertain requests.

When a television binding exists, the ordinary `ott l` overview prints the TV
commands before querying the player. This help remains visible if the OttPlay
page is disconnected. `ott l tv status` bypasses the page entirely.

## Controller configuration

Configure `televisions` in the **controller's private configuration**, not in
the local CLI alias file. Use the device ID shown by `ott devices` for each
alias. Television control requires the controller's administrator credential:
the CLI's `server_config` must reference a private file containing its
`admin_token`. Player tokens and scoped `operator_token` credentials cannot
use this physical-device control lane. See [CLI configuration](cli.md) for
credential setup; never put the token in a command argument.

The following addresses and IDs are examples:

```json
{
  "televisions": [
    {
      "device_id": "living-room-player",
      "host": "192.168.1.50",
      "port": 3001,
      "tls": true,
      "client_key_file": "/var/lib/ottplay-control/private/televisions/living-room.json",
      "mac": "02:12:34:56:78:90",
      "broadcast": "192.168.1.255",
      "wol_port": 9,
      "launch_url": "https://player.ottplay.here.now/",
      "fallback": {
        "url": "http://homeassistant.local:8123",
        "token_file": "/var/lib/ottplay-control/private/televisions/home-assistant-token",
        "entity_id": "media_player.lg_living_room"
      }
    }
  ]
}
```

Add a separate binding for `lg2` with its own device ID, address, MAC and pairing
file. The controller must be able to reach each TV; reserve its address in DHCP.
Prefer TLS on port 3001. Port 3000 with `tls: false` is only for televisions that
need an explicitly configured unencrypted LAN connection.

`broadcast` is the IPv4 destination of the UDP Wake-on-LAN packet. A subnet's
directed broadcast address is appropriate when the network forwards it;
routers may filter directed broadcasts across subnets. The TV's own unicast
IPv4 address can also work across a routed network when the router retains a
usable neighbor mapping for the sleeping TV. This depends on the router and
TV network interface; unicast wake is not guaranteed. Test wake from the
controller's actual network location, including its container or pod.

Use either `launch_url` for the hosted player or `app_id` for an installed app,
never both. Discover actual app IDs with `tv apps`; the CLI does not accept
arbitrary launch addresses or arbitrary webOS commands. `launch` does not wake
a sleeping TV. `go` combines wake and launch. Opening an app is separate from
confirming that OttPlay connected or that its video decoder started playing.

Pairing saves a private JSON credential containing `client_key` and the
`certificate_sha256` identity. Keep its directory private and writable by the
controller, and keep the file at mode 0600 on persistent storage on Unix.
On Windows, pairing creates a protected file ACL for the current user, SYSTEM
and Administrators; imported key and token files must have an equally restricted
ACL. Unix-style `chmod` alone does not establish privacy on Windows. A previously
provisioned plain client-key file is also supported with an explicit
`certificate_sha256` pin in the binding. Do not place keys, HA tokens or private
configuration in source control, command arguments, screenshots or logs.

### Containers and Kubernetes

Pairing keys and fallback token files must be **regular files at mode 0600**,
readable by the controller. Symlinked files and group/world-readable files are
rejected. A projected Kubernetes Secret is therefore unsuitable as the direct
`client_key_file` or fallback `token_file`: its entries are symlinks, and the
chart's default Secret permissions are 0440. The server's main configuration
can still use its normal read-only Secret mount.

The image runs as UID/GID 65532 with a read-only root filesystem. Use the
chart's `journalExistingClaim` setting to mount an existing persistent volume
at `/var/lib/ottplay-control`; the same claim can hold journal, registry and
television data in separate paths. Preserve the existing claim and deployment
scheduling when updating. Provision `/var/lib/ottplay-control/private/televisions`
as a writable private directory for UID 65532. A new pairing creates its key
there automatically; provision an imported key or fallback token as a regular
0600 file owned by that UID.

For imported credentials, a temporary provisioning Job can mount the source
Secret and the existing claim, copy only the intended files while running as
UID 65532 with `umask 077`, and apply mode 0600 to the copies. Do not overwrite
an existing pairing key, print credential contents, or place credentials in
command arguments. The runtime image contains no shell or `tar`, so ordinary
`kubectl cp` into its container does not work. Remove the provisioning Job
after verification; retain the persistent claim. Verify ownership and mode
again after a rollout: recursive volume permission changes can otherwise add
group access that the credential reader rejects.

For Docker Compose, use the existing `compose.journal.yml` writable-state
overlay and provision its host directory for UID 65532 before startup. See the
[deployment guide](deployment.md) for the base Compose and Helm commands.

### First connection and pairing

1. Turn the TV on and permit network/mobile-device control in its settings.
2. Configure the correct host and writable private pairing-file path.
3. Run `ott -t 60 l tv pair` and accept the pairing request on that television.
4. Run `ott l tv status`, then verify system volume with a small explicit change.
5. Configure the app or URL and test `ott l tv launch`.
6. Enable network wake on the TV, configure its MAC and the appropriate LAN
   broadcast address, then test a complete off/on cycle while the TV is visible.

Pairing is explicit; ordinary operations do not wait for or complete interactive
pairing. A revoked key may still cause the TV to display a connection prompt;
run `tv pair` deliberately to complete registration.

### A changed certificate or replaced TV

`tv pair` preserves an existing certificate pin. Repeating it cannot override
a mismatch. First verify the physical television, its address, and the reason
for the changed identity. Do not disable TLS to suppress the error.

After that verification, stop new TV operations, move only that television's
old pairing file to a private recovery location outside its active key path,
and remove its obsolete `certificate_sha256` override from the binding if one
was configured. Validate the configuration and restart the controller when
the binding changed. With the TV visible, run `ott -t 60 l tv pair` to save a
new key and certificate identity, then check `ott l tv status`. Preserve the
other television's credentials and do not clear the whole private directory.

### Home Assistant fallback

The fallback uses a fixed native LG `media_player` entity and a long-lived token
read from the private `token_file`. Use the entity owned by Home Assistant's LG
webOS integration, **not an OttPlay proxy media-player entity**, which could
route the request back to this controller. Verify the selected entity's device
and integration in Home Assistant.

This control fallback is separate from the notification/event bridge. Enabling
or disabling notification banners does not select the TV control transport.
The output identifies `direct webOS`, `Wake-on-LAN`, or `Home Assistant
fallback`, so a successful fallback cannot be mistaken for a direct connection.

Fallback is only safe before an uncertain direct mutation. If a command may
already have reached the television, the controller reports uncertainty instead
of repeating it through Home Assistant. This matters especially for relative
volume changes. Inspect `tv status` before issuing another change.

## Results and monitoring

- **confirmed**: the returned evidence confirms the reported result. System
  volume and mute confirmation includes their observed value.
- **accepted**: the request was submitted, but its final effect has not been
  observed. A Wake-on-LAN packet or app-launch acknowledgement alone is not
  proof that a TV woke or that playback started.
- **uncertain**: the outcome could not be established. The CLI returns a failure
  status and does not replay the mutation.

Use `ott -j l tv status` for automation and `ott l` for the player overview.
The television and player are different observations: a TV may be on while
OttPlay is closed, or OttPlay may be in standby while the TV remains on.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No television is bound | Check that the binding uses the device ID behind the selected alias, then reload the controller configuration through the normal deployment process. |
| Direct connection unavailable | Verify the TV address, network-control setting and the controller-to-TV route. Test while the TV is awake. |
| Pairing is required | Run `tv pair` with the TV visible and accept its prompt. Verify that the credential directory is writable and persistent. |
| Certificate mismatch | Follow the changed-certificate procedure above. `tv pair` intentionally does not overwrite a saved pin. |
| Credential is invalid in Kubernetes | Use a regular 0600 file owned by UID 65532 on the private writable claim, not a projected Secret symlink; check permissions after rollout. |
| Wake packet accepted but TV stays off | Check its network-wake settings, MAC and broadcast subnet. Some TV/network combinations do not keep Wi-Fi reachable in standby. |
| Controller runs in Kubernetes or another subnet | Ensure the controller can reach webOS and send the wake packet to the TV's LAN. UDP broadcasts generally do not cross routed/container networks automatically; a working HA fallback may provide the LAN-side wake path. |
| HA fallback cannot wake the TV | Test the native LG entity directly in HA and configure its own wake support. Fallback cannot create an unavailable power-on capability. |
| Volume cannot be changed | Confirm system audio is controllable in the selected output mode. A fixed-level optical output or external receiver may need its own volume control. |
| App was accepted but player is disconnected | Check the configured app/URL and TV screen. `tv launch` does not prove that the page loaded or remote control connected. |
| Command timed out | Read `tv status` before repeating it. Increase the next operation's bounded timeout when pairing or waking; do not blindly replay a relative volume change. |

See also [CLI commands](cli.md), [player lifecycle controls](cli.md#restarting-playback-or-the-player)
and the separate [Home Assistant notification bridge](home-assistant.md).
