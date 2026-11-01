# Television shell shortcuts

`coco` wakes LG2, opens its configured OttPlay application, and plays a shuffled
`MultCocomelonAll` Plex collection. `cocop` opens the television's native Plex
application and starts that library through Plex Server. Both take no
arguments. The `lg2` alias and its
native television binding must already be configured; see
[native television control](television.md).

Install the helper from a reviewed checkout beside, rather than inside, the
installed CLI revisions:

```sh
mkdir -p "$HOME/.local/lib/ottplay-shortcuts"
install -m 755 scripts/ott-tv-plex.py scripts/ott_plex_native.py "$HOME/.local/lib/ottplay-shortcuts/"
python3 -m venv "$HOME/.local/lib/ottplay-shortcuts/venv"
"$HOME/.local/lib/ottplay-shortcuts/venv/bin/python" -m pip install --require-hashes -r scripts/requirements-plex-shortcuts.txt
```

On macOS or Linux, add this function to `~/.bashrc`:

```bash
coco() {
    "$HOME/.local/lib/ottplay-shortcuts/venv/bin/python" "$HOME/.local/lib/ottplay-shortcuts/ott-tv-plex.py" --mode ott "$@"
}
cocop() {
    "$HOME/.local/lib/ottplay-shortcuts/venv/bin/python" "$HOME/.local/lib/ottplay-shortcuts/ott-tv-plex.py" --mode native "$@"
}
```

Open a new Bash shell, or reload `~/.bashrc` in your current shell. Then run:

```sh
coco
```

The helper resolves `~/.local/bin/ott` once and uses that same Python CLI revision
for every step, with the normal CLI configuration and `OTT_CONFIG` override. It
sends one `ott -t 90 lg2 tv go`, waits for live Plex capabilities and a readable
Plex library connection, then sends one
`ott -t 45 lg2 plex shuffle "MultCocomelonAll"`. It confirms the returned queue's
runtime and item order through `plex status` until playback is reported. The
500-item limit is honored when the player supports truncated queues.

Progress goes to stderr at each stage and every five seconds while waiting.
The entire workflow has a 240-second deadline; player readiness and final
playback confirmation also have separate 60- and 45-second limits. A nonzero
exit status means playback was not confirmed. A timeout can still mean a command
was accepted: inspect `ott lg2 tv status` and `ott lg2 plex status` before trying
again. Launch and shuffle requests are never automatically repeated; only
read-only status requests are polled.

The advisory lock `~/.local/state/ottplay-shortcuts/lg2.lock` prevents overlapping
`coco` and `cocop` runs. Its empty file remains after exit; the operating system
releases the lock even if the process dies. Do not delete it while a shortcut
is running. This lock does not prevent manual remote or `ott` commands. If a
different queue or player runtime appears during confirmation, the helper stops
without overriding it.

If startup fails, check native TV control first, then run `ott lg2 caps` and
`ott lg2 plex libraries`. Ensure Plex credentials are saved in the player and
the requested collection is available. `coco` uses Plex inside OttPlay; it does
not operate the separate native Plex television application.

## Native Plex with `cocop`

First configure the `plex` app alias in the controller's LG2 television binding
and verify its installed application ID with `ott lg2 tv apps`. The ordinary
OttPlay launch target stays unchanged. `ott lg2 tv go plex` must open the native
Plex application successfully.

The native app must be signed in to your Plex Server and able to access the
library. It must also authorize playback under Plex's own account and network
rules. During LG2 validation the native app displayed **“Remote playback
requires Remote Watch Pass”**, despite the LAN setup. Native playback was not
confirmed in that state; successful queue creation or an accepted command does
not bypass the prompt. Check the television screen if `cocop` cannot confirm
playback. Run `coco` explicitly to use Plex through OttPlay instead; the helper
never switches applications automatically after an uncertain native request.

This path uses Plex Companion through the server's client proxy. It
pins the Plex server and native client by their machine identifiers; friendly
names are not sufficient. A native client may advertise a loopback address,
which is not directly reachable from the controller Mac. The helper therefore
forces server-proxied control on each new client snapshot.

Create `~/.config/ottplay-control/shortcuts.json`, replacing these example
identifiers and paths with your installation's values:

```json
{
  "plex_url": "https://plex.example.test:32400",
  "plex_token_file": "/home/user/.config/ottplay-control/plex-token",
  "plex_server_id": "your-plex-server-machine-identifier",
  "plex_client_id": "your-native-lg-plex-client-identifier",
  "expected_app_id": "your-installed-plex-app-id"
}
```

The token file contains only a Plex access token, must be a regular file owned
by your user, and must have mode `0600` or `0400`. Symlinks and group/world
permissions are rejected. Keep tokens out of shell functions, command arguments
and the JSON configuration. HTTPS certificates are verified; redirects,
environment HTTP proxies, and requests to another server origin are rejected.
The pinned dependency file installs PlexAPI and its HTTP dependencies into the
shortcut-specific environment; the ordinary `ott` installation stays separate.
The server URL must include an explicit port and contain only an HTTP(S) origin
(an optional trailing slash is accepted). IPv6 literals and path prefixes are
not supported by this adapter's PlexAPI playback URL handling; invalid URLs
are rejected before the television is changed.

Run `cocop`. It sends one `tv go plex`, waits until the TV reports the expected
foreground application, and polls Plex Server until the configured client
supports playback, timelines and play queues. It then:

1. Resolves `MultCocomelonAll` and creates one shuffled queue of up to 500 items.
2. Sends one playback request for that exact server-created queue through the
   Plex Server client proxy.
3. Polls the pinned client's timeline until it reports `playing` with that exact
   queue ID and a media ID.

Old playback, another queue and a missing queue ID cannot confirm this request.
The prepared queue ID and first media ID are printed to stderr for diagnosis.
When the library is larger than 500 items, the helper reports the cap and
shuffles the selected 500-item subset. Neither queue creation nor playback is
retried automatically. Client discovery and timeline reads are safe to repeat.

The same 240-second total deadline applies. Foreground-app and client discovery
share a 60-second limit; queue preparation, playback submission and confirmation
each allow up to 45 seconds within the remaining total budget. Network operations
run in bounded child processes, so a stalled connection cannot keep the helper
running indefinitely. Cancellation stops local polling and further requests.
An already accepted server-side operation can still finish; a timeout or
interrupt therefore never causes an automatic playback retry.

If `cocop` fails, inspect `ott lg2 tv status` and native Plex on the television.
Look for authorization or Remote Watch Pass prompts even when using the LAN.
Check the token's access to the configured server/library, the exact server and
client machine identifiers, and the native client's Companion support. A
successful app launch alone is not evidence that Plex has registered the
client. `ott lg2 plex status` describes Plex playback inside OttPlay and does
not report the separate native application's queue. Home Assistant is not
needed for native Plex content control; it may still be used by the television
binding for power-on fallback.
