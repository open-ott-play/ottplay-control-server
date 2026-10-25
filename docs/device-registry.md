# Durable device registration

The optional device registry lets an administrator approve an unknown player,
add or revoke a device, and rotate credentials without rewriting the server's
static configuration. A new player's pairing request grants no access. Approval
must persist the registration before a device credential becomes usable.

Static deployments keep their existing behavior when device_registry is absent.
The registry API is administrator-only and is excluded from hosted device
transport. A device token, pairing secret or scoped integration token cannot
provision a device or read its credential.

## Initialize once

Add this block to the private controller configuration:

```json
{
  "device_registry": {
    "path": "/var/lib/ottplay-control/private/devices.json"
  }
}
```

Run the initializer once, using the service account that owns the private store:

```sh
ottplay-control-server registry-init --config /path/to/config.json
ottplay-control-server validate --config /path/to/config.json
ottplay-control-server serve --config /path/to/config.json
```

The initializer refuses to overwrite existing state. Serving refuses a missing,
invalid, insecure or already-locked store. It never creates a fresh registry when
a persistent volume disappears. The store directory must be private (0700) and
the file and lock 0600 on Unix; restrict the equivalent account ACL on Windows.
The file contains device credentials and must be backed up privately.

Use one controller process and one writer. In the Helm chart, the existing
journalExistingClaim mount can also hold the registry under /var/lib/ottplay-control.
Provision the PVC separately and run initialization as an explicit maintenance
step with the same UID and mount. Do not initialize on every startup. The mounted
configuration Secret stays read-only and is never rewritten by the server.
The chart retains one replica and Recreate deployment strategy.

A registry-enabled configuration may have an empty static devices list. On
startup, the server merges static identities, durable registrations and
tombstones before validating Home Assistant and operator device scopes. Revoked
identities remain known for scope validation but cannot authenticate or receive
commands. A scope naming a completely unknown device still fails validation.

## First Kubernetes rollout

The nonsecret examples [PVC](../deploy/device-registry-pvc.yaml.in) and
[bootstrap Job](../deploy/device-registry-bootstrap-job.yaml.in) use a separate
1 GiB ReadWriteOnce `local-path` claim named `ottplay-control-registry`. They are
templates, not manifests to apply unchanged. This path assumes the deployment
has no existing journal claim. If one is already mounted, preserve that claim
and its journal or plan an explicit private data migration before changing
`journalExistingClaim`; a fresh claim must not silently replace existing state.

Verify the published release image and digest first. Stage a private configuration
Secret containing the existing configuration plus the registry path above.
Use a new Secret name for initial rollout so the old running image is not given
registry configuration before it is upgraded. No Secret contents belong in Git.
Set these values to the verified release and intended cluster objects:

```sh
export OTT_NAMESPACE='YOUR_NAMESPACE'
export OTT_RELEASE='YOUR_EXISTING_HELM_RELEASE'
export OTT_CONFIG_SECRET='YOUR_STAGED_PRIVATE_CONFIG_SECRET'
export OTT_IMAGE_REPOSITORY='ghcr.io/open-ott-play/ottplay-control-server'
export OTT_IMAGE_TAG='EXACT_PUBLISHED_RELEASE_TAG'
export OTT_IMAGE_DIGEST='sha256:VERIFIED_64_HEX_DIGEST'
export OTT_VERIFIED_IMAGE="${OTT_IMAGE_REPOSITORY}:${OTT_IMAGE_TAG}@${OTT_IMAGE_DIGEST}"
```

Copy the live Deployment's `spec.template.spec.nodeSelector`, `affinity` and
`tolerations` as JSON into `OTT_NODE_SELECTOR_JSON`, `OTT_AFFINITY_JSON` and
`OTT_TOLERATIONS_JSON`. Use `{}`, `{}` and `[]` only for fields that are actually
absent. Preserve hostname/architecture restrictions and excluded nodes. The
bootstrap Job must use the same scheduling constraints as the server:
`local-path` binds storage to its first consumer's node, and initializing on an
ineligible node can leave the server permanently unable to mount that claim.
Do not relax the workload's placement to work around an incorrectly bound PVC.

Render with `envsubst` (GNU gettext) into an operator-owned directory. Replace
every placeholder and inspect the namespace, image, Secret and scheduling fields
before applying. Do not infer a tag or digest from the example:

