# Local native EPG fallback

The terminal CLI can use a separate native EPG process on the same computer when
the configured public guide service is unavailable. That process downloads the
fixed public `https://cdn.epg.one/epg2.xml.gz` feed, retains an accepted compressed
copy, and uses the same Rust parser, channel matcher and search implementation as
the hosted service. It does not download or parse the guide on the TV.

This is a guide fallback, not offline video storage. Playing a live channel or
an archive still requires access to the media provider. A vector database and a
second Python implementation of channel matching are not required.

## Configure the fallback

Keep every existing CLI configuration field and add `fallback_url` inside the
existing `epg` object in `~/.config/ottplay-control/cli.json` (or `OTT_CONFIG`):

```json
"epg": {
  "url": "https://ottplay-epg-api.igw-2560801.workers.dev/epg/v1",
  "source": "epg-one",
  "fallback_url": "http://127.0.0.1:18792/epg/v1"
}
```

Without `fallback_url`, existing primary-only behavior is unchanged. A fallback
must be a literal loopback IP address, with an HTTP(S) URL ending in `/epg/v1`.
DNS names, non-loopback hosts, credentials, query strings and fragments are
rejected. Loopback transport bypasses environment proxy settings. The primary
and fallback must be different endpoints.

Only classified EPG availability failures can trigger fallback: HTTP 429, 500,
502, 503 or 504, or a guide transport failure. Authentication/permission errors,
invalid responses, stale guide snapshots, changed catalogue identities and
player errors do not silently switch sources. A fallback restarts the whole
read-only search against one local guide generation and a fresh player catalogue;
results from different sources are not combined. The original search deadline
still applies. Playback is dispatched only after a complete accepted search and
is never retried through the fallback.

