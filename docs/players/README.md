# Player features

Commands that depend on a matching native player advertising the capability,
and the diagnostics that go with them.

- [Typed player settings and VPortal presets](../player-settings.md) — publishing a settings schema and saving through the same store as the player.
- [Current playback and retained player reports](../player-state.md) — automatic current-media reports and the optional retained history.
- [Current-playback audio and subtitles](../media-tracks.md) — listing and selecting tracks for the current item only.
- [Multi-view control](../multiview.md) — the backend-neutral version 1 control contract and the operations a player advertises.
- [Native catalog selection and subtitle preferences](../native-catalog.md) — the native catalog commands and their capability discovery.
- [Native playback diagnostics](../native-playback-diagnostics.md) — the bounded snapshot for a native player stuck on a loading screen.
- [Provider and companion diagnostics](../provider-diagnostics.md) — what to collect when a list opens but a movie reports an unsupported source.
- [Managed Plex server selection](../managed-plex-servers.md) — exposing and temporarily activating one retained managed server.
- [Browse USB folders on a native player](../local-browser.md) — the additive protocol that needs matching CLI, controller and player support.
- [Local native EPG fallback](../local-epg.md) — the separate native EPG process used when the public guide is unavailable.
- [Native Mac acceptance through the CLI](../native-acceptance.md) — the runbook that exercises a qualified macOS player through the real CLI.
- [Temporary performance capture](../performance-capture.md) — the off-by-default bounded capture for playback freezes and notification load.

## Torrent

- [Torrent catalog control](../torrent-catalog.md) — installing the CLI directory and using a player advertising `torrent_catalog` v1.
- [Torrent catalog RPC v1](../torrent-catalog-rpc.md) — the `torrent_catalog` action contract and its `op`/`runtime` shape.
- [Local Torrent indexers](../torrent-indexer.md) — managing a locally operated Torznab service such as Jackett or Prowlarr.
