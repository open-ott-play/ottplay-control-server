# Controller, CLI and API

The command route the server exposes, the terminal client that drives it and the
protocols built on top of them.

- [Command API](../api.md) — the authenticated JSON routes, their rejection rules and error handling.
- [Control players from the terminal](../cli.md) — the standard-library CPython client and the files that must stay together.
- [Discover OTT-play controllers in the local network](../discovery.md) — the `_ottplay-ctrl._tcp` service type and search domains.
- [Durable device registration](../device-registry.md) — approving an unknown player, adding or revoking a device and rotating credentials.
- [Hosted device transport](../device-transport.md) — carrying an existing device request inside a JSON POST for hosted bridges.
- [Reliable control and playback handoff](../reliable-control.md) — scoped integration credentials, durable command outcomes and explicit handoff.
- [Home Assistant events on active players](../home-assistant.md) — the private long-lived token, filtered rules and automation YAML.
- [Home Assistant events (Russian)](../home-assistant.ru.md) — the Russian edition of the same guide.
- [One notification preference per player](../notification-preferences.md) — the single `notifications` switch covering banners, camera images and clips.
- [Request correlation and authenticated initiators](../request-audit.md) — the controller-only audit feature and what it requires.
- [Remote diagnostics protocol 2](../remote-diagnostics.md) — the scoped operator protocol and the commands that use it.
- [Diagnostic operator CLI and MCP](../diagnostics-cli.md) — `cli/diagnostics.py` as a standard-library protocol 2 client.
- [Read-only diagnostic workbench](../workbench.md) — the workbench extending an existing remote-control connection.
- [Security design and verification](../security-design.md) — how playback reports, tokens and connections are authorized.

## Translated editions

- [Reliable control and playback handoff — all editions](../translations/reliable-control/README.md) — the 24 translated copies of the reliable-control guide.
