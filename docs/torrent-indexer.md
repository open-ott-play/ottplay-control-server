# Local Torrent indexers

`ott PLAYER torrent indexer` manages a locally operated Torznab service,
including a Torznab endpoint supplied by Jackett or Prowlarr. The player's owned
native companion performs discovery and metadata preparation. Stremio remains
an optional, separate discovery route.

Install all CLI modules from one revision, update the controller and player,
then check `ott t1 caps` for `torrent_indexer: {"version": 1}`. This requires
the owned native companion; a browser, TV or external companion URL does not
provide the capability by itself. Help works without credentials or a connection.

Scoped controller operators need `capabilities` and the appropriate
`torrent_indexer.OP` permission: `list`, `configure`, `remove`, `caps`, `search`
or `prepare`; child discovery additionally uses `indexers`. Search also reads
`caps`, so grant both operations. `doctor` uses existing capability and caps
permissions, plus `search` only when `--query` is supplied. Catalog
insertion and playback require their existing separate permissions. Source
configuration does not grant a general network proxy or access to another player.

```sh
ott t1 torrent indexer --help
ott t1 torrent indexer list
ott t1,t2 torrent indexer list
```

## Configure a source

The operator chooses the indexer and its Torznab API endpoint. The endpoint must
resolve to loopback or private IPs as seen by the companion, not by the terminal
running `ott`. For Kubernetes, expose the selected service through a
loopback-only port forward on the companion's machine and keep it running.
A working tunnel does not select or configure an indexer automatically.

Save the API key in an owner-readable local file, or use an environment variable
provided by your secret manager. Do not put the key directly in command arguments.
`TORZNAB_ENDPOINT` below is the endpoint supplied by the operator, without an
API-key query string.

```sh
ott t1 torrent indexer configure home --label 'Home indexer' \
  --endpoint "$TORZNAB_ENDPOINT" \
  --api-key-file ~/.config/ottplay-control/secrets/home-indexer.key

# Alternative: read the value of an existing environment variable.
ott t1 torrent indexer configure home --label 'Home indexer' \
  --endpoint "$TORZNAB_ENDPOINT" --api-key-env TORZNAB_API_KEY

ott t1 torrent indexer list
ott t1 torrent indexer caps home
```

Choose exactly one key input. An empty key is supported for a deliberately
unauthenticated local service. Source IDs start with a lowercase ASCII letter,
followed by lowercase letters, digits or hyphens, with at most 64 characters.
At most eight sources are supported. Endpoints and keys are write-only:
configuration acknowledgements and source lists expose only IDs and labels.

The authenticated controller transports configuration to the player. Endpoints
and keys are excluded from operation history, diagnostic reports and exported
profiles. The companion keeps private configuration outside the video piece
cache. The local companion contacts Torznab directly and handles media through
the native playback path; hosted player pages and public OTTserver instances
are not HTTP/download relays.

## Search, prepare and play

Start with a read-only diagnosis when a source is slow:

```sh
ott t1 torrent indexer doctor home
ott t1 torrent indexer diagnose home
ott -j t1 torrent indexer doctor home

# Opt in to exactly one search, with a small result page.
ott -t 45 t1 torrent indexer doctor home --query 'Big Buck Bunny'
```

`doctor` and its alias `diagnose` check player capabilities and the source's
Torznab capabilities. Without `--query`, they do not search any trackers and
explicitly report that search was not tested. With `--query`, the diagnosis
also runs one search with a limit of five, or the source's smaller advertised
maximum. Capabilities may come from the companion's cache, so a successful
check is not proof of a fresh connection or of every indexer's health.
Neither mode configures a
source, prepares a torrent, changes provider, or starts playback.

Indexer commands report their current stage and elapsed time to stderr, with
periodic updates while waiting. JSON results remain on stdout, so scripts can
capture them separately. Diagnostics do not print the query, endpoint, API key,
or an upstream error body.

There are separate time limits. `ott -t` bounds the entire CLI operation,
including player discovery, capability checks and search. It does not extend
the native companion's internal request deadline. The current companion uses
a 15-second Torznab deadline; a larger CLI timeout cannot make that request wait
longer. A confirmed `torznab_timeout` means the companion reached its deadline.
A controller transport failure or missing receipt does not establish that the
source or player is offline.

