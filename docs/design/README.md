# Favorites design contracts

Source contracts for global Favorites, its recovery paths and provider
configuration. Each one states whether it is an activated route or a proposal.

- [Portable global favorites](global-favorites.md) — the CLI, controller protocol and local storage revision for global Favorites.
- [Global favorites items v1](favorites-global-items-v1.md) — the accepted items contract, with the schema and fixtures beside it.
- [Global Favorites fresh admission](global-favorites-admission.md) — the required `admission` field in `describe` and what it gates.
- [Global favorites recovery v1](global-favorites-recovery-v1.md) — the additive recovery proposal, its schema and synthetic fixtures.
- [Favorites recovery v1 — protocol and recovery contract](favorites-recovery-v1.md) — the recovery capability and the fenced receipt ledger format.
- [Favorites profile synchronization](favorites-sync-proposal.md) — the current design, wired in source with rollout pending.
- [Bound native provider configuration, version 1](native-provider-settings-v1.md) — the contract for native Edem and the 43 registered named operators.
