# Android tablet: Plex strict kiosk and startup

[Русский](android-kiosk-setup.ru.md) · [ADB guide](android-adb.md) ·
[OTT Home](../native/android-home/README.md) · [USB manager](android-usb-manager.md)

A dedicated tablet needs three separately verified settings: its content queue,
OTT's strict kiosk policy, and Android startup. Installing an APK or verifying
screen-off/resume does not establish startup after an OS reboot.

## Identify and install

1. Record the physical tablet, OTT alias/UUID, USB-manager alias, serial, model,
   Android ID and SDK. OTT and USB aliases can differ; do not infer identity from
   an alias or reuse another tablet's token.
2. Use the official APK variant compatible with that SDK, verify its release
   SHA-256 and package/signing identity, and install with `install -r`. Preserve
   player data. Use the [shared manager](android-usb-manager.md) when it owns USB;
   do not start a competing ADB server or use unscoped `adb` commands.
3. Pair the player with its own controller credentials and verify `ott TABLET`.
   Load the intended named preset and run `ott TABLET load PRESET verify`.
   Home-network presets and a development machine's localhost preset are not
   interchangeable.

## Verify Plex from the tablet

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Verify the library ID, title and item count. A successful request from an
administrator's computer does not prove tablet connectivity. Check the saved
address, routing, TLS support and credentials on the tablet when requests fail.
Prefer the server's valid HTTPS endpoint. If old firmware requires a private
LAN HTTP endpoint, keep it scoped to this installation, record that tokens and
media use unencrypted LAN transport, and do not rewrite a shared preset or
disable certificate verification globally. Never publish tokens or private
media URLs in diagnostics or documentation.

Changing the Plex server address clears selected library roots. Select the
**Plex** provider, open **Plex settings → Folders**, check the intended library
row (not just **Browse folders**), choose **Ok**, then **Save and open library**.
In player `1.1.58-beta.19`, server credentials alone can open the folder selector while
`ready` remains false; complete the folder selection before queue commands.
Do not substitute the M3U provider merely to obtain `ready: true`: a Plex queue
can play there, but `kiosk on --strict` can save a waiting M3U policy with no
bound media. That does not lock or restore the Plex queue.

## Start and lock the queue

```sh
ott TABLET kiosk off
ott TABLET plex shuffle 'LIBRARY NAME'
ott TABLET plex status
ott TABLET debug --engine
# Continue only after the intended queue is playing.
ott TABLET kiosk on --strict
ott TABLET plex status
ott TABLET kiosk status
ott TABLET debug --engine
```

For an ordered queue use `plex library 'LIBRARY NAME'` instead of `plex shuffle`.
Shuffle chooses one order; kiosk repeats it. Confirm the intended items and
`enabled: true`, `strict: true`, and a policy bound to the intended Plex media
queue. A `waiting` policy with no channel or media is not an accepted kiosk.
Observe at least two media samples with advancing position/frame count and inspect the display. An acknowledgement or `playing`
label without progress is insufficient. Test a natural end-of-item transition;
when testing an entire loop is impractical, record that limit explicitly.

Strict kiosk controls the player UI. It is not Android device-owner enrollment
or proof that Home, Recents, notifications and Settings are blocked. Keep the
existing system launcher and an attended maintenance route.

## Enable startup separately

Build and verify [OTT Home 0.2.0](../native/android-home/README.md), preserving its
existing signing key for updates. Install `play.ott.home` beside `play.ott.foss`.
Open `play.ott.home/.StartupActivity` and enable **Start OTT after reboot**.
The preference defaults off. It does not change the official player binary,
its OTA signature, profile data or Home-button assignment.

On firmware with a Home picker, optionally select **OTT Home → Always** as well.
Where the picker is unavailable (including tested older Fire firmware), the
boot opt-in supplies startup independently. Previously configured default-Home
installations retain their behavior. Disable both mechanisms for full rollback.

The shared USB manager's `recover_on_boot` is a third, independent mechanism:
it needs the NAS and USB cable, only handles a new boot, and does not continuously
restart a crashed app. It is not a substitute for autonomous startup. For an
autonomous-startup test, leave that recovery policy off so it cannot mask a
missing companion launch. Respect secure keyguards; unlock normally if asked.

## Acceptance and handover

Save evidence privately, with no credentials in public receipts:

- APK versions, hashes, physical/OTT/USB identity mapping and selected preset.
- Plex library/item count/order, strict policy and initial advancing playback.
- Boot ID before and after a real OS reboot; `sys.boot_completed=1` afterward.
- No manual player launch or USB recovery between reboot and observation.
- OTT foreground activity, controller reconnection, restored queue/strict policy,
  and advancing playback. A boot receiver's `requested` result is only dispatch.
- Separate power-button test: screen off, audio stopped, normal unlock and resume.
- Exact limits: secure unlock required, USB-assisted recovery, untested physical
  exits, stream failures or memory exhaustion. Do not call those fully accepted.

Before handing over the next tablet, use the same checklist rather than copying
a previous device's success claim. Older Fire tablets may need `hls.js` for M3U
because vendor native HLS can crash; this does not prove Plex playback stability.

## Maintenance and rollback

```sh
ott TABLET kiosk off
```

Open **OTT Startup**, disable **Start OTT after reboot**, and restore the original
Home selection if it was changed. If needed, uninstall only `play.ott.home` over
the verified USB transport; preserve `play.ott.foss` and its data. Disabling
startup does not alter the queue or automatically disable kiosk.
