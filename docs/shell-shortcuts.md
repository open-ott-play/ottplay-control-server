# Television shell shortcuts

`coco` wakes LG2, opens its configured OttPlay application, and plays a shuffled
`MultCocomelonAll` Plex collection. `cocop` opens the television's native Plex
application and starts that library through Home Assistant. Both take no
arguments. The `lg2` alias and its
native television binding must already be configured; see
[native television control](television.md).

Install the helper from a reviewed checkout beside, rather than inside, the
installed CLI revisions:

```sh
mkdir -p "$HOME/.local/lib/ottplay-shortcuts"
install -m 755 scripts/ott-tv-plex.py "$HOME/.local/lib/ottplay-shortcuts/ott-tv-plex.py"
```

On macOS or Linux, add this function to `~/.bashrc`:

```bash
coco() {
    python3 "$HOME/.local/lib/ottplay-shortcuts/ott-tv-plex.py" --mode ott "$@"
}
cocop() {
    python3 "$HOME/.local/lib/ottplay-shortcuts/ott-tv-plex.py" --mode native "$@"
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

Home Assistant must already have its Plex integration connected to the server
and expose the actual LG Plex client as a `media_player` entity. Use the Plex
integration's **Scan Clients** button entity, not the LG webOS entity or an
OttPlay proxy entity, for this content handoff. The Plex client must be signed
in and allowed to play the library.

Create `~/.config/ottplay-control/shortcuts.json`, replacing these example
entity IDs and paths with your installation's values:

```json
{
  "ha_url": "https://home-assistant.example.test",
  "ha_token_file": "/home/user/.config/ottplay-control/ha-token",
  "plex_entity": "media_player.native_lg_plex",
  "scan_entity": "button.plex_server_scan_clients",
  "expected_app_id": "your-installed-plex-app-id",
  "library_count_entity": "sensor.plex_server_library_multcocomelonall"
}
```

`library_count_entity` is optional; it enables a warning when the library exceeds
500 items. The token file contains only a Home Assistant long-lived access token,
must be a regular file owned by your user, and must have mode `0600` or `0400`.
Symlinks and group/world permissions are rejected. Do not put tokens in shell
functions, command arguments or the JSON configuration. No Plex server token is
needed by the helper. HTTP redirects are rejected and environment HTTP proxies
are disabled; HTTPS certificates are verified normally.

Run `cocop`. It sends one `tv go plex`, waits until the TV reports the expected
foreground application, invokes the configured Scan Clients button, and
waits for a native Plex entity with playback support. If the client is still
unavailable or not ready, it repeats only this idempotent discovery step no more
often than every 10 seconds, within the same 60-second readiness budget. A scan
HTTP timeout can also be retried. It then sends one
`media_player.play_media` request with:

```json
{
  "media_content_type": "movie",
  "media_content_id": "{\"library_name\":\"MultCocomelonAll\",\"libtype\":\"movie\",\"allow_multiple\":true,\"maxresults\":500,\"shuffle\":true}"
}
```

The request also names the configured native Plex entity. Playback confirmation
requires `playing`, a matching `media_library_title`, a media ID, and a newer
Home Assistant update than the snapshot taken before playback was requested.
The media ID must also change, or the same item's playback position must drop
by more than two seconds. Neither cached state nor normally advancing old
playback counts. If a new shuffle happens to pick the same item near its start,
the helper may report an uncertain result instead of claiming success.
Native Plex searches at most
500 matching items before shuffling; this is not a sample of the full library
when the library is larger.

The same 240-second total deadline applies. Foreground-app and client readiness
share a 60-second limit; playback submission and confirmation each allow up to
45 seconds within the remaining total budget. HTTP calls run in bounded child
processes so a stalled connection cannot keep the helper running indefinitely.
Cancellation stops local polling and further requests. An already accepted
server-side operation can still finish, so a timeout or interrupt never causes
an automatic playback retry.

If `cocop` fails, inspect `ott lg2 tv status` and the native Plex entity in Home
Assistant. Check its playback support, library attribute, client discovery and
library access. `ott lg2 plex status` describes Plex playback inside OttPlay and
does not report the separate native application's queue.
