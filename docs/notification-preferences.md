# One notification preference per player

`notifications` controls Home Assistant banners, camera images and future
notification clips together. It does not stop or change primary IPTV/Plex
playback. The
player stores one global preference outside provider/profile settings. Its default
is on. It survives player reloads, app updates and provider/profile changes while
the application's persistent data is retained.

```sh
ott t1 notifications                 # read the durable preference
ott t1 notifications status          # same read
ott t1 notifications off             # mute banners and camera media together
ott t1 notifications on              # enable future events, without replay
ott t1,t2,t3,t4 notifications off     # change each distinct player in order
ott -j t1,t2 notifications status     # per-player machine-readable outcomes
```

These commands require an online updated player and controller. They do not wake
playback or disable kiosk. The CLI resolves all target names before sending a
group operation, de-duplicates identical device IDs, and reports each result. A
timeout does not prove a change failed; read the preference after reconnecting
instead of assuming success or blindly repeating it. Separate installations that
reuse a device credential still have separate storage: use a unique registration
for each player. Clearing application data resets this preference to its default.

## Disable or enable one player and verify delivery

Use the target's CLI alias, for example `f`. Find its registered device ID with
`ott devices` so you can identify the same player in the controller status.
The command changes only that installation; other players keep their preferences.
There is no need to disable the shared HA bridge or restart the player.

To disable both HA banners and notification camera media on that player:

```sh
ott -j f notifications off
ott -j f notifications
ott -j ha status
```

The direct player read should return `enabled: false` and `persisted: true`.
After the controller receives the updated presence, the matching entry in
`players` should report `notifications_enabled: false`, `active: false` and
`pending: 0`. If status has not caught up, read it again after the next presence
update. `active: false` alone does not prove that the preference is off: a hidden,
offline or standby player can also be inactive. `media_jpeg: true` describes
client capability and does not override the disabled preference.

To enable future notifications again:

```sh
ott -j f notifications on
ott -j f notifications
ott -j ha status
```

Confirm `enabled: true` and `persisted: true` on the player, then
`notifications_enabled: true` in its controller entry. Delivery also requires
the player to be connected, visible and active; enabling the preference does
not wake it or replay old events. The controller's shared HA counters and camera
requests may continue while another player remains eligible, even when `f` is
disabled. Use the individual entry to check this player's delivery state.

## Protocol and persistence

[The exact v1 examples](../contracts/notifications-v1.json) define the capability,
requests and responses. Requests contain `version:1`, the exact runtime from
capabilities, and `operation:get|set`; only set also contains a boolean `enabled`.
Success contains exactly `version`, `runtime`, `operation`, `enabled`, `persisted`.
The controller accepts success only with `persisted:true` and, for set, the
requested boolean. Negative results contain exactly `version`, `runtime`,
`operation`, `error`, using the four documented error codes. Runtime-mismatched,
unbound and malformed responses cannot consume a pending request. The command
operator scopes are `notifications.get` and `notifications.set`.

The player writes `sNotificationsEnabled` through its global storage abstraction
and verifies the value a fresh page can recover. Session-memory fallback alone
must not produce a persistence success. Turning off clears existing banners and
media, cancels pending media work, retires the notification runtime, and reports
inactive presence immediately. Turning on starts another notification runtime;
old queued events and in-flight media cannot reappear.

## Compatible presence negotiation

The first notification poll uses only the legacy `runtime` and `active` fields.
Local off always folds into `active=false`, even with an older controller. A new
controller advertises this additive response field:

```json
{"capabilities":{"enabled":true,"media":["jpeg-v1"]}}
```

Only after that response may a client add `enabled=true|false` and, if supported,
`media=jpeg-v1` to subsequent polls. This avoids sending unknown query fields to
older strict controllers. Missing `enabled` means on for legacy clients; missing
`media` means no camera delivery. Reported preference is ephemeral on the server,
not a second persistent setting. `ott ha status` shows its last reported value
when one exists; use the direct player command for a durable preference read.

The server purges pending notifications and revokes media access on disabled or
inactive presence, runtime changes and stale sessions. It selects active,
enabled recipients before acquiring camera media and checks again before queue
delivery and media access. There can be a short gap before the server receives
the new heartbeat; the disabled player already suppresses presentation and
fetches. A shared camera request for other active recipients can still finish.

Motion notifications represent detection only. Motion-cleared events must not
produce banners or media. This does not suppress configured occupancy, light or
outlet off events.
