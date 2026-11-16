# Request correlation and authenticated initiators

This feature is a source change requiring an updated controller and CLI. It is
not included in the currently deployed Control beta.6. The optional native
activity-v2 producer is a separate follow-up; Native 0.4.30 does not advertise it.
The existing player activity-v1 status and command wire envelopes stay unchanged.

## Read the controller's retained metadata

```sh
ott l_ audit
ott -j l_ audit
ott l_ audit 0123456789abcdef0123456789abcdef
ott -j t1,t2 audit
```

Plain `audit` reads the controller only. It never sends a player command,
capability query, media probe, wake, restart or capture request. It lists retained
request IDs, exact requested action/operation, authenticated credential role,
admission/deadline times and transport outcome. A request-ID lookup is exact;
an absent entry is `not_retained`, not evidence that the request never existed.
Admission/deadline timestamps are wall-clock observations; elapsed age is a
separate bounded duration. Clock adjustments and millisecond rounding can make
their arithmetic differ. Entries are ordered by admission, not wall-clock time.

The controller records only admitted RPCs in these action families: `play`,
`play_catalog`, `playback`, `restart`, `provider`, `profile`, `input`, `command`,
`local_media`, `plex_queue`, `plex_library`, `lifecycle` and `kiosk`. Families may
include reads: `plex_library.list` does not request playback, while
`plex_library.play` does. The journal does not reinterpret either as an executed
playback effect. Legacy command transports and other APIs are outside this view.

Routine status/capability/inspection and audit reads do not occupy this history,
so background status polling cannot evict the playback request being diagnosed.
The history is bounded to 32 entries per device, 512 total and 10 minutes, newest
admission first. Restarting the controller resets its epoch and the entire
volatile history. Capacity eviction, retention expiry, excluded action families,
older software and lack of permission can all leave a request unavailable.
Entries older than the retention window are omitted from reads; bounded in-memory
records are physically pruned on the next tracked admission or process exit.
This is not a promise of timed secure erasure.

## Join a player's explicit activity view

```sh
ott l_ audit --activity
ott -j l_ audit --activity
```

`--activity` explicitly adds fresh player capabilities and one passive
`command_activity_diagnostics` version 2 request when advertised, followed by one
controller audit read within the same timeout. It requires the matching updated
controller and native producer. It does not fall back to a playback command or
retry a timed-out read. An older player remains unsupported; installing a CLI
alone does not add its capability.

The join uses an exact canonical request ID from the validated current runtime,
the same configured controller/device and a matching action family. Sequence
numbers and ages are informational; they are never correlation identities. A
native entry with `request_id: null` is not correlated by title, timing, action
similarity or sequence. Ring loss or an unmatched action stays unknown.
An explicit missing-ID lookup, unsupported activity view, empty activity ring or
incomplete correlation returns exit code3. Valid controller/player observations
remain in JSON even when the overall correlation is unknown. Plain unfiltered
audit can return an observed empty bounded view without claiming complete history.

```mermaid
sequenceDiagram
    participant Caller
    participant Controller
    participant Player
    participant Audit as Audit CLI
    Caller->>Controller: Authenticated RPC
    Controller->>Controller: Bounded metadata + generated request ID
    Controller->>Player: Existing request envelope with ID
    Player->>Player: Versioned activity retains canonical ID
    Audit->>Player: Explicit passive activity-v2 read
    Audit->>Controller: Read retained request metadata
    Audit->>Audit: Exact ID join; missing evidence stays unknown
```

## What attribution establishes

- `admin` establishes use of the administrator credential. If a person, HA,
  scripts and chats share it, their requests are indistinguishable.
- `operator` identifies a configured command-operator credential; its configured
  identifier is reported as `operator_id`. The identifier is a public label,
  not a token. Separate operator credentials can distinguish authenticated
  principals, subject to their configured device/action scopes.
- A principal label does not establish which human or application actually used
  that credential. Caller-provided names, User-Agent strings or headers are not
  promoted into verified sender identity.

Administrator reads can see retained entries for the selected device. An
operator sees only entries admitted with its own credential that remain within
its current device and action authorization. Request IDs are not authorization:
knowing an ID never grants access to another operator's history. Device
credentials cannot read the controller audit API.

The raw native activity ring belongs to the current control authority, not an
individual command operator. The controller narrows an operator-origin v2
response before retaining it and again on receipt reads. It keeps only canonical
IDs that still map to that operator's retained, same-device request and currently
permitted exact action, with a matching action family. Null, foreign, stale and
unmapped IDs are omitted. Reads never widen a previously filtered response.
Administrator-origin responses retain the bounded device-wide view. A filtered
or empty operator view is partial evidence, not proof that no other command ran.

The view exports no command parameters, media titles, URLs, source paths,
authorization headers, tokens, credential hashes, idempotency keys, IP addresses
or session payloads. It stores transport metadata only. `responded/ok` means a
player response was received; it does not prove playback or a visible picture.
`expired` means the response deadline elapsed without a retained response; it
does not prove the request was never executed. Do not replay uncertain changes.

## Troubleshooting and limits

An unsupported controller needs a source update through the normal reviewed
deployment process. This change does not itself authorize any live update.
`--activity` additionally needs the separately advertised native v2 producer.
The CLI reports malformed, oversized, runtime-mismatched or unavailable evidence
without echoing raw server/player errors or silently substituting another
transport.

For a new incident, preserve the request ID or explicit activity-v2 report before
retention expires. For an incident from before this feature was installed,
activity-v1 sequence 1 alone cannot reconstruct a historical controller request
or tell whether a person, HA or another chat initiated it. An existing retained
CLI `--receipt` line can supply an exact request ID, but it does not identify an
application behind a shared credential.

The [controller audit contract](../contracts/request-audit-v1.json) and
[native activity-v2 contract](../contracts/command-activity-v2.json) contain
synthetic fixtures and closed field/value bounds. Neither is a durable audit log
or a replacement for runtime ownership coordination.
