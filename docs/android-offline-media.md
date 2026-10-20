# Offline media on Android tablets

Status: code investigation on 2026-10-19, not a completed offline playback test.

## Result

Local playback without Wi-Fi or Internet is technically possible. The current Android OTT build does not provide a complete offline library, download manager, or Plex offline cache. k8a is an Amazon KFGIWI on Android 5.1.1 / API 22. fire8 still needs device-specific runtime validation.

## Proposed implementation

1. Import H.264/AAC MP4 files into application-owned storage (or explicitly grant access to a selected directory).
2. Start an Android media service bound only to `127.0.0.1` before loading OTT's playlist.
3. Serve a local `offline.m3u` plus the media files. Implement HTTP Range, correct MIME types, and the CORS behavior required by the local WebView.
4. Configure an OTT M3U profile pointing to the loopback playlist. Persist it independently of the shared Home preset so a preset reload cannot silently remove the offline setup.
5. Integrate service startup and lifecycle with the player. Screen-off must stop playback and file reads; restart must restore the selected profile and kiosk item.

The playlist and every media URL must resolve locally. A playlist on NAS, Plex, or the Internet is not an offline setup. The loopback HTTP service does not require a network connection.

## Evidence in the player source

Paths below are relative to the separate `ottplay-foss` repository inspected for this investigation.

- `capacitor.config.ts`: the APK includes `dist-mobile` UI assets.
- `src/provider/m3u-driver.ts`: profile settings persist, but playlist contents are fetched again at startup.
- `src/provider/m3u-settings.ts`: local playlist selection depends on `host.readFile`; an Android implementation was not found.
- `android/app/src/main/AndroidManifest.xml`: no shared media read permissions were declared in the inspected build.
- `M3UProxyPlugin.kt`: playlist retrieval expects an HTTP connection; passing a `file://` playlist is not a supported substitute.
- `DashExoPlayerPlugin.kt`: native playback accepts a URI, but this does not establish end-to-end file or content URI support in every OTT playback engine.
- `src/plugins/plex.ts`: no offline download/cache feature was found; Plex's server-side file path is not a playable local URL.
- `MobileCommandQueuePlugin.kt`: the existing loopback endpoint serves commands, not a media library.

## Required acceptance tests

On both k8a and fire8, test with Wi-Fi disabled and no USB network forwarding:

- Cold boot and start the local playlist without contacting external services.
- Play, seek, select the next file, and replay the last file.
- Restore the selected kiosk file after player/service restart.
- Sleep and resume; verify playback/file reads stop while asleep.
- Confirm operation after shared Home profile reload and after an OTA update.
- Verify storage capacity, supported codecs, and smooth playback on each tablet.

These tests have not been performed. No offline media files or local media server were installed as part of the investigation.