```sh
: "${OTT_NODE_SELECTOR_JSON:?Copy the live nodeSelector as JSON}"
: "${OTT_AFFINITY_JSON:?Copy the live affinity as JSON}"
: "${OTT_TOLERATIONS_JSON:?Copy the live tolerations as JSON}"
OTT_ROLLOUT_DIR="$(mktemp -d)"
envsubst '${OTT_NAMESPACE}' < deploy/device-registry-pvc.yaml.in > "$OTT_ROLLOUT_DIR/pvc.yaml"
envsubst '${OTT_NAMESPACE} ${OTT_CONFIG_SECRET} ${OTT_VERIFIED_IMAGE} ${OTT_NODE_SELECTOR_JSON} ${OTT_AFFINITY_JSON} ${OTT_TOLERATIONS_JSON}' \
  < deploy/device-registry-bootstrap-job.yaml.in > "$OTT_ROLLOUT_DIR/init.yaml"
kubectl apply -f "$OTT_ROLLOUT_DIR/pvc.yaml"
kubectl apply -f "$OTT_ROLLOUT_DIR/init.yaml"
kubectl -n "$OTT_NAMESPACE" wait --for=condition=complete \
  job/ottplay-control-registry-init --timeout=5m
kubectl -n "$OTT_NAMESPACE" logs job/ottplay-control-registry-init -c initialize
kubectl -n "$OTT_NAMESPACE" logs job/ottplay-control-registry-init -c validate
```

The Job runs `registry-init` in its init container, followed by `validate` in the
main container, with the same verified image, read-only Secret and writable PVC.
Both run as UID/GID 65532 with fsGroup 65532, a read-only root filesystem, dropped
capabilities and no service-account token. `backoffLimit: 0` prevents blind retry.
The initializer creates its private 0700 directory and 0600 store; do not loosen
these modes to work around a storage error. `local-path` may wait for the Job's
first consumer before binding the claim. An existing store is an initialization
failure, not permission to remove it or recreate the volume.

After Job completion, upgrade the existing release with explicit image identity
and the same Secret/claim. `--reuse-values` preserves unrelated settings:

```sh
helm upgrade "$OTT_RELEASE" charts/ottplay-control-server \
  --namespace "$OTT_NAMESPACE" --reuse-values \
  --set-string image.repository="$OTT_IMAGE_REPOSITORY" \
  --set-string image.tag="$OTT_IMAGE_TAG" \
  --set-string image.digest="$OTT_IMAGE_DIGEST" \
  --set-string existingSecret="$OTT_CONFIG_SECRET" \
  --set-string journalExistingClaim=ottplay-control-registry \
  --wait --timeout 5m
kubectl -n "$OTT_NAMESPACE" rollout status deployment/"$OTT_RELEASE" --timeout=5m
ott -j registry list
```

