# Torrent catalog RPC v1 — implementation contract

Action `torrent_catalog`; every request has `op`
and `runtime`. No arbitrary storage, eval, shell, endpoint discovery, or URL
fetching in controller/CLI. Player uses only its proven owned-native service.

## Bounds and shared types

- Request envelope remains at most 16384 UTF-8 bytes. Result data at most 65536.
- Catalog and metadata snapshot maximum 8388608 bytes; strict UTF-8 JSON.
- Chunk raw bytes 1..6144; base64 canonical RFC4648 with padding, no whitespace.
- UUID means lowercase canonical UUIDv4. TOKEN is `[A-Za-z0-9_.:-]{1,128}`.
- ID is `[A-Za-z0-9_-]{1,64}`. SHA is 64 lowercase hexadecimal characters.
- Runtime/revision are TOKEN. Revision identifies exact stored catalog bytes
  within runtime, including empty storage. Every content change invalidates it.
  Missing storage is distinct from corrupt storage: corrupt/noncanonical input
  must fail invalid_document/storage_failed and may never be treated as empty.
- Source is 1..8192 UTF-8 bytes, no controls/whitespace, magnet:? or HTTP(S)
  torrent metadata URL (pathname ends .torrent, case-insensitive). HTTP(S)
  credentials forbidden. Player/native service validates public fetch policy;
  controller never resolves or fetches source. Original source is preserved.
- Labels name/title/category: 1..256 UTF-16 units, at most 1024 UTF-8 bytes,
  trimmed, no controls or unpaired surrogates.
- File name: 1..4096 UTF-16 units, at most 16384 UTF-8 bytes, no controls or
  unpaired surrogates. Final filename extension must be one of mp4, m4v, mkv,
  webm, mov, avi, ts, m2ts, mpg, mpeg, ogv (case-insensitive). Index 0..999999999 integer; length 0..9007199254740991 integer.
- Capabilities additive `torrent_catalog:{version:1}`. Advertise only when
  owned-native service is supported. No external endpoint can grant capability.

Snapshot descriptor, exact keys:

```json
{"id":"UUID","kind":"catalog","revision":"TOKEN","sha256":"SHA","size_bytes":123}
```

For metadata kind=`metadata`, revision=null. Catalog snapshot document is the
strict config below, or literal JSON null when no catalog exists. Metadata
snapshot document is `{schema:1,source:SOURCE,files:[FILE...]}`. Snapshot is
immutable. Export reads neither start networking nor change the selected
provider. Keep snapshots for 600s idle, extending on valid chunk access.

Catalog exact keys:
`{schema:1,id:ID,name:LABEL,entries:[{id:ID,title:LABEL,category:LABEL,source:SOURCE,files:[{index:INT,name:NAME,length:INT}]}]}`.
Limits 256 unique entry IDs, 4096 unique file indices per entry, 10000 files total;
zero entries/files allowed. Unknown keys rejected recursively, including
endpoints/stream URLs. Canonical snapshot bytes chosen by player; SHA authenticates
those exact bytes. Imports accept alternate JSON ordering but reject duplicate
keys and invalid numbers/encoding/depth (>8), normalize only key ordering, never
IDs/source/name content. Player enforces 8 MiB producer limit too. Torrent-specific
Favorites catalog reader must accept the same bounded catalog, not old 1 MiB cap.

Operation descriptor, exact keys:

```json
{"id":"UUID","kind":"metadata","state":"pending","next_offset":null,"snapshot":null,"reason":null}
```

Metadata states pending/ready/failed/cancelled. Ready has metadata snapshot.
Write states uploading/committed/failed/cancelled. Uploading next_offset is
integer 0..8388608, snapshot null; committed has catalog snapshot and next_offset
null. Other states have null offset/snapshot. Failed has reason from enum;
all other states reason=null. Retain at most 16 bounded operations, no eviction
of active operations to admit new work; aggregate staged/snapshot bytes<=32MiB.
Idle expiry 600s, extended by valid chunk/status activity; active metadata hard
deadline 75s including optional loader. Idempotency holds only while a record
is retained in this runtime. After expiry/reload, status returns expired or
operation_not_found; CLI never automatically resubmits a mutation. No unbounded
tombstone retention is required.
Cancel/source/runtime fencing must release native metadata resources. New
provider/source, standby, owner loss or reload cancels pending metadata; no
late callback may commit. Read/catalog operations never wake playback.

Play operations use kind=`play`, states pending/accepted/failed/cancelled,
null next_offset/snapshot, and one additional mandatory field
`target:{catalog_id:ID,entry_id:ID,file_index:INT}`. Accepted only means admitted
playback dispatch. Status can recover a lost play receipt. Cancel can prevent
pending admission, but accepted play stays accepted and is never stopped by
operation cancellation. UUID namespace is shared across all kinds; cross-kind
reuse is operation_conflict.

All successful responses have exact common keys `{version:1,op,runtime}` plus
the operation-specific keys below. All rejected/unsupported results have exact
`{version:1,op,runtime,error,reason}`; error is the exact fixed string
`Torrent catalog request failed.`. The reason carries the typed category; never
include source URLs, tokens, paths, payload excerpts or backend exceptions.

Reasons: invalid_request, unsupported, policy_restricted, runtime_changed,
source_changed, revision_conflict, snapshot_not_found, operation_not_found,
operation_conflict, busy, expired, cancelled, metadata_failed, metadata_timeout,
no_files, invalid_document, document_too_large, hash_mismatch, offset_mismatch,
incomplete, catalog_mismatch, entry_conflict, entry_not_found, file_not_found,
storage_failed, effect_failed.