If the same `epg` object contains an explicit
[`stateful_archive_origins` policy](cli.md#stateful-archive-proxies), both primary
and fallback searches preserve it locally. Switching guide services never turns
media probing back on for those origins. Deferred candidates are marked in the
result and are not cached as verified recordings; the origin list is not sent to
either EPG service.

The normal stderr progress identifies when a local search starts. JSON results
remain on stdout:

```sh
ott l p --list "три кота"
ott -j l p --list "три кота" >results.json 2>search.log
ott l три кота
```

To use the local guide deliberately, set `epg.url` to the loopback URL and remove
`fallback_url`. Do not configure the same endpoint as both primary and fallback.

The compact search API uses Unicode 17.0.0 matching rules. The CLI uses that API
only when Python's `unicodedata.unidata_version` is also `17.0.0`; other versions
retain the compatible current-programme and full-history search, including the
local fallback. This can take longer for archive searches. A single stderr
progress message identifies compatibility mode. Check the installed runtime with
`python3 -c 'import unicodedata; print(unicodedata.unidata_version)'`.

## Install on macOS

Use a tested `ottplay-server` binary containing the native EPG search/cache
changes. The installer requires its source commit and SHA-256, copies those
exact bytes into an immutable revision directory, and creates one per-user
LaunchAgent. Supplying a hash binds the copied file; it does not replace build
verification or establish that arbitrary bytes were built from the named commit.

```sh
python3 scripts/install-local-epg.py install \
  --binary /absolute/path/to/ottplay-server \
  --source-sha FULL_TESTED_40_CHARACTER_SOURCE_COMMIT \
  --sha256 VERIFIED_64_CHARACTER_BINARY_SHA256
python3 scripts/install-local-epg.py status
```

Run this as the signed-in user, without `sudo`. The default port is `18792`;
`--port PORT` selects another unused unprivileged port. Update the CLI URL to
match. `--no-start` writes the managed installation without starting it.

The service has its own binary and port:

- LaunchAgent: `~/Library/LaunchAgents/com.ottplay.epg-local.plist`.
- Runtime and ownership manifest:
  `~/Library/Application Support/OTTPlay/epg-local/`.
- Binary: `revisions/<source-commit>/ottplay-server` beneath that runtime.
- Compressed feed cache: `~/.cache/ottplay-control/epg-native/`.
- Logs: `~/Library/Logs/OTTPlay/epg-local/stdout.log` and `stderr.log`.

Its arguments bind only `127.0.0.1`; `EPG_ONLY=true` selects the restricted EPG
router and `EPG_CACHE_DIR` selects its cache. It starts at login and restarts after
an unexpected exit. It does not change the existing OttPlay web server on port
8443, any player window, or player profiles. Do not enable `EPG_ONLY` on the
regular web-serving process: that mode exposes only the restricted EPG routes.

The first start may report `ready: false` while the public feed is downloaded and
parsed. Keep the background process running so its regular refresh can maintain
a fresh snapshot. It parses a large public guide; this is not a tiny-memory
service. Allow headroom for old and new snapshots during refresh, and inspect
actual memory use on the target computer before imposing a restrictive limit.

## Manage the process

```sh
python3 scripts/install-local-epg.py status
python3 scripts/install-local-epg.py stop
python3 scripts/install-local-epg.py start
python3 scripts/install-local-epg.py uninstall
```

`stop` unloads the job for the current session; the plist remains registered for
the next login. `start` reloads it. `uninstall` stops it and removes the LaunchAgent
plist. It preserves the ownership manifest, immutable runtime revisions, logs and
accepted guide cache, so a later reinstall can reuse them. Remove `fallback_url`
from the CLI configuration when permanently removing the service. Data deletion
is a separate explicit operation after checking that no process uses those paths.

Updates must use the installer with a new source revision and verified hash.
It refuses a different binary under an existing revision, an occupied port owned
by another process, unknown installation files, symlinked target paths or a
modified LaunchAgent. If an update cannot bootstrap, the previous registered
configuration is restored. If the first bootstrap fails, the owned installation
is retained for inspection and a later `start`; the installer exits with an error.

## Other operating systems

The EPG server and CLI fallback are portable; the included lifecycle installer
manages macOS LaunchAgents only. Run the tested native binary under the platform's
service manager, binding only loopback and setting a persistent cache directory:

```sh
EPG_ONLY=true EPG_CACHE_DIR=/absolute/private/cache/directory \
  /absolute/path/to/ottplay-server --host 127.0.0.1 --port 18792
```

Configure the same loopback `fallback_url` in the CLI. Keep the process separate
from a general-purpose OttPlay web server and use equivalent restart-at-login
and private-file permissions in the platform service definition.

## Troubleshooting

- **Not installed:** build or obtain a compatible, verified native binary, then
  run the installation command. Installing the Python CLI alone does not create
  the background service.
- **Port already in use:** inspect the listener, or select another port with
  `--port`. The installer does not kill an unrelated process.
- **Running but not ready:** inspect `stderr.log`. A cold process may still be
  downloading/indexing the feed. `/epg/v1/health` reports readiness independently
  of the process liveness endpoint `/health`.
- **No Internet:** a fresh accepted disk snapshot can support guide searches.
  A missing, corrupt or expired snapshot cannot. There is no silent stale-guide
  playback; restore connectivity and let refresh complete.
- **Public service fails and fallback also fails:** inspect the progress log and
  local readiness. Increasing a timeout cannot repair an unavailable feed or an
  incompatible native build.
- **Unknown or modified installation:** preserve the existing files and inspect
  ownership. The installer intentionally does not adopt a manually edited plist
  or an unrelated runtime directory.
- **Archive provider media probe timed out:** the guide was searched, but the
  selected media endpoint did not answer within its 15-second probe budget. This
  is distinct from the overall `--timeout`; no playback was dispatched. Check the
  media provider before repeating the search.
- **Search succeeds but media does not play:** guide lookup and media availability
  are separate. Diagnose the provider, archive retention and player restrictions;
  do not route private stream URLs or credentials into the public EPG service.

The cache accepts only the fixed public source. Compressed/decompressed sizes,
freshness, parse validity and atomic replacement are checked by the native server.
Failed refresh retains the last accepted snapshot, but its age is not reset.