Confirm the live pod's image digest and both mounts, `/readyz` success, and the
expected active/static identity inventory. Record `store_id`, then perform one
planned deployment restart and verify the same `store_id`, generations and
revoked states through `ott -j registry list` after readiness returns. Do not
rerun initialization on pod restart. Keep the PVC and private configuration in
the backup plan, including the [rollback safeguards](#rollback-after-enabling-the-registry).
The completed bootstrap Job may be removed after retaining its nonsecret logs;
do not delete the PVC as Job cleanup.

## CLI workflow

Install all `cli/*.py` files from the same release, including
`device_registry.py`. Keep the private `cli.json` and administrator
`server_config`. Registry operations never rewrite `server_config` or a
Kubernetes Secret. Restricted operator credentials cannot register devices.

```sh
ott registry list
ott add kitchen DEVICE_UUID
ott pair kitchen
ott registry rotate kitchen
ott registry credential kitchen --output ./kitchen-credential.json
ott registry revoke kitchen
ott remove kitchen
ott unalias old_name
```

`add` verifies a durable registration's current generation before saving its
local alias. `pair` displays the active connection credential as before.
`registry credential` requires a private output file or explicit `--show-secret`;
output files are 0600 and never overwritten. Rotation mints a fresh credential,
so reconnect the player with the new code. `registry revoke` keeps its alias for
administration. `remove` revokes first, then removes that alias after confirmation.
`unalias` is purely local. `ott alias NEW_NAME DEVICE_UUID` adds a local name
without rotating a credential or writing server configuration.

For an unknown player, enable remote control and read its pairing code:

```sh
ott pending
ott registry approve kitchen DEVICE_UUID ABCDEFGH
ott registry approve office OTHER_UUID ABCD2345 --diagnostics on
```

Diagnostics default to off for a new identity. For an existing identity, omit
the flag to retain its policy. Approval preserves the selected trusted controller
address. The player claims its own credential; the CLI saves its alias only after
confirming the durable registration and current generation.

Existing `add`, `alias`, `pair` and `approve` commands probe registry support.
Only a definitive 404 permits legacy static behavior. Authentication, network,
malformed-response and server errors never trigger a Secret fallback. A locally
configured registry or pending registry checkpoint also disables fallback.
`registry` and `remove` always require registry support.

Recover a lost response explicitly, using the saved private request:

```sh
ott registry resume
ott registry add office OTHER_UUID --checkpoint ~/.config/ottplay-control/office.pending.json
ott registry resume --checkpoint ~/.config/ottplay-control/office.pending.json
```

The default checkpoint is `cli.json.registry-pending.json` beside `cli.json`.
Its directory must be private (0700 on Unix). The CLI fsyncs the request before
sending, locks concurrent operations and binds recovery to the controller,
administrator profile and registry store ID. Resume reads the receipt first;
if absent within the 24-hour recovery window, it retries only the same operation
ID and request. A changed current generation prevents alias updates. After
inspecting the current state, `ott registry forget` retires only the local
checkpoint; it does not undo a server operation.

Resolve a changed static configuration with `ott registry reconcile kitchen`.
Only when that static entry was deliberately removed, use
`ott registry reconcile kitchen --convert-to-dynamic`. These commands bind the
current generation and, where present, the observed static fingerprint. They
mint fresh credentials and never revive an old revoked token.

## Administrator API

All requests use the existing administrator Bearer credential. Responses have
Cache-Control: no-store. Token values are returned only by the explicit credential
endpoint or an approved pairing claim; registration lists and operation receipts
contain no tokens.
The shared request examples and recovery rules are in
[device-registry-v1.json](../contracts/device-registry-v1.json).

- GET `/api/device-registry`: version, store_id, revision, bounded device metadata
  and limits. Each device reports device_id, generation, origin, state and
  diagnostics_enabled; a configured static identity also has static_fingerprint.
- POST `/api/device-registry/add`: create a new dynamic identity.
- POST `/api/device-registry/revoke`: revoke an exact generation.
- POST `/api/device-registry/rotate`: replace an exact generation with a fresh
  credential, including explicit re-enablement of a revoked device.
- POST `/api/device-registry/reconcile`: resolve a static configuration conflict
  by minting a fresh override credential.
- GET `/api/device-registry/operations?id=OPERATION_ID`: read a durable receipt.
- GET `/api/device-registry/credential?device_id=UUID&generation=GENERATION`:
  read the exact active credential. A stale or revoked generation is rejected.

Add request:

```json
{
  "version": 1,
  "operation_id": "0123456789abcdef0123456789abcdef",
  "device_id": "living-room-tv",
  "diagnostics_enabled": false
}
```

Revoke or rotate request:

```json
{
  "version": 1,
  "operation_id": "abcdef0123456789abcdef0123456789",
  "device_id": "living-room-tv",
  "expected_generation": "11111111111111111111111111111111"
}
```

Successful mutations return version, operation_id, device_id, generation, state
and persisted:true. Revoke additionally returns device_stop_confirmed:false:
revoking network access does not prove that playback stopped on the physical
device. Already-admitted requests finish before revocation is confirmed; further
requests with the old credential fail. Queued commands, results, notification
media grants and runtime diagnostic credentials are revoked, and retained
diagnostic captures are cleared.
Camera acquisitions also bind the device generation, so a delayed frame cannot
reach a re-registered device that happens to reuse the same runtime identifier.

Operation IDs are 32 lowercase hexadecimal characters. Repeating an operation ID
with the same request returns its original receipt; changing its request returns
409. A receipt describes that operation, not necessarily the current state.
After a lost response, read the receipt and current device generation before
saving aliases or sharing credentials. Never fall back from an uncertain API
mutation to editing a Kubernetes Secret.
Local recovery checkpoints must bind the controller address, administrator
profile and store_id. Save the operation ID and canonical request before sending;
do not blindly repeat an uncertain mutation after its 24-hour receipt window.

## Pairing and selected addresses

POST `/api/pairings` accepts device_id, optional server_id and optional
server_address. Unapproved requests are bounded, expire after ten minutes and
are intentionally lost on restart. Administrator approval persists both the
registration and the approved claim; that claim can be recovered after restart
until its original expiry. Old clients may omit operation_id on approval: the
pairing ID is used as the durable operation ID.

POST `/api/pairings/approve` accepts id, code, optional operation_id and optional
diagnostics_enabled. A newly registered unknown device defaults to diagnostics
disabled. Enabling it requires global diagnostics configuration. An existing
identity retains its policy; a conflicting explicit value is rejected. Existing
operator scopes are never expanded to include the new device automatically.

GET and DELETE `/api/pairings?id=...` still require the matching claim secret.
DELETE retires the claim only; it does not revoke the device. Use administrator
revoke for that purpose.

To let a hosted player retain a trusted hosted address while older native
clients keep the canonical DNS address, configure explicit HTTPS aliases:

```json
{
  "discovery": {
    "public_url": "https://controller.example/ott-control",
    "public_aliases": ["https://player.example/ott-control"]
  }
}
```

There may be at most eight aliases, unique after the same normalization as
public_url. They are not inferred from allowed_origins. Omitting server_address
keeps the canonical address. An explicit address must match the canonical base
or an alias. Pending and approved claims bind that selected address separately
from the canonical DNS descriptor; both are checked again on approval/claim.
The claim returns the selected normalized address unchanged. Arbitrary URLs,
HTTP aliases, changed DNS descriptors and removed aliases are rejected. Public
discovery and DNS retain the canonical address for older native clients.
On startup, claims for removed addresses are retired durably before serving;
restoring an alias later cannot revive its old claim secrets. The registered
device and its current credential remain in place.

## Conflicts and recovery

Revoking a static device stores a durable tombstone, so restart cannot revive
its old Secret token. A static override is bound to the exact static credential
and diagnostic policy. Changing or removing that configuration produces a
conflict: the identity becomes unavailable while other valid devices continue.

To reconcile a still-present static identity, submit the ordinary expected
generation fields to `/reconcile` plus observed_static_fingerprint from the
current registry list. Reconciliation generates a new credential; it never
adopts a previous revoked token or silently overrides a newly changed Secret.

If the static entry was deliberately removed, `/reconcile` refuses it. Explicit
conversion uses `/add` with diagnostics_enabled, convert_to_dynamic:true and the
current expected_generation. A normal add refuses existing/revoked identities.
Distribute the newly returned generation's credential after either operation.

The registry is bounded: 64 active devices, 1,024 retained identities/tombstones,
64 revoked hashes per identity, 64 approved claims, 128 ordinary operation slots
plus 64 slots reserved for revocation, and
a 4 MiB file. Inventory can contain up to 1,088 identities when the 64 static
entries are distinct from every retained registry record. Receipts expire after
24 hours. Unexpired records are not evicted
to make a mutation fit; tombstones are not automatically pruned. A capacity
failure performs no mutation. Use a new device identity if its credential
history is exhausted, retaining the old tombstone.

Ordinary changes stop when 128 receipts are retained; revoke alone may extend
the pool to 192. Admission also reserves the exact record, credential-history and
serialized-byte footprint needed to revoke every active device. Existing static
devices need room for their first tombstone too. A repeated revoke of an already
revoked identity may be rejected if it would spend another device's reservation.
This means the effective ordinary capacity can be lower than a raw file or
record limit. The server never evicts unexpired receipts or tombstones to fit.
Startup rejects effective active credentials found anywhere in revoked history;
an old static credential hidden by an override or tombstone is not active reuse.

If persistence fails after a possible rename/fsync, the running server disables
device credential admission and rejects further registry operations. The old
or new file may be authoritative. Readiness returns 503 while liveness remains
available. Repair storage, validate the private file,
restart the single controller and inspect the durable operation receipt and
device generation. Do not delete the registry to make startup pass, and do not
assume a timeout or failed response proves that no registration occurred.

## Rollback after enabling the registry

After a registration, rotation or revocation, keep the private store and use a
registry-compatible server image. Disabling `device_registry`, restoring an old
static Secret, or running an image that ignores the store can revive revoked
credentials and make dynamically registered identities disappear. Restoring an
older registry backup can also roll back revocations; reconcile its effective
identities and credential history before serving traffic.

Rolling back to a pre-registry server requires an explicit migration of the
effective active identities and their operator/Home Assistant scopes. Preserve
the registry backup and ensure the replacement configuration excludes every
revoked credential; do not bypass tombstones by reusing the former static
configuration. A deployment rollback alone is not that migration.
