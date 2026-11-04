# Shut down a supported device

`ott PLAYER shutdown` requests a graceful device power-off. It works through a
locally configured Android ADB binding or an existing controller-managed
television binding. The player page does not need to be open for either route.
An unsupported device returns an error without sending a power command.

```sh
ott p2 shutdown --help
ott p2 shutdown status         # inspect the configured route without switching off
ott p2 shutdown               # request device power-off
ott p2 shutdown device        # the same operation
ott p1,p2 shutdown            # independent, parallel requests
ott projectors shutdown       # a saved player group also works
```

An accepted request is not proof that the device has finished shutting down.
The command reports an uncertain result if the connection disappears or the
deadline expires after dispatch. It does not repeat the command or switch to
another transport after an uncertain result. Check the device before deciding
whether another request is appropriate.

## Set up an Android projector

Install Android SDK Platform Tools on the computer running `ott`; `adb` must be
on its `PATH`. Enable USB or network debugging on the projector and authorize
that computer. ADB enabled alone does not guarantee that the firmware allows
shutdown. No separate OttPlay Android agent is required for this route.

Install the complete CLI from one reviewed source revision containing
`cli/device_power.py`, alongside `ott.py` and the other CLI modules. Updating only
the Go controller or the projector app does not install this CLI command. Check
`ott p2 shutdown --help` before configuring it. The source revision pinned in the [CLI installation guide](cli.md#choose-the-cli-source-revision) includes this feature.

First register the player alias using the [CLI registration workflow](cli.md#register-and-connect-a-player).
Then bind one explicit ADB target:

```sh
adb devices -l
ott p2 shutdown bind-adb 192.0.2.20:5555
ott p2 shutdown status
ott -t 30 p2 shutdown
```

`192.0.2.20:5555` is a placeholder: use the projector's actual ADB endpoint.
For USB, replace it with the exact serial shown by `adb devices -l`.
`bind-adb` reads the device identity and supported power command before saving
the binding. It does not shut down, reboot, root or install anything on the
projector. Complete any RSA authorization prompt on the device first.

The binding lives in `device_power` in the private CLI configuration, keyed by
the player's device UUID. Renaming an alias therefore keeps the same binding.
Before shutdown the CLI checks the saved Android identity again and requires
the firmware to advertise `svc power shutdown`. The request uses that graceful
Android service operation. It never substitutes a power-button toggle or a
forced reboot command.
The status probe checks the advertised method; firmware permission can still
reject the actual shutdown request.

### Firmware that asks for confirmation

Some projector firmware displays a system **Power off** confirmation even after
`svc power shutdown`. The CLI can complete the specific English dialog observed
on supported firmware: **Power off**, **Do you want to shut down?**, **CANCEL** and
**OK**. It verifies the focused window is owned by Android's system UID 1000,
uses package `android` and has type `KEYGUARD_DIALOG`. The labels, widget types
and resource IDs must match, including the firmware's actual
`android:id/alert_dialog_custom_btn_comfirm` spelling.

The CLI reads fresh button bounds, verifies the same system window still has
focus, then submits one tap to **OK**. Button coordinates are not saved for later
use. If this exact dialog is already open when you explicitly run `shutdown`,
the CLI completes it without sending another `svc power shutdown` request.

Other dialogs, translated text and changed layouts are not automatically
confirmed. This is a narrow compatibility path for the recognized system power
dialog, not a general dialog-clicking facility. Complete an unrecognized prompt
with the physical remote. A submitted confirmation still does not prove the
projector has finished powering off; firmware can refuse the operation or lose
its ADB connection while shutting down. No forced reboot is used as a fallback.

```sh
ott p2 shutdown unbind         # remove only this computer's local power binding
```

Binding and unbinding require exactly one player. `shutdown` and `shutdown
status` accept ordinary comma-separated lists and saved groups; each result is
reported separately. One failure does not undo another device's shutdown.

## Use an existing television binding

Without a local ADB binding, shutdown can use the same controller-managed
television route as `tv off`:

```sh
ott l shutdown status
ott l shutdown
```

Set up and pair the television using the [television guide](television.md).
Its existing authentication requirements, direct webOS route and configured
Home Assistant fallback still apply. An explicit local ADB binding takes
precedence: an unavailable or rejected ADB target does not silently select a
different television route.

## Choose the right operation

- `standby` stops player activity while retaining its remote connection; `go`
  resumes the player. Neither command promises physical device power-off.
- `restart stream` restarts playback; `restart` reloads the player.
- `exit` closes a supported app, while `reboot` restarts a supported device.
- `shutdown` requests device power-off through its configured power route.

A powered-off projector may need its physical remote to turn on again. Player
`go` cannot wake a device that has lost its remote connection. For a television
with a working configured wake route, use `tv on` or `tv go` as documented in the
television guide.

## Troubleshooting

- **Command missing:** install all CLI files from a revision containing
  `device_power.py`; check which `ott` launcher is on your `PATH`.
- **ADB target unavailable or unauthorized:** switch on the projector, check its
  current IP or USB serial, and accept the computer's RSA prompt. Run `adb
  devices -l` and `shutdown status`; neither requires a power change.
- **Device identity changed:** verify that the endpoint still belongs to the
  intended projector. Rebind explicitly after confirming the device, for
  example after a factory reset. Do not bypass the identity check.
- **Shutdown unsupported or permission denied:** the firmware does not expose
  or permit the operation. Use the remote control; the CLI does not escalate
  privileges or replace shutdown with standby.
- **Power-off confirmation remains on screen:** only the specifically recognized
  Android system dialog is eligible for automatic confirmation. A translated,
  changed or unrecognized dialog requires the physical remote. Do not repeatedly
  send shutdown to dismiss an unfamiliar prompt.
- **Uncertain result:** the projector may already be shutting down. Inspect its
  screen or power indicator before submitting another command. A disconnected
  ADB transport alone does not prove power-off.
- **No power binding:** configure one ADB target as above or configure a
  supported television on the controller. A browser or ordinary app connection
  alone is not a device power-control capability.