Jackett's aggregate `all` feed can wait for slow members before returning results
from faster members. The reviewed upstream implementation waits for completion
or a 40-second limit; this is an implementation detail, not a guarantee for every
installed Jackett version. A successful capabilities check therefore does not
prove that aggregate search fits the companion's deadline. See the
[upstream aggregate implementation](https://github.com/Jackett/Jackett/blob/38532121754915ac78659968049cb77ce08ea26e/src/Jackett.Common/Indexers/Meta/BaseMetaIndexer.cs#L74).

Do not repeatedly reconfigure the saved source to diagnose a slow member:
changing its endpoint or API key invalidates its existing prepared references.
Use the error's stage and bounded diagnosis before another explicit search.

### Select a configured Jackett indexer

Child discovery and selection require an updated controller and a player with
an implemented companion extension. They are gated by a separate capability:

```json
{"torrent_indexer_children":{"version":1,"operations":["indexers"],"scopedOperations":["caps","search"]}}
```

The original `torrent_indexer: {"version": 1}` marker alone does not enable
these commands. Installing this CLI does not add the companion extension. If
the marker is missing, the CLI reports the required update before sending any
child-indexer request. Existing source commands and `doctor` remain usable.

```sh
ott t1 torrent indexer indexers jackett
ott t1 torrent indexer caps jackett --indexer internetarchive
ott t1 torrent indexer doctor jackett --indexer internetarchive
ott -t 45 t1 torrent indexer search jackett 'Big Buck Bunny' --indexer internetarchive --limit 5
```

Use an ID returned by `indexers`; `internetarchive` is only an example and must
be configured in your Jackett instance. IDs are case-sensitive and may start
with a digit. Selection applies to one request. The saved `jackett` source,
its feed URL, API key, generation and prepared references remain unchanged.
Search validates the selected child's capabilities, then searches that same
child. It does not fan out to other members or fall back to the aggregate feed.
Use the returned result reference with the usual `prepare jackett RESULT_REF`.

Discovery uses Jackett's authenticated `t=indexers&configured=true` operation
on a recognized `all` feed; it does not run tracker searches. Configured does
not mean healthy. Generic Torznab services and filtered aggregate feeds may
report that child discovery is unsupported; this is distinct from a supported
source with zero configured children. Authentication, network and malformed
response failures remain errors. See the
[official Jackett API](https://github.com/Jackett/Jackett#aggregate-indexers).

Our contract limits discovery to 256 unique IDs, 128 ASCII bytes per ID, 128
UTF-8 bytes per label and 32 UTF-8 bytes each for optional language/type.
These are OTT bounds, not Jackett's declared limits. Oversized or duplicate
entries reject the response; no silent truncation or partial list is returned.
IDs must match `[A-Za-z0-9][A-Za-z0-9_-]{0,127}` and cannot be `all` in any case.
URLs and filter expressions are not accepted as child IDs.

### Search and preparation

Read `caps` to discover search availability, category IDs and the page limit.
Pages are explicit: there is no automatic pagination. The default requested page
size is 50; the protocol allows limits 1..100 and offsets 0..1,000,000. Choose a
limit supported by the source. `CATEGORY_ID` must come from its capabilities.

```sh
ott t1 torrent indexer search home 'Big Buck Bunny' --limit 50 --offset 0
ott t1 torrent indexer search home 'Big Buck Bunny' --category "$CATEGORY_ID"
ott -j t1 torrent indexer search home 'Big Buck Bunny' --limit 25
ott t1 torrent indexer prepare home RESULT_REF
```

Replace `RESULT_REF` with the exact opaque search handle. Results contain titles
and available metadata, not download links. Unknown metadata stays unknown.
Request the returned `nextOffset` explicitly for another page; null means there
is no indicated next page.

Preparation resolves metadata into a durable companion-local reference. It
prints `ottplay-local-torrent:home/SOURCE_REF` and does not start playback. Use
the exact returned source with the existing catalog:

```sh
ott -t 120 t1 torrent add 'ottplay-local-torrent:home/SOURCE_REF' --name 'Big Buck Bunny'
ott t1 torrent list
ott t1 torrent files ENTRY
ott t1 torrent play ENTRY FILE_INDEX
```

Replace uppercase placeholders with returned values. Successful preparation or
catalog insertion does not prove codec support or playback. The companion and
player own media transfer; the controller and CLI do not proxy video.

## Reference lifetime and removal

Search handles last up to 15 minutes, with at most 512 retained. Search again if
a handle expires. Prepared references survive companion restarts, with limits
of 128 references and 64 MiB of metadata. There is no silent durable eviction.

Changing a source's endpoint or key, or removing it, invalidates its references.
A label-only update preserves them. References belong to one companion: copying
a catalog or profile does not copy credentials, metadata or the reference store.
Configure and prepare sources on the destination when needed.

```sh
ott t1 torrent indexer remove home
```

Group commands run independently per target, without cross-player transactions
or replaying successful targets after another player fails.

Each invocation shares one deadline across capability discovery and subsequent
requests: 45 seconds by default, up to 300 with global `-t`. Search validates the
source's capabilities before submitting its query. Neither timeout nor a lost
response causes an automatic repeat of a configuration, removal or preparation.

Use global `--receipt` to print safe request IDs to stderr. For recovery, choose
the receipt whose action is `torrent_indexer`, then inspect it without replaying
the request:

```sh
ott --receipt t1 torrent indexer prepare home RESULT_REF
ott t1 operation REQUEST_ID
```

## Troubleshooting

- **Unsupported:** update CLI, controller and native player; check the capability
  and owned companion. Remote control alone does not add native Torrent support.
- **Local-only or network error:** check the endpoint from the companion machine,
  loopback forward, DNS and service. Public endpoints, credentials in URLs and
  ordinary cross-origin redirects are refused.
- **Search unavailable or invalid category:** read `caps` and select advertised
  capabilities. There is no fallback to a different indexer.
- **HTTP/API/XML error:** inspect the local indexer service. Reports contain safe
  reason codes rather than response bodies, URLs or keys.
- **Reference missing or source changed:** search and prepare using the current
  configuration. A copied URI does not create its backing reference elsewhere.
- **Timeout or unconfirmed change:** mutations are never automatically retried.
  Inspect current sources and the retained controller operation before another
  change; a label alone cannot verify which write-only key is stored.
- **Store limit:** inspect retained companion metadata. Do not delete private
  state as a cache cleanup: that loses durable references.

See [catalog commands and recovery](torrent-catalog.md),
[diagnostic reports](provider-diagnostics.md), and
[controller installation](deployment.md).

The wire action is `torrent_indexer`, with version 1, the negotiated player
runtime and an operation name on every request. The shared
[request/response fixtures](../contracts/torrent-indexer-v1.json) define public
DTO examples and rejection cases. Companion-local references contain no endpoint,
API key or download URL.

The additive [child-indexer fixtures](../contracts/torrent-indexer-children-v1.json)
define discovery and request-scoped selection. They preserve the original
request and error shapes whenever selection is omitted. The companion keeps
membership and capability caches bound to the source generation and child;
the controller validates the selected ID on successful replies.
