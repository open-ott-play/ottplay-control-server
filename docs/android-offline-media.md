# Android local media and playback without a network

Current Android builds implement **Local videos** using the Storage Access
Framework (SAF), including the API 22 Fire variant. Select **Settings → Provider
→ Local videos**, then **Add folder** or **Add files** in the system picker.
OTT stores document references and persisted read-only grants; newly linked
videos remain in their original location. Older application-owned imports remain
compatible. This replaces the earlier feasibility proposal for an M3U playlist
and a separate local media service.

The player streams from the original document through its own loopback media
listener, with byte-range support. No broad all-files permission is required.
The storage must remain connected and the document provider must supply a
seekable file. Refresh discovers changes in connected folders. Disconnecting a
linked source removes OTT metadata and its access grant, not the original media.
Removing a legacy imported copy deletes the application-owned copy.

See the player documentation for the [Android source implementation and
acceptance checks](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/android-local-media-sources.md)
and [Local videos on all supported platforms](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/offline-media.md).

## Remote control

Update the player, CLI and controller together, then use the connected folder's
exact name or source ID:

```sh
ott d1 caps
ott d1 local sources
ott d1 local shuffle Movies
ott d1 local play Movies
ott d1 provider local
```

The `local_media` capability lists supported operations. Remote commands need
network access to the controller even when the videos are on the device.
Android does not accept a desktop filesystem path as a grant: connect the source
in its picker first. Tauri desktop builds that advertise `absolute_paths` can
also connect an absolute path on that target computer. Browser players use
previously selected sources and their browser permissions. The compatible wire
provider ID remains `offline`; the preferred CLI name is `local`.

When both the controller and player support progressive Local playback, `local
play` and `local shuffle` can accept a playable prefix while discovery continues.
The CLI marks the item count as partial. Support is negotiated per player;
installing a new CLI alone does not enable it on an older Android build. See
[Local playback readiness](cli.md#local-folders-and-shuffled-playback) for
completion, timeout and compatibility details.

The optional `local` section in a [named CLI preset](cli.md#include-local-folders-in-a-preset)
checks Android folders already connected under their exact names. It does not
copy files or transfer another device's permission. The separate numbered
[Local videos profiles](cli.md#local-videos-profiles) save a library selection and
selected item. Kiosk, parental settings and platform access restrictions apply.

## What works without Wi-Fi

Local playback requires the media to be physically available on the device or a
connected drive, and the APK must include its player assets. A NAS/Plex URL or a
cloud-only document is not a local media backup. This feature does not add a Plex
download cache, codecs or transcoding. SAF permission alone does not prove that
a cloud document remains available after connectivity is lost.

## Device acceptance and troubleshooting

Implementation and automated tests do not establish offline behavior on every
tablet or firmware. Check each deployed device:

1. Select a local H.264/AAC MP4 folder. Confirm OTT storage gains small metadata
   records rather than another copy of the videos; play, seek and change files.
2. Disable networking and repeat playback. Force-stop and restart OTT, then
   reboot; verify the saved selection and persisted source permission.
3. Test screen-off/resume and Standby/wake, including a pending folder scan.
4. Load the shared preset and install an update; verify the folder binding is
   retained. Remote control can be checked once networking is restored.
5. Disconnect the source or revoke permission. Confirm an unavailable-source
   result instead of unrelated playback, then reconnect through the picker.
6. Remove a linked source and confirm the originals remain. Verify any legacy
   imported copies separately.

If the picker is unavailable, check that the device has a working document
provider. If access is lost, reselect the original folder and keep its drive
connected. A non-seekable provider or unsupported codec needs another suitable
source/file; a server connection cannot repair it. If remote commands are
unsupported, check capabilities and update all three components before retrying.