## Requests and responses

1. `read {op,runtime}` -> common + `{snapshot:CATALOG_SNAPSHOT}`.
2. `read_chunk {op,runtime,snapshot_id:UUID,offset:INT,max_bytes:INT 1..6144}`
   -> common + `{snapshot_id,offset,data_b64,next_offset:INT_OR_NULL}`.
   Offset must be within snapshot; no empty successful chunk; next_offset is
   offset+decoded bytes, null exactly at end. CLI verifies full size/SHA.
3. `metadata {op,runtime,operation_id:UUID,source:SOURCE}`
   -> common + `{operation:METADATA_OPERATION}`. Returns promptly; metadata
   never adds to catalog or starts media playback. A repeated identical UUID
   returns existing operation; differing source with same UUID is conflict.
4. `status {op,runtime,operation_id:UUID}` -> common + `{operation:OPERATION}`.
5. `cancel {op,runtime,operation_id:UUID}` -> common + `{operation:OPERATION}`.
   Cancels only this operation and its resources; terminal state stays terminal.
6. `write_begin {op,runtime,operation_id:UUID,revision:TOKEN,mode:"merge"|"replace",size_bytes:INT 1..8388608,sha256:SHA}`
   -> common + `{operation:WRITE_OPERATION}`. Binds exact base revision and
   payload digest; no persistent change before commit. Existing UUID may only
   match identical original fields. No imported installation grants.
7. `write_chunk {op,runtime,operation_id:UUID,offset:INT,data_b64:BASE64}`
   -> common + `{operation:WRITE_OPERATION}`. Sequential offsets only. An exact
   duplicate previously accepted chunk returns current progress; conflicting
   bytes/offset rejected. No hidden commit.
8. `write_commit {op,runtime,operation_id:UUID}`
   -> common + `{operation:WRITE_OPERATION}`. Verify exact total bytes/SHA,
   strict catalog document and original runtime/revision/source binding again.
   Recheck policy immediately before commit. Headless catalog operations work
   without selecting Torrent, touching UI or adopting the active UI owner.
   `replace` adopts incoming catalog ID and exact entries. `merge` adopts ID for
   empty storage, otherwise requires identical catalog ID; retain destination
   order/name, append new IDs in incoming order, identical entries no-op,
   conflicting same ID rejects entire import. Store once with verified readback;
   failure cannot claim committed. Cache committed receipt; duplicate commit
   returns it without another write while retained. Do not switch provider,
   start or stop media. Save catalog and invalidate cache; update visible list
   on next explicit provider load/play, not immediate loadChannels/autoplay.
9. `play {op,runtime,operation_id:UUID,revision:TOKEN,entry_id:ID,file_index:INT}`
   -> common + `{operation_id,accepted:true,catalog_id:ID,entry_id:ID,file_index:INT}`.
   Only the exact existing catalog entry/file; enforce native owner and normal
   playback/kiosk/parental/standby restrictions. Use normal guarded provider
   selection/playback path. An identical retained UUID cannot start a second
   play; conflicting reuse rejected. Accepted is dispatch, not decoder confirmation.

Reads require normal remote auth. Every op has separate controller permission
`torrent_catalog.OP`; capabilities remains separately scoped. Writes/metadata/
play honor existing player policy. No op automatically enables remote access.

## CLI behavior and compatibility

`torrent list`, `files ENTRY`, `inspect SOURCE`, `add SOURCE --name TITLE
[--files 0,3,7]`, `add --metadata UUID --name TITLE [--files ...]`, `refresh ENTRY`,
`rename ENTRY TITLE`, `remove ENTRY`, `play ENTRY INDEX`, `status UUID`, `cancel
UUID`, `export FILE [--with-favorites]`, `import FILE [merge|replace] [--dry-run]
[--with-favorites]`. IDs exact; unique exact titles may resolve, ambiguous titles
must fail with IDs. Selection is zero-based native index, never ordinal row.

Add/refresh: fetch metadata once -> wait within user deadline -> read immutable
metadata -> produce typed catalog preserving existing catalog/entry IDs -> CAS
replace. Recheck no source/revision drift before commit. Default selects all
playable files; explicit selection must exist. Refresh keeps entry ID/title and
replaces saved file list; source itself remains unchanged. Rename changes title
and category coherently; remove deletes only selected catalog entry. None plays.

No automatic mutation replay. Show/persist operation UUID before submission;
timeout leaves an explicit uncertain/pending result with status/cancel command.
Never say add/import succeeded before committed readback. Pure list/files/JSON
status omit original source and transient URLs. Export is explicit private 0600
file with no overwrite and full validated catalog. Metadata output only files.

Default deadline follows CLI 45s. Torrent command accepts explicit `-t` up to 10800s
without broadening existing other-command 300s limit. Preflight large imports
against remaining budget before write_begin; do not silently truncate.

Bundle v1 exact `{version:1,kind:"ottplay-torrent-bundle",catalog:CATALOG,favorites:FAVORITES_DOC}`.
Favorites is unchanged portable global Favorites document. Export only Torrent
entries with this catalog ID. Validate each locator syntax and catalog ID;
preserve IDs. Well-formed references to removed entries/files remain unresolved,
never silently dropped or remapped. Preflight Favorites capability and document
before any catalog write. Import catalog first, then existing global Favorites merge/recovery.
These are separate commits: report partial success and operation identities if
Favorites fails. Do not claim cross-store atomicity. No receiver grants, private
bindings, temporary sessions or companionEndpoint in bundle. Plain catalog
export/import is default; Favorites inclusion explicit.
