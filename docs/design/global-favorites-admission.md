# Global Favorites fresh admission

This source contract adds no ninth operation. The required `admission` field in
`describe` is null when `write_ready` is false; otherwise it is exactly
`{operation_id:UUIDv4,ttl_ms:10000}`. A trusted player mints the ID with a
cryptographic random generator. Failure to mint fails write admission closed.
No client flag, ledger absence or constant true callback proves freshness.

Describe changes neither membership nor receipt bytes. At most one unstarted
grant is retained in memory, bound to the actual current runtime, captured
whole-store revision and globally readable context revision. A new describe
replaces that unstarted grant; it does not alter an already started stage.
The ten-second lifetime starts when minted and is never extended by polling.

The CLI reads a complete portable export, calculates its plan and deadline,
then refreshes describe immediately before begin. It requires identical store
revision, context revision, portable hash, byte count and entry count; it
rechecks kinds, limits and budget against the refreshed description. Begin
uses exactly the newly minted operation ID and revisions. The CLI measures
the grant lifetime conservatively from before the refresh request and never
extends its original command deadline.

The player atomically consumes a matching live grant into a stage, binding the
complete canonical begin payload. A duplicate begin may only return that same
existing stage for an identical payload. It never repeats application. A
consumed grant without its stage, expired grant, foreign runtime, changed
revision or different payload is rejected. Successful, missing, evicted or
unknown receipt status cannot resurrect a grant. Across a player restart no
previous in-memory grant is valid, even if a caller substitutes the new runtime.
Protected receipt admission and the actual physical writer lock remain required.

Acceptance includes concurrent begin attempts consuming one grant once,
same-payload duplicates returning one stage, changed-payload rejection,
expired/replaced/foreign-runtime grants, restart after commit with receipt loss,
and a refreshed describe whose context or portable snapshot changed. Those
cases must produce at most one commit or no commit, never implicit replay.
