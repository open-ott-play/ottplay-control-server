# Torrent catalog control

Install the complete CLI directory, update the controller, and use a player
advertising `torrent_catalog: {"version": 1}` in `ott PLAYER caps`. This requires
the player's owned native Torrent service. An external companion URL does not
grant the capability; ordinary browser/TV builds may not support it.

The controller relays typed requests. Catalog storage, metadata resolution and
media traffic belong to the player/native service. Catalog operations work
without selecting Torrent and do not switch or stop current playback. Only
`torrent play` requests playback.

This API manages manually saved sources. Movie discovery and its private
metadata/stream-addon preferences are separate; configure them through
[typed settings](player-settings.md#torrent-metadata-and-stream-addons).
Search results are not included in `torrent list`, import or export.

## Commands

Replace the example source with your metadata URL or quoted magnet link.
`ENTRY` is an ID from `list`, or a unique exact title. Ambiguous titles fail and
list matching IDs. File numbers are native indices from `files`/`inspect`, not
row numbers in a filtered listing.

```sh
ott t1 torrent                          # same as list
ott t1 torrent list
ott t1 torrent files ENTRY
ott -t 120 t1 torrent inspect 'https://media.example/collection.torrent'
ott -t 120 t1 torrent add 'https://media.example/collection.torrent' --name 'Collection'
ott -t 120 t1 torrent add 'https://media.example/collection.torrent' --name 'Selected' --files 0,3
ott t1 torrent play ENTRY 3
ott -t 120 t1 torrent refresh ENTRY
ott t1 torrent rename ENTRY 'New title'
ott t1 torrent remove ENTRY
```

`add` defaults to all playable files. `refresh` keeps the entry ID, source and
title, replacing its file list with all playable files. `rename` changes both
title and category. Removing an entry does not stop playback. Playback
acceptance confirms dispatch, not decoding; inspect ordinary `status` or
`state` afterwards.

Allowed final filename extensions are `mp4`, `m4v`, `mkv`, `webm`, `mov`, `avi`,
`ts`, `m2ts`, `mpg`, `mpeg`, and `ogv`, case-insensitively. An eligible container
does not guarantee that the device supports every audio/video codec inside it.

## Timeouts and recovery

Each metadata request or mutation prints and privately saves its operation UUID
before submission. If the outcome is pending or uncertain, inspect that same
operation instead of repeating the command.

```sh
ott t1 torrent status OPERATION_UUID
ott t1 torrent cancel OPERATION_UUID
ott -t 120 t1 torrent add --metadata OPERATION_UUID --name 'Collection' --files 0,3
```

The last command reuses ready metadata from `inspect`. Cancel affects only that
operation; cancelling an already accepted play does not stop playback. After
expiry/restart, an unknown operation cannot prove the earlier outcome. Read the
current catalog before deciding on a new change.

The default deadline is 45 seconds. Torrent alone accepts explicit `-t` up to
10800 seconds; metadata itself has a 75-second player deadline. Large catalogs
transfer in bounded chunks at a conservative rate. Initial read, upload and
verified readback of catalogs near 8 MiB can take hours; use `-t 10800` for these
exceptional transfers. A budget preflight can reject a write before upload.

Private `.torrent-operations-*` directories beside the CLI configuration store
UUIDs, runtime and request digests, without source URLs. Keep these until the
outcome is resolved. Player operations/snapshots expire after 600 seconds idle;
valid chunk/status activity extends retention. The CLI never automatically
replays a mutation after a lost response.

## Export, import and Favorites

```sh
ott t1 torrent export ./catalog.json
ott -t 300 t2 torrent import ./catalog.json merge --dry-run
ott -t 300 t2 torrent import ./catalog.json merge
ott -t 300 t2 torrent import ./catalog.json replace
ott t1 torrent export ./collection.json --with-favorites
ott -t 300 t2 torrent import ./collection.json merge --with-favorites --dry-run
ott -t 300 t2 torrent import ./collection.json merge --with-favorites
```

Export creates a new owner-only file, refusing existing files/symlinks. It
contains original source URLs: treat it as private. Ordinary list/status output
omits sources. Imports preserve catalog/entry IDs and source/filename content.
Installation grants, endpoints and temporary stream URLs are never transferred.

`merge` is the default. An empty destination adopts the ID; a populated one
requires the same catalog ID. Existing order/name remain, new entries append,
identical entries are unchanged, and conflicting entries with the same ID reject
the whole merge. `replace` adopts the supplied catalog. Concurrent changes fail
the revision check rather than being overwritten.

`--with-favorites` uses a versioned bundle with ordinary global Favorites for
that catalog. Well-formed references to removed entries/files remain unresolved
with their IDs intact. Import preflights Favorites support, then commits catalog
and Favorites separately. A partial result requires inspection of both stores
and the reported operation IDs using the existing `fav global` recovery
workflow. These commits are not atomic. Dry-run checks the proposed merge, not
media availability or decoding.

## Groups

```sh
ott tt torrent list
ott t1,t2 torrent export ./catalog.json
ott -t 300 tt torrent import ./catalog.json replace
```

Export creates `catalog.t1.json` and `catalog.t2.json`. Each target keeps its own
result, deadline and operation IDs. Group execution is independent, not a
cross-player transaction; failures do not replay successful targets.

## Troubleshooting

- **Unsupported:** check `caps`, controller revision and owned native service.
- **Metadata timeout/no files:** inspect the source/native service. Reuse ready
  metadata where available; do not assume a catalog change occurred.
- **Revision/runtime/source changed:** read current state before a new operation.
- **Unknown/pending:** inspect the saved UUID within retention; no automatic replay.
- **Catalog mismatch/entry conflict:** review exported IDs and choose deliberate
  merge or replacement. Do not silently regenerate IDs.
- **Document too large:** limits are 8 MiB, 256 entries, 4096 files per entry and
  10000 files total. Split explicitly; imports never silently truncate.
- **Deadline insufficient:** allow time for upload and complete verified readback.
- **Partial Favorites result:** inspect both stores and retained operation;
  catalog success is not automatically rolled back.

See the [wire contract](torrent-catalog-rpc.md) and shared examples in
[`contracts/torrent-catalog-v1.json`](../contracts/torrent-catalog-v1.json).
