#!/usr/bin/env python3
"""OTT-play remote CLI using the Python 3 standard library."""
import argparse
import base64
import getpass
import http.client
import ipaddress
import importlib.util
import json
import math
import os
import ssl
from pathlib import Path
import queue
import re
import secrets
import stat
import struct
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request
import warnings
import zlib



def https_handler():
    """Retain platform verification; optionally require stronger OpenSSL keys."""
    context = ssl.create_default_context()
    context.minimum_version = max(context.minimum_version, ssl.TLSVersion.TLSv1_2)
    level = os.environ.get("OTT_TLS_SECURITY_LEVEL")
    if level is not None:
        if level not in ("2", "3", "4", "5"):
            raise ValueError("OTT_TLS_SECURITY_LEVEL must be 2, 3, 4 or 5")
        # Raise strength without widening a host-selected cipher allowlist.
        if int(level) > context.security_level:
            ciphers = ":".join(cipher["name"] for cipher in context.get_ciphers()
                               if cipher["protocol"] != "TLSv1.3")
            context.set_ciphers(ciphers + ":@SECLEVEL=" + level)
    return urllib.request.HTTPSHandler(context=context)


HELP = """ott [-c/--config FILE] [-t/--timeout SECONDS] [-j/--json] [--receipt] PLAYER [COMMAND ...]
  ott resolve --request-stdin         resolve a private playlist search for a local player
  ott diagnostics --help             scoped runtime diagnostics and session control
  ott report verify DIRECTORY [-j]   verify and re-evaluate saved evidence offline
  ott presets                        list locally configured preset names
  ott devices                        list devices and their last connection
  ott ha status [-j]                 Home Assistant bridge and active banner recipients
  ott NAME notifications [on|off]    read/save the single banner and camera preference
  ott NAME settings [list|schema]    discover typed settings for this player/source
  ott NAME setting KEY [VALUE]      read or save one setting (e.g. bufSize 3)
  ott NAME setting players hls.js   choose a schema-listed playback engine
  ott t1,t2 notifications off        save that preference on each distinct player
  ott alias NAME UUID                name an existing device
  ott add NAME UUID                  create a device access code and queue
  ott registry --help               durable registration, revoke, rotate and recovery
  ott remove NAME                   revoke access, then remove the local alias
  ott unalias NAME                  remove only the local alias
  ott pair NAME                      show the player connection settings
  ott discover                       show controllers advertised in network DNS
  ott pending                        list pending player pairing requests
  ott approve NAME CODE              approve the code displayed by that player
  ott NAME                           show available controls, then a player summary
  ott NAME load PRESET               apply a private Plex, M3U and optional Stalker preset
  ott NAME load PRESET plan|apply|resume|verify [--checkpoint FILE]
                                     inspect or safely resume exact preset settings
  ott handoff SOURCE TARGET          continue live/archive viewing on another player
  ott NAME 12                        play channel 12 from the s listing
  ott NAME prev / previous           play the previous channel in the current category
  ott NAME next                      play the next channel in the current category
  ott NAME +15 / -15                 move forward / back 15 channels in that category, wrapping
  ott NAME TITLE                     search channels, current EPG, then archives within 144 hours
  ott NAME play s                    play a channel whose name is reserved
  ott NAME s [TEXT]                  list channels, optionally matching TEXT
  ott NAME s --limit N [--offset N] [TEXT]  list one media page (N: 1–200)
  ott -t 300 NAME s                  allow more time for a complete large catalog
  ott NAME categories [list]         list media categories; * marks the selected category
  ott NAME category NAME             select a category without starting playback (alias: cat)
  ott NAME categories select id:ID   select the exact category ID shown by categories
  ott NAME category all              return to all videos; s lists the selected category
  ott NAME play id:VIDEO_ID           play an exact video ID shown by ott --json NAME s
  ott NAME p                         list channel — current programme
  ott NAME p TEXT                    search current programmes, then available archives
  ott NAME p --list [TEXT]           list programmes without switching channels
  ott NAME vp TEXT                   loop VPortal videos whose titles match TEXT
  ott NAME vpr TEXT                  shuffle matching VPortal videos and loop the queue
  ott NAME vp --list TEXT            list matching VPortal videos without playing
  ott NAME v                         show volume
  ott NAME v 35                      set volume to 35%
  ott NAME v +5 / v -5               increase / decrease volume
  ott NAME aspect                    show current and saved aspect modes
  ott NAME aspect fit / fill         request Fit to screen / Fill screen
  ott NAME providers                 list providers (zero-based indices)
  ott NAME provider m3u              select a provider by ID, index or name
  ott NAME provider m3u 1            select M3U, wait for loading, play channel 1
  ott NAME provider m3u R            select M3U, play a random channel (r/random)
  ott NAME provider m3u РЕН          exact channel name first, else random match
  ott t1,t2,t3,t4 provider m3u        switch each listed player (alias: prov)
  ott NAME provider offline          select Local videos (if listed in providers)
  ott NAME provider-config FILE      update active provider settings from JSON
  ott NAME plex setup URL [TOKEN]    set Plex address and token (hidden prompt if omitted)
  ott NAME plex setup URL --token-file FILE  set Plex address and token from a file
  ott NAME plex server URL           update the saved Plex server address
  ott NAME plex token [TOKEN]        update the Plex token (hidden prompt if omitted)
  ott NAME plex token-file FILE      update the Plex token from a file
  ott NAME plex preview ID [ID ...]  check the listed Plex items without starting playback
  ott NAME plex play ID [ID ...]     play IDs in order from the start; stop after the last
  ott NAME plex play --shuffle ID...  shuffle all IDs once; kiosk on repeats that order
  ott NAME plex libraries            list movie/show libraries from the player's Plex server
  ott NAME plex shuffle NAME         shuffle up to 500 library items; warn if the library is larger
  ott NAME plex library [--shuffle] [--preview] NAME  play or preview a library (or id:16)
  ott NAME plex queue / status       show the Plex queue without changing it
  ott NAME plex next / prev / stop   move within or clear the Plex queue (no wrapping)
  ott NAME playlist URL              update the M3U playlist
  ott NAME fav                       list saved favorites on this player
  ott NAME fav add [NAME]            save the currently playing item and its profile
  ott NAME fav play ID_OR_NAME       load the saved profile and play that exact item
  ott NAME fav remove ID_OR_NAME     remove one saved favorite
  ott NAME profiles                  list the active provider’s 15 profiles without URLs
  ott NAME profile N                 select M3U, VPortal or Local videos profile 1–15
  ott NAME profile save N [NAME]     save the active Local videos source and selection
  ott NAME profile load N            load a saved profile (alias: profile N)
  ott NAME profile export N FILE     export portable Local videos configuration
  ott NAME profile import N FILE     save exported configuration; bind locally if needed
  ott NAME profile N url URL         change a profile's playlist URL
  ott NAME profile N history HOURS   set archive depth in hours (0–8760)
  ott NAME profile N vportal LINK    set a standalone VPortal profile's link
  ott NAME profile N name NAME       rename a profile
  ott NAME profile-config N FILE     update profile settings atomically from JSON
  ott NAME caps                      show runtime identity and supported controls
  ott NAME doctor [-j]                read web/native identity and diagnostic capabilities
  ott NAME debug [--engine] [-j]      read bounded runtime/native diagnostics (alias: dbg)
  ott NAME inspect --view ui,media    observe the interface and media without changing playback
  ott NAME operation REQUEST_ID      read a web operation receipt without repeating its effect
  ott NAME bundle --out DIRECTORY    save private, bounded diagnostic evidence
  ott server debug [-j]               read controller process and queue counters
  ott NAME test list                 list fixed read-only diagnostic scenarios
  ott NAME test run health --report DIRECTORY
  ott NAME test run media-progress --duration 5 --report DIRECTORY
  ott NAME test run media-progress --lane native --duration 5 --report DIRECTORY
  ott NAME screenshot                save one remote screenshot; browser source selection may be needed
  ott NAME shot -o FILE.png           same capture, with an explicit output file
  ott NAME key KEY                   send one supported named input after acknowledgement
  ott NAME pause / resume            pause or resume supported archive/VOD playback
  ott NAME seek SECONDS              seek supported VOD to an absolute position (0–9007199254740991)
  ott NAME restart                   reload the player after acknowledgement
  ott NAME restart player            explicit form of restart
  ott NAME restart stream            restart only the current stream
  ott NAME restart app               relaunch a supported native app
  ott NAME reload                    reload the player after acknowledgement
  ott NAME reboot [device]           reboot the device OS only when supported
  ott NAME android bind NATIVE       bind a separately provisioned native device alias
  ott NAME android status / logs     inspect Android independently of the web player
  ott NAME android operation ID      read a durable native receipt without replay
  ott NAME android recover           reattach stalled video, keeping its queue/position
  ott NAME android restart / reboot  restart the Android app / reboot the tablet
  ott NAME android standby / go      enter or leave standby through the bound native agent
  ott NAME android screenshot [-o FILE.png]  capture the Android display
  ott NAME android pause / resume / seek SECONDS  control native-owned playback
  ott NAME android queue play ID...  play an exact VPortal queue and loop it
  ott NAME android queue status / next / prev / restart / stop
  ott NAME update --variant api22|full  install the latest published beta over Wi-Fi
  ott NAME update --version TAG --variant api22|full  select an official release
  ott NAME update --variant api22|full --prepare-only  download and verify only
  ott NAME update status             inspect Capacitor APK update progress
  ott NAME update prepare HTTPS_APK_URL SHA256  download and verify a newer APK
  ott NAME update install SHA256     open the Android installer after ACK
  ott NAME android update HTTPS_URL SHA256  install a signed update manifest
  ott NAME standby / go              enter player standby / wake and start configured playback
  ott NAME stop / start              aliases for standby / go (wake also wakes the player)
  ott NAME kiosk [status]            show kiosk policy and playback health
  ott NAME kiosk on [CHANNEL]        lock channel, or the current VPortal/Plex video queue
  ott NAME kiosk on --strict [CHANNEL]  lock local controls; media-footer seeking needs a supported player
  ott NAME kiosk set CHANNEL         replace by number or first name match
  ott NAME kiosk off                 release the kiosk lock
  ott NAME R / random [FROM TO]      play a safe random channel from All
  ott NAME msg TEXT                  show an on-screen message
  ott NAME exit / quit / close        exit the app only when the platform supports it

Configuration: ~/.config/ottplay-control/cli.json or OTT_CONFIG.
--receipt writes one safe JSON request receipt to stderr per player RPC; stdout is unchanged.
Comma-separated player lists support provider/prov and notifications; each distinct player runs once, in order.
Add a channel number, R/random or name to switch and play from the provider's complete All catalog.
Use provider "Local videos" -- NAME to separate a multiword provider from a channel explicitly.
An exact full channel name wins; otherwise a random partial-name match is selected. Playback wakes standby.
R/random uses All: 80% from channel numbers 1–1000 and 20% from higher numbers when both pools are available.
An explicit FROM TO range is inclusive and uniform. Random selection excludes AceStream and parental-protected channels.
All player names are checked first. A player failure does not stop the remaining players.
--timeout applies to each player; completed switches are not rolled back or automatically retried.
An exact saved player name containing commas retains its existing single-player meaning.
Player, channel, programme, VPortal and provider searches are case-insensitive.
Command aliases: status/st; s/channels; p/programs/programmes; v/vol/volume;
vp/vportal; vpr/vportal-random; msg/message; profile/prof; profiles/profs;
provider/prov; providers/provs; categories/cats; category/cat; capabilities/caps; input/key; prev/previous; screenshot/shot;
fav/favs/favorite/favorites/favourite/favourites; aspect/aspect-ratio; stop/standby; go/start/wake.
Aspect modes also accept "Fit to screen" and "Fill screen".
Use -l or --list with p/vp/vpr. Input aliases include enter/ok, return/back,
ch+/channel_up, ch-/channel_down, vol+/volume_up, vol-/volume_down, fs/fullscreen.
Profile fields: url/playlist, history/history-hours/history_hours, vp/vportal, n/name.
Only these exact aliases are expanded; use play TITLE for a reserved channel name.
Category names match without case: exact name first, then a unique substring. Use id:ID for duplicates.
Use category select NAME for a category named List, Status, Select or Set.
Example: ott NAME cat "Tom and Jerry"; ott NAME s; ott NAME 12; ott NAME next.
Numbers in s are catalog numbers, even when the selected category has fewer rows.
Signed channel offsets are nonzero whole numbers from -9007199254740991 to
+9007199254740991; each offset sends one request. Unsigned 15 selects channel 15.
"""

# Top-level commands take precedence over player names, including saved aliases.
TOP_LEVEL_COMMANDS = frozenset((
    "add", "alias", "approve", "devices", "diagnostics", "discover", "handoff",
    "ha", "help", "pair", "pending", "presets", "registry", "remove", "report", "resolve", "server", "unalias",
))


COMMAND_ALIASES = {
    "st": "status",
    "r": "random",
    "channels": "s", "programs": "p", "programmes": "p",
    "vol": "v", "volume": "v", "vportal": "vp", "vportal-random": "vpr",
    "message": "msg", "prof": "profile", "profs": "profiles",
    "prov": "provider", "provs": "providers",
    "cats": "categories", "cat": "category",
    "caps": "capabilities", "key": "input", "quit": "exit", "close": "exit",
    "previous": "prev", "shot": "screenshot",
    "stop": "standby", "start": "wake", "go": "wake",
    "aspect-ratio": "aspect",
    "notification": "notifications", "notify": "notifications", "notif": "notifications",
    "fav": "favorites", "favs": "favorites", "favorite": "favorites",
    "favourite": "favorites", "favourites": "favorites",
}
PROFILE_FIELD_ALIASES = {
    "url": "playlist", "playlist": "playlist",
    "history": "history_hours", "history-hours": "history_hours", "history_hours": "history_hours",
    "vp": "vportal", "vportal": "vportal", "n": "name", "name": "name",
}
LIFECYCLE_OPERATIONS = frozenset(("restart_stream", "reload_player", "restart_app", "standby", "wake", "exit_app", "reboot_device"))
INPUT_KEYS = frozenset(("up", "down", "left", "right", "ok", "back", "menu", "settings", "channels", "guide", "info",
                        "channel_up", "channel_down", "volume_up", "volume_down", "mute", "play_pause", "audio", "aspect",
                        "zoom", "pip", "fullscreen"))
INPUT_ALIASES = {
    "u": "up", "d": "down", "l": "left", "r": "right", "enter": "ok", "select": "ok", "return": "back",
    "setup": "settings", "channel-list": "channels", "epg": "guide", "i": "info",
    "channel-up": "channel_up", "ch+": "channel_up", "channel-down": "channel_down", "ch-": "channel_down",
    "volume-up": "volume_up", "vol+": "volume_up", "volume-down": "volume_down", "vol-": "volume_down",
    "play-pause": "play_pause", "pp": "play_pause", "fs": "fullscreen",
}
CHANNEL_STEPS = {"prev": "previous_channel", "next": "next_channel"}
CHANNEL_OPERATIONS = frozenset((*CHANNEL_STEPS.values(), "step_channel"))
PLAYBACK_OPERATIONS = frozenset(("pause", "resume", "seek", *CHANNEL_OPERATIONS))
OFFLINE_PROFILE_OPERATIONS = frozenset(("list", "load", "save", "export", "import"))
OFFLINE_BINDING_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
ASPECT_OPERATIONS = frozenset(("get", "set"))
NOTIFICATIONS_OPERATIONS = frozenset(("get", "set"))
NOTIFICATIONS_ERRORS = frozenset(("invalid_request", "runtime_mismatch", "storage_unavailable", "unsupported"))
ASPECT_MODES = {"fit": "Fit to screen", "fill": "Fill screen"}
SCREENSHOT_SOURCES = frozenset(("player-view", "player-window", "browser-tab", "window", "display"))
MAX_SCREENSHOT_BYTES = 1024 * 1024
RECEIPT_ACTIONS = frozenset((
    "status", "capabilities", "screenshot", "inspect", "providers", "profiles",
    "channels", "programs", "epg_catalog", "resolve_archive", "play_archive_catalog",
    "play_catalog", "kiosk", "maintenance", "vportal_queue", "lifecycle", "input",
    "playback", "profile", "profile_settings", "profile_save", "profile_export", "profile_import", "restart", "play", "provider", "app_update",
    "vportal", "vportal_random", "vportal_search", "command", "provider_settings", "plex_queue", "plex_library", "categories", "favorites", "aspect",
    "settings_compare", "playback_handoff_export", "playback_handoff_import", "notifications", "player_settings",
))
PLEX_QUEUE_OPERATIONS = frozenset(("play", "preview", "status", "next", "previous", "stop"))
MAX_PLEX_QUEUE_ITEMS = 500
PLEX_QUEUE_ERRORS = frozenset((
    "Plex configuration is missing or invalid.",
    "Plex provider module could not be loaded.",
    "Plex server is unreachable or access was denied.",
    "One or more Plex items are unavailable or not playable.",
    "Plex playback is unavailable on this player.",
    "Plex queue request timed out.",
    "Plex queue request was cancelled.",
    "Plex queue request is already in progress.",
    "Player context changed before Plex playback.",
    "Plex playback could not start.",
    "Plex queue is empty.",
    "Plex queue is already at its first item.",
    "Plex queue is already at its last item.",
    "Unlock parental access before starting the Plex queue.",
    "Kiosk mode does not allow a Plex queue.",
))
PLEX_LIBRARY_OPERATIONS = frozenset(("list", "preview", "play"))
PLEX_LIBRARY_ERRORS = {
    "ambiguous_library": "Several Plex libraries match. Use plex libraries, then select id:NUMBER.",
    "library_not_found": "No Plex library matches. Use plex libraries to see available names.",
    "unsupported_library": "This Plex library is not a movie or show library.",
    "too_many_libraries": "The Plex server has more than 1000 libraries; the result is incomplete.",
    "too_many_items": "The Plex library exceeds the 500-item queue limit; no partial queue was started.",
    "incomplete_library": "The complete Plex library could not be verified; no partial queue was started.",
}
CATEGORIES_OPERATIONS = frozenset(("list", "select"))
MAX_CATEGORY_PAGE_SIZE = 200
MAX_CATEGORY_TOTAL = 1000000
CATEGORY_ERRORS = {
    "catalog_changed": "The catalog changed. Run categories again before selecting.",
    "catalog_incomplete": "Catalog discovery failed before completing. Check the provider connection and reload its catalog before retrying.",
    "runtime_mismatch": "The player restarted. Run categories again before selecting.",
    "category_not_found": "The category is no longer available. Run categories again.",
    "restricted": "Current player policy does not allow this category operation.",
    "unsupported": "Media categories are not supported by the active provider.",
    "unavailable": "The media catalog is unavailable; check player status.",
    "invalid_request": "The player could not accept the category request; update the player and controller.",
}


FAVORITES_OPERATIONS = frozenset(("list", "add", "play", "remove"))
MAX_FAVORITES = 100
FAVORITE_ID_PATTERN = r"fav-[a-z0-9-]{1,60}"
FAVORITES_ERRORS = {
    "invalid_request": "The favorite request no longer matches this player runtime; check caps and try a new request.",
    "unsupported": "Favorites are unavailable for this player or provider.",
    "no_current_item": "There is no current playable item to save; start the desired item first.",
    "unsupported_item": "This item has no stable provider reference and cannot be saved as a favorite.",
    "profile_unavailable": "The saved profile is unavailable or could not finish loading.",
    "profile_changed": "The saved profile's source has changed; restore it or save a new favorite from the desired source.",
    "item_unavailable": "The exact saved item is unavailable from its provider; no substitute was selected.",
    "ambiguous_favorite": "Several favorites match; use fav to list their exact IDs.",
    "favorite_not_found": "No favorite matches; use fav to list saved entries.",
    "too_many_favorites": "The player has reached the 100-favorite limit; remove an entry before adding another.",
    "storage_unavailable": "The player's favorite storage could not be read or saved.",
    "policy_denied": "Player kiosk or parental policy does not allow this favorite operation.",
    "busy": "Another favorite operation is still in progress.",
    "timeout": "The favorite operation timed out; check player status before another change.",
    "cancelled": "The favorite operation was cancelled.",
    "context_changed": "The player or profile changed while preparing the favorite.",
    "playback_failed": "The provider could not start the saved item; check player status.",
}


def command_verb(words):
    """Normalize exact command tokens without changing user search text."""
    value = words[0].casefold() if words else ""
    return COMMAND_ALIASES.get(value, value)


def listing_option(words):
    return len(words) > 1 and words[1].casefold() in ("--list", "-l")


class Error(Exception):
    pass


class PlayerRejected(Error):
    """A completed request explicitly rejected by the player, with its metadata."""
    def __init__(self, message, data):
        super().__init__(message)
        self.data = data


class PlayerUnsupported(Error):
    """A completed unsupported response, optionally retaining bound metadata."""
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data


class TransportError(Error):
    pass


class HTTPError(Error):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Server returned HTTP {code}. " + {
            401: "Check operator_token or admin_token in the private configuration.",
            403: "Insufficient permissions or a disallowed Origin.",
            404: "Check the address and update the server to support CLI requests.",
            429: "The queue or request limit has been reached; try again later.",
        }.get(code, "Check the request parameters."))


class EpgClient:
    """Public EPG transport, deliberately independent of controller credentials."""
    def __init__(self, settings, timeout):
        if (not isinstance(settings, dict) or set(settings) != {"url", "source"}
                or settings["source"] != "epg-one" or not isinstance(settings["url"], str)):
            raise Error('EPG configuration must contain url and source "epg-one"')
        url = settings["url"]
        try:
            parsed = urllib.parse.urlsplit(url)
            invalid = (parsed.scheme not in ("http", "https") or not parsed.hostname
                       or parsed.username is not None or parsed.password is not None
                       or "?" in url or "#" in url or "\\" in url
                       or any(ord(char) <= 32 or ord(char) == 127 for char in url)
                       or parsed.port == 0)
        except ValueError:
            invalid = True
        if invalid:
            raise Error("EPG url must be an HTTP(S) address without credentials, whitespace, query or fragment")
        self.url = url.rstrip("/") + "/current"
        self.timeout = min(10, timeout)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect(), https_handler())

    def current(self, channels, search):
        payload = {"version": 1, "source": "epg-one", "channels": channels, "search": search}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 512 * 1024:
            raise Error("EPG catalogue exceeds the request size limit; no playback was requested")
        deadline = time.monotonic() + self.timeout
        cancelled = threading.Event()
        outcome = queue.Queue(maxsize=1)
        def perform():
            try:
                outcome.put((True, self._current(body, deadline, cancelled)))
            except Exception as exc:
                outcome.put((False, exc))
        threading.Thread(target=perform, name="ottplay-epg-http", daemon=True).start()
        try:
            ok, value = outcome.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            raise Error("EPG service timed out; no player EPG scan or playback was requested") from None
        if time.monotonic() >= deadline:
            cancelled.set()
            raise Error("EPG service timed out; no player EPG scan or playback was requested")
        if not ok:
            raise value
        return value

    def _current(self, body, deadline, cancelled):
        request = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ottplay-cli/1.0"})
        try:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise Error("EPG service timed out")
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise Error("EPG service did not return a successful response")
                body = bytearray()
                while True:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise Error("EPG service timed out")
                    chunk = response.read1(min(65536, 2 * 1024 * 1024 + 1 - len(body)))
                    if not chunk:
                        remaining = getattr(response, "length", None)
                        if isinstance(remaining, int) and remaining > 0:
                            raise Error("EPG service returned an incomplete response")
                        return json.loads(body)
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise Error("EPG service response exceeds the size limit")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise Error(f"EPG service returned HTTP {code}; no player EPG scan or playback was requested") from None
        except (OSError, http.client.HTTPException):
            raise Error("EPG service unavailable; no player EPG scan or playback was requested") from None
        except ValueError:
            raise Error("EPG service returned invalid JSON") from None


def epg_text(value, maximum, encoding="utf-16-le"):
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode(encoding)) <= maximum * (2 if encoding == "utf-16-le" else 1)
    except UnicodeEncodeError:
        return False


def epg_number(value):
    return type(value) in (int, float) and -9007199254740991 <= value <= 9007199254740991


def epg_batches(channels):
    """Keep every channel while respecting each public EPG request's limits."""
    batch, size = [], 2048  # Envelope and the bounded search text.
    for channel in channels:
        row = {key: value for key, value in channel.items() if key != "number"}
        row_size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if batch and (len(batch) == 2048 or size + row_size > 480 * 1024):
            yield batch
            batch, size = [], 2048
        batch.append(row)
        size += row_size
    if batch:
        yield batch


def expired_epg_catalog(error):
    return (isinstance(error, PlayerRejected)
            and error.data.get("error") == "Channels or provider changed. Retry the EPG query before playing.")


def server_programs(client, device, settings, search, refresh=False):
    epg = EpgClient(settings, client.timeout)
    if not epg_text(search, 1024, "utf-8"):
        raise Error("Programme search must contain at most 1024 UTF-8 bytes")
    snapshot = client.call(device, "epg_catalog", {})
    invalid = "The player returned an invalid EPG catalogue; no playback was requested"
    if (not isinstance(snapshot, dict) or not epg_text(snapshot.get("catalog"), 128, "utf-8")
            or not snapshot["catalog"].strip() or not isinstance(snapshot.get("channels"), list)
            or len(snapshot["channels"]) > 10000):
        raise Error(invalid)
    channels, by_id, numbers = [], {}, set()
    for row in snapshot["channels"]:
        if (not isinstance(row, dict) or not all(epg_text(row.get(key), 512) for key in ("id", "name", "tvgId", "tvgName"))
                or not row["id"].strip() or row["id"] in by_id or type(row.get("number")) is not int
                or row["number"] < 1 or row["number"] in numbers or type(row.get("shift")) is not int
                or not -86400 <= row["shift"] <= 86400):
            raise Error(invalid)
        if numbers and row["number"] <= channels[-1]["number"]:
            raise Error(invalid)
        channels.append({key: row[key] for key in ("id", "number", "name", "tvgId", "tvgName", "shift")})
        by_id[row["id"]] = channels[-1]
        numbers.add(row["number"])
    if not channels:
        return {"as_of": time.time(), "checked": 0, "partial": False, "programs": [], "total": 0}, {}
    programs, targets, generation, as_of = [], {}, None, 0
    search_deadline = time.monotonic() + client.timeout
    for batch in epg_batches(channels):
        batch_by_id = {row["id"]: by_id[row["id"]] for row in batch}
        remaining = search_deadline - time.monotonic()
        if remaining <= 0:
            raise Error("EPG service timed out; no player EPG scan or playback was requested")
        epg.timeout = min(10, remaining)
        response = epg.current(batch, search)
        if time.monotonic() >= search_deadline:
            raise Error("EPG service timed out; no player EPG scan or playback was requested")
        invalid = "EPG service returned an invalid or incomplete current-programme result; no playback was requested"
        if (not isinstance(response, dict) or type(response.get("version")) is not int or response["version"] != 1
                or response.get("source") != "epg-one" or not epg_text(response.get("generation"), 256)
                or not response["generation"] or type(response.get("fetchedAt")) is not int
                or not 0 < response["fetchedAt"] <= 9007199254740991 or not epg_number(response.get("asOf"))
                or abs(response["asOf"] - time.time()) > 60
                or type(response.get("checked")) is not int or response["checked"] != len(batch)
                or type(response.get("total")) is not int or response["total"] != len(batch)
                or not isinstance(response.get("programs"), list) or len(response["programs"]) > len(batch)):
            raise Error(invalid)
        if response.get("stale") is not False:
            raise Error("EPG service has no fresh guide snapshot; no playback was requested")
        last_number = 0
        for row in response["programs"]:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in batch_by_id
                    or not epg_text(row.get("title"), 16384) or not row["title"]
                    or any(not epg_number(row.get(key)) for key in ("start", "end"))
                    or not row["start"] <= response["asOf"] < row["end"]):
                raise Error(invalid)
            channel = by_id[row["id"]]
            if channel["number"] <= last_number:
                raise Error(invalid)
            last_number = channel["number"]
            programs.append({"channel": channel["name"], "number": channel["number"], "title": row["title"],
                             "start": row["start"], "end": row["end"]})
            targets[channel["number"]] = {"catalog": snapshot["catalog"], "id": row["id"]}
        if generation is not None and response["generation"] != generation:
            raise Error("EPG guide changed during search; retry the query. No playback was requested")
        generation = response["generation"]
        as_of = max(as_of, response["asOf"])
    programs = [row for row in programs if row["start"] <= as_of < row["end"]]
    if not programs and search.strip():
        spec = importlib.util.spec_from_file_location("ott_programme_search", Path(__file__).resolve().with_name("programme_search.py"))
        history = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(history)
        try:
            return history.search_archives(client, device, settings, snapshot, search, refresh,
                                           catalog_expired=expired_epg_catalog)
        except history.SearchError as exc:
            raise Error(str(exc)) from None
    return {"as_of": as_of, "checked": len(channels), "partial": False,
            "programs": programs, "total": len(channels)}, targets


def read_json(path):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration field")
            result[key] = value
        return result
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=unique_fields)
    except (OSError, ValueError) as exc:
        raise Error(f"Could not read JSON file {path}") from exc


def write_private(path, data, exclusive=False):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ott-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if exclusive:
            # Reserve a provisioning journal without replacing another operation.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def decode_response_json(raw):
    """Reject ambiguous fields and non-finite numbers before projecting receipts."""
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate response field")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("Non-finite response number")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite response number")
        return number

    return json.loads(raw, object_pairs_hook=unique_fields,
                      parse_constant=reject_constant, parse_float=finite_float)


def print_request_receipt(receipt):
    print(json.dumps(receipt, ensure_ascii=True, allow_nan=False, separators=(",", ":")), file=sys.stderr, flush=True)


class Client:
    def __init__(self, config, timeout=45):
        self.config = config
        self.timeout = timeout
        self.server = config["server"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.server)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Error("server must be an HTTP(S) address without credentials, query or fragment")
        self.credentials = read_json(config["server_config"])
        self.token = self.credentials.get("operator_token", self.credentials.get("admin_token"))
        if not isinstance(self.token, str) or not self.token:
            raise Error("Private credentials require operator_token or admin_token")
        # Never forward an administrator credential through an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect(), https_handler())

    def api(self, path, payload=None, timeout=None):
        budget = min(10, self.timeout if timeout is None else timeout)
        deadline = time.monotonic() + budget
        cancelled = threading.Event()
        outcome = queue.Queue(maxsize=1)

        def perform():
            try:
                outcome.put((True, self._api(path, payload, budget, deadline, cancelled)))
            except Exception as exc:
                outcome.put((False, exc))

        # Socket timeouts alone are idle timeouts: trickling bytes (or DNS) can
        # exceed them. Bound the caller separately and never replay this POST.
        threading.Thread(target=perform, name="ottplay-http", daemon=True).start()
        try:
            ok, value = outcome.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            raise TransportError("Timed out waiting for the server response") from None
        if time.monotonic() >= deadline:
            cancelled.set()
            raise TransportError("Timed out waiting for the server response")
        if not ok:
            raise value
        return value

    def _api(self, path, payload, budget, deadline, cancelled):
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": "ottplay-cli/1.0"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.server + path, data=body, headers=headers)
        try:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise TransportError("Timed out waiting for the server response")
            with self.opener.open(request, timeout=budget) as response:
                body = bytearray()
                while True:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise TransportError("Timed out waiting for the server response")
                    # read1 returns available bytes instead of waiting for the
                    # entire body, letting a timed-out trickle worker retire.
                    chunk = response.read1(min(65536, 2 * 1024 * 1024 + 1 - len(body)))
                    if not chunk:
                        # Unlike read(), read1() can return EOF before the
                        # advertised Content-Length without IncompleteRead.
                        remaining = getattr(response, "length", None)
                        if isinstance(remaining, int) and remaining > 0:
                            raise TransportError("The server closed the connection before sending the complete response")
                        return response.status, decode_response_json(body)
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise Error("The server response exceeds the size limit")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise HTTPError(code) from None
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError("Server unavailable; check the address and connection") from exc
        except (ValueError, RecursionError) as exc:
            raise Error("The server returned invalid JSON") from exc

    def device(self, name):
        aliases = self.config.get("players", {})
        matches = [value for key, value in aliases.items() if key.casefold() == name.casefold()]
        if len(matches) == 1:
            return matches[0]
        ids = [row["id"] for row in self.credentials.get("devices", []) if row["id"].casefold() == name.casefold()]
        if len(ids) == 1:
            return ids[0]
        raise Error(f"Unknown player {name!r}. Run ott devices or ott add NAME UUID")

    def call(self, device, action, params, idempotency_key=None):
        """Preserve the legacy data-only API for existing commands."""
        if idempotency_key is None:
            return self.call_receipt(device, action, params)["data"]
        return self.call_receipt(device, action, params, idempotency_key=idempotency_key)["data"]

    def call_receipt(self, device, action, params, idempotency_key=None):
        """Submit once and retain the request identity, including read failures."""
        request_id = None
        status = "unknown"
        try:
            query = "?" + urllib.parse.urlencode({"device_id": device})
            deadline = time.monotonic() + self.timeout
            try:
                payload = {"action": action, "params": params}
                if idempotency_key is not None:
                    payload["idempotency_key"] = idempotency_key
                _, queued = self.api("/api/requests" + query, payload, timeout=self.timeout)
            except Error as exc:
                # A lost POST response cannot prove whether the mutation was queued.
                # Replaying it would give a relative-volume command a second ID.
                if isinstance(exc, HTTPError) and exc.code < 500:
                    raise
                raise Error(str(exc) + ". The request may have been accepted; do not repeat the change blindly.") from exc
            if not isinstance(queued, dict) or not isinstance(queued.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", queued["id"]):
                raise Error("The server did not return a request ID. The request may have been accepted; do not repeat the change blindly.")
            request_id = queued["id"]
            try:
                receipt = self._wait_receipt(query, request_id, deadline)
            except Error as exc:
                exc.request_id = request_id
                if isinstance(exc, PlayerRejected):
                    status = "rejected"
                elif isinstance(exc, PlayerUnsupported):
                    status = "unsupported"
                raise
            status = "ok"
            return receipt
        finally:
            callback = getattr(self, "on_receipt", None)
            if callable(callback):
                metadata = {"action": action if isinstance(action, str) and action in RECEIPT_ACTIONS else "unknown",
                            "request_id": request_id, "status": status}
                try:
                    callback(metadata)
                except Exception:
                    # Output failure cannot replay or change a completed operation.
                    pass

    def _wait_receipt(self, query, request_id, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.8, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                status, result = self.api("/api/requests" + query + "&id=" + request_id, timeout=remaining)
            except TransportError:
                continue
            except HTTPError as exc:
                if exc.code in (502, 503, 504):
                    continue
                if exc.code == 404:
                    raise Error("The request receipt has expired or is missing. The request may have been executed; do not repeat the change blindly.") from exc
                raise Error(f"The request receipt could not be read (HTTP {exc.code}). The request may have been executed; do not repeat the change blindly.") from exc
            except Error as exc:
                raise Error(str(exc) + ". The request may have been executed; do not repeat the change blindly.") from exc
            if time.monotonic() >= deadline:
                break
            if status == 202:
                if not isinstance(result, dict) or result.get("status") != "pending":
                    raise Error("The server returned an invalid pending status. The request may have been executed; do not repeat the change blindly.")
                continue
            if status != 200 or not isinstance(result, dict) or result.get("id") != request_id or result.get("status") not in ("ok", "rejected", "unsupported") or not isinstance(result.get("data"), dict):
                raise Error("The server returned an invalid player response. The request may have been executed; do not repeat the change blindly.")
            if result.get("status") != "ok":
                data = result["data"]
                matches = data.get("matches", [])
                if not isinstance(matches, list) or not all(isinstance(row, dict) for row in matches):
                    raise Error("The player rejected the request but returned an invalid match list")
                details = "\n".join(f"{row.get('number', row.get('index', ''))}: {clean(row.get('name', ''))}" for row in matches)
                message = clean(data.get("error", "The player rejected the request")) + ("\n" + details if details else "")
                if result["status"] == "rejected":
                    raise PlayerRejected(message, data)
                raise PlayerUnsupported(message, data)
            return {"request_id": request_id, "status": "ok", "data": result["data"]}
        raise Error("The player did not respond. Open it, check the address/access code and use a version that supports the CLI. The request may still execute before its TTL expires; do not repeat the change blindly.")


def clean(value):
    # Provider-controlled names must not send terminal control/escape sequences.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))


def channel_identity(value):
    # Legacy channel IDs can be strings or JavaScript safe integers.
    if isinstance(value, str) and value and epg_text(value, 512):
        return value
    if type(value) is int and -9007199254740991 <= value <= 9007199254740991:
        return str(value)
    return None


def play_channel(client, device, params, timeout=45):
    previous_timeout = client.timeout
    try:
        return _play_channel(client, device, params, time.monotonic() + timeout)
    finally:
        client.timeout = previous_timeout


def _play_channel(client, device, params, deadline):
    text = params["query"].strip()
    if not text or re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
        return client.call(device, "play", params), None
    exact_id = text[3:] if text.casefold().startswith("id:") else None
    if exact_id is not None and not media_text(exact_id, 2048):
        raise Error("Use play id:VIDEO_ID with an exact nonempty ID from the selected category")
    response = read_media_channels(client, device, "" if exact_id is not None else text, deadline,
                                   require_modern=exact_id is not None)
    modern = response.get("version") == 1 if isinstance(response, dict) else False
    matches = response.get("channels") if isinstance(response, dict) else None
    invalid = "The player returned an invalid channel list; no switch was requested"
    if not isinstance(matches, list):
        raise Error(invalid)
    previous, identities, rows = 0, set(), []
    for row in matches:
        if not isinstance(row, dict):
            raise Error(invalid)
        number, name = row.get("number"), row.get("name")
        identity = row.get("id") if modern and media_text(row.get("id"), 2048) else channel_identity(row.get("id"))
        if (type(number) is not int or not previous < number <= 9007199254740991
                or identity is None or identity in identities
                or not epg_text(name, 16384) or not name.strip()
                or (exact_id is None and (media_search_key(text) not in media_search_key(name) if modern
                                          else text.casefold() not in name.casefold()))):
            raise Error(invalid)
        previous = number
        identities.add(identity)
        if exact_id is None or row["id"] == exact_id:
            rows.append({key: row[key] for key in ("id", "number", "name")})
    data = dict(response, channels=rows) if modern else {"channels": rows}
    if modern and response["loading"] and exact_id is None:
        error = Error("The media catalog is still loading; no playback was requested. List categories again when loading finishes.")
        data["playback"] = {"error": str(error)}
        return data, error
    if not rows:
        error = Error("No channels match the search; no switch was requested")
        data["playback"] = {"error": str(error)}
        return data, error
    exact = [row for row in rows if exact_id is not None or media_search_key(row["name"]) == media_search_key(text)]
    selected = exact[0] if len(exact) == 1 else None
    try:
        # One mutation follows the complete read-only search. Never replay it.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Channel search exceeded --timeout; no playback was requested")
        client.timeout = remaining
        if selected is None:
            controls = random_controls(client, device, deadline)
            catalog = response["catalog"] if modern else random_catalog(client, device, controls, deadline)
            result = random_play(client, device, catalog, {"search": text}, deadline, exact or rows)
            selected = result["channel"]
        else:
            result = (media_request(client, device, "play_catalog", {"catalog": response["catalog"], "id": selected["id"]}, deadline)
                      if modern else client.call(device, "play", {"query": str(selected["number"])}))
        channel = result.get("channel") if isinstance(result, dict) else None
        if (not isinstance(channel, dict) or result.get("dispatched") is not True
                or type(channel.get("number")) is not int or channel["number"] != selected["number"]
                or (channel.get("id") != selected["id"] if modern
                    else channel_identity(channel.get("id")) != channel_identity(selected["id"]))
                or channel.get("name") != selected["name"]):
            raise Error("The player did not confirm the requested channel switch. The request may have executed; do not repeat the change blindly.")
        data["playback"] = {"dispatched": True, "channel": {key: channel[key] for key in ("id", "number", "name")}}
        return data, None
    except Error as exc:
        data["playback"] = {"error": str(exc)}
        return data, exc


def select_programme(programs):
    # Validate every candidate before drawing: the random row may be any row.
    invalid = "The player returned an invalid programme list; no switch was requested"
    if not isinstance(programs, list):
        raise Error(invalid)
    previous = (0, 0)
    for row in programs:
        order = (row.get("number", 0), row.get("start", 0) if row.get("mode") == "archive" else 0) if isinstance(row, dict) else (0, 0)
        if (not isinstance(row, dict) or type(row.get("number")) is not int
                or not 0 < row["number"] <= 9007199254740991
                or type(order[1]) not in (int, float) or not previous < order
                or (row.get("mode") == "archive" and
                    (type(row.get("start")) is not int or type(row.get("end")) is not int
                     or not time.time() - 144 * 3600 <= row["start"] < row["end"] <= time.time()))
                or any(not epg_text(row.get(key), 16384) or not row[key].strip()
                       for key in ("channel", "title"))):
            raise Error(invalid)
        previous = order
    return (secrets.choice(programs) if len(programs) > 1 else programs[0]) if programs else None


def parse_app_update(words):
    op = words[0].casefold() if words else "status"
    if op == "status" and len(words) <= 1:
        return {"operation": "status"}
    if op == "install" and len(words) == 2 and re.fullmatch(r"[a-f0-9]{64}", words[1]):
        return {"operation": op, "sha256": words[1]}
    if op == "prepare" and len(words) == 3 and re.fullmatch(r"[a-f0-9]{64}", words[2]):
        try:
            url = urllib.parse.urlsplit(words[1])
            valid = (len(words[1]) <= 2048 and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in words[1])
                     and url.scheme == "https" and url.hostname and url.username is None and url.password is None
                     and not url.fragment and (url.port is None or 0 < url.port <= 65535))
        except ValueError:
            valid = False
        if valid:
            return {"operation": op, "url": words[1], "sha256": words[2]}
    raise Error("Use update status, update prepare HTTPS_APK_URL SHA256, or update install SHA256")


def app_update_metadata(data, params):
    invalid = "Invalid Capacitor update result; inspect update status before retrying"
    if not isinstance(data, dict):
        raise Error(invalid)
    if params["operation"] == "install":
        if data.get("accepted") is not True or data.get("operation") != "install":
            raise Error(invalid)
        return {"accepted": True, "operation": "install"}
    phases = ("idle", "downloading", "ready", "awaiting_permission", "installing", "awaiting_confirmation", "installed", "failed")
    if (type(data.get("version")) is not int or data["version"] != 1 or data.get("phase") not in phases
            or not isinstance(data.get("sha256"), str) or not re.fullmatch(r"(?:[a-f0-9]{64})?", data["sha256"])
            or any(type(data.get(key)) is not int or not 0 <= data[key] <= 2100000000 for key in ("installed_code", "target_code"))
            or any(not isinstance(data.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_.+\-]{0,64}", data[key]) for key in ("installed_version", "target_version", "error"))
            or type(data.get("can_request_installs")) is not bool or data.get("user_confirmation_required") is not True):
        raise Error(invalid)
    keys = ("version", "phase", "sha256", "installed_version", "installed_code", "target_version", "target_code", "error", "can_request_installs", "user_confirmation_required")
    return {key: data[key] for key in keys}


def parse_channel_list(words):
    options = {}
    while words:
        if words[0] == "--":
            words = words[1:]
            break
        if words[0] not in ("--limit", "--offset"):
            break
        key = words[0][2:]
        if key in options or len(words) < 2 or not re.fullmatch(r"[0-9]{1,7}", words[1]):
            raise Error("Use s --limit N [--offset N] [TEXT] with each whole-number option once")
        value = int(words[1])
        minimum, maximum = (1, MAX_CATEGORY_PAGE_SIZE) if key == "limit" else (0, MAX_CATEGORY_TOTAL)
        if not minimum <= value <= maximum:
            raise Error(f"--{key} must be between {minimum} and {maximum}")
        options[key] = value
        words = words[2:]
    if "offset" in options and "limit" not in options:
        raise Error("Use --offset together with --limit to request one media page")
    if "limit" in options:
        options.setdefault("offset", 0)
    return dict(options, search=" ".join(words).strip())


def parse_command(words):
    if not words:
        return "status", {}
    verb, tail = command_verb(words), words[1:]
    text = " ".join(tail)
    # Reserve numeric-looking offsets while preserving names such as +HD.
    signed_head = re.match(r"[+-][+-]*\s*\.?(.)", verb)
    if verb in ("+", "-") or (signed_head and signed_head[1].isnumeric()):
        if tail or not re.fullmatch(r"[+-][0-9]+", verb):
            raise Error("Use +N or -N alone with a whole-number channel offset; use play TITLE for a signed channel name")
        digits = verb[1:].lstrip("0") or "0"
        if len(digits) > 16 or not 0 < int(digits) <= 9007199254740991:
            raise Error("Channel offset must be nonzero and between -9007199254740991 and +9007199254740991")
        offset = int(digits) * (-1 if verb[0] == "-" else 1)
        return "playback", {"operation": "step_channel", "offset": offset}
    if verb == "favorites":
        return "favorites", parse_favorites(tail)
    if verb == "notifications":
        if not tail or (len(tail) == 1 and tail[0].casefold() in ("status", "get")):
            return "notifications", {"operation": "get"}
        if len(tail) == 1 and tail[0].casefold() in ("on", "off"):
            return "notifications", {"operation": "set", "enabled": tail[0].casefold() == "on"}
        raise Error("Use notifications [status|on|off]; this controls banners and camera media together")
    if verb in ("category", "categories"):
        return "categories", parse_categories(tail, singular=verb == "category")
    if verb == "update":
        return "app_update", parse_app_update(tail)
    if verb == "kiosk":
        mode = tail[0].casefold() if tail else "status"
        values = tail[1:]
        strict = "--strict" in values
        if values.count("--strict") > 1 or (strict and mode not in ("on", "set")):
            raise Error("Use --strict only with kiosk on or kiosk set")
        values = [value for value in values if value != "--strict"]
        if mode not in ("status", "on", "off", "set") or (mode in ("status", "off") and values) or (mode == "set" and not values):
            raise Error("Use kiosk [status], kiosk on [--strict] [CHANNEL], kiosk set [--strict] CHANNEL or kiosk off")
        params = {"mode": mode}
        if strict:
            params["strict"] = True
        if values:
            query = " ".join(values)
            try:
                valid = 0 < len(query.strip().encode("utf-8")) <= 1024 and all(ord(c) >= 32 and ord(c) != 127 for c in query)
            except UnicodeEncodeError:
                valid = False
            if not valid:
                raise Error("Kiosk channel must contain 1–1024 UTF-8 bytes without control characters")
            params["query"] = query.strip()
        return "kiosk", params
    if verb == "load":
        if len(tail) != 1 or not preset_name(tail[0]):
            raise Error("Use load PRESET with a short preset name")
        return "load", {"preset": tail[0]}
    if verb == "profiles":
        if tail:
            raise Error("Use profiles without arguments")
        return "profiles", {}
    if verb == "profile" and tail and tail[0].casefold() in ("save", "load", "export", "import"):
        operation = tail[0].casefold()
        if len(tail) < 2 or not re.fullmatch(r"[1-9]|1[0-5]", tail[1]):
            raise Error("Profile number must be an integer from 1 to 15")
        params = {"number": int(tail[1])}
        if operation == "load" and len(tail) == 2:
            return "profile", params
        if operation == "save":
            if len(tail) > 2:
                params["name"] = " ".join(tail[2:])
                validate_profile_settings({"name": params["name"]})
            return "profile_save", params
        if operation in ("export", "import") and len(tail) == 3 and tail[2]:
            if operation == "export":
                params["output"] = tail[2]
            else:
                params["profile"] = offline_profile_document(read_settings_json(tail[2], "Local videos profile"))
            return "profile_" + operation, params
        raise Error("Use profile save N [NAME], profile load N, profile export N FILE or profile import N FILE")
    if verb in ("profile", "profile-config"):
        if not tail or not re.fullmatch(r"[1-9]|1[0-5]", tail[0]):
            raise Error("Profile number must be an integer from 1 to 15")
        number = int(tail[0])
        if verb == "profile-config":
            if len(tail) != 2:
                raise Error("Use profile-config N FILE.json")
            settings = read_profile_settings(tail[1])
        elif len(tail) == 1:
            return "profile", {"number": number}
        else:
            field = PROFILE_FIELD_ALIASES.get(tail[1].casefold())
            if field is None or len(tail) < 3 or (field != "name" and len(tail) != 3):
                raise Error("Use profile N [url/playlist URL | history/history_hours HOURS | vp/vportal LINK | n/name NAME]")
            value = " ".join(tail[2:])
            if field == "history_hours":
                if not re.fullmatch(r"0|[1-9][0-9]{0,3}", value):
                    raise Error("Profile history must be an integer from 0 to 8760 hours")
                value = int(value)
            settings = {field: value}
        validate_profile_settings(settings)
        params = {"number": number, "settings": settings}
        if len(json.dumps({"action": "profile_settings", "params": params}, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
            raise Error("Profile settings exceed the 16 KiB request limit")
        return "profile_settings", params
    if verb == "screenshot":
        if not tail:
            return "screenshot", {}
        if len(tail) == 2 and tail[0] in ("-o", "--output") and tail[1]:
            return "screenshot", {"output": tail[1]}
        raise Error("Use screenshot/shot [-o/--output FILE.png]")
    if verb == "restart":
        target = tail[0].casefold() if len(tail) == 1 else "player"
        target = {"s": "stream", "p": "player", "a": "app", "application": "app"}.get(target, target)
        if len(tail) > 1 or target not in ("stream", "player", "app"):
            raise Error("Use restart [stream|player|app]; reboot device is a separate operation")
        if target == "app":
            return "lifecycle", {"operation": "restart_app"}
        return "restart", {"target": target}
    if verb == "capabilities":
        if tail:
            raise Error("Use capabilities or caps without arguments")
        return "capabilities", {}
    if verb == "aspect":
        value = text.casefold()
        if not tail or value in ("get", "status"):
            return "aspect", {"operation": "get"}
        mode = {"fit to screen": "fit", "fill screen": "fill"}.get(value, value)
        if mode not in ASPECT_MODES:
            raise Error('Use aspect [get|status|fit|fill]; modes also accept "Fit to screen" or "Fill screen"')
        return "aspect", {"operation": "set", "mode": mode}
    if verb in ("reload", "reboot", "exit", "standby", "wake"):
        if tail and not (verb in ("reload", "reboot") and len(tail) == 1 and
                         tail[0].casefold() == ("player" if verb == "reload" else "device")):
            raise Error("Use reload [player], reboot [device], exit, standby or go (aliases: stop, start, wake) without extra arguments")
        operation = {"reload": "reload_player", "reboot": "reboot_device", "exit": "exit_app"}.get(verb, verb)
        return "lifecycle", {"operation": operation}
    if verb == "input":
        key = tail[0].casefold() if len(tail) == 1 else ""
        key = INPUT_ALIASES.get(key, key)
        if key not in INPUT_KEYS:
            raise Error("Use key/input with one supported named key; run caps for available inputs")
        return "input", {"key": key}
    if verb in CHANNEL_STEPS:
        if tail:
            raise Error("Use prev/previous or next without arguments")
        return "playback", {"operation": CHANNEL_STEPS[verb]}
    if verb in ("pause", "resume", "seek"):
        if verb != "seek":
            if tail:
                raise Error("Use " + verb + " without arguments")
            return "playback", {"operation": verb}
        if len(tail) != 1 or not re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", tail[0]):
            raise Error("Use seek SECONDS with an absolute nonnegative position")
        position = float(tail[0])
        if not math.isfinite(position) or not 0 <= position <= 9007199254740991:
            raise Error("Seek position must be finite and between 0 and 9007199254740991 seconds")
        return "playback", {"operation": "seek", "position": position}
    if verb in ("vp", "vpr"):
        listing = listing_option(words)
        text = " ".join(tail[1:] if listing else tail).strip()
        try:
            length = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise Error("VPortal search must be valid UTF-8 text") from None
        if not 1 <= length <= 1024:
            raise Error("Use vp TEXT, vpr TEXT or vp --list TEXT; the search must contain 1–1024 UTF-8 bytes")
        action = "vportal_search" if listing else "vportal_random" if verb == "vpr" else "vportal"
        return action, {"query": text}
    if verb == "s":
        return "channels", parse_channel_list(tail)
    if verb == "p":
        text = " ".join(tail[1:] if listing_option(words) else tail).strip()
        return "programs", {"search": text}
    if verb == "v":
        if not tail:
            return "status", {}
        if len(tail) != 1 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            raise Error("Use v, v 35, v +5 or v -5")
        value = float(text)
        relative = text.startswith(("+", "-"))
        if not math.isfinite(value) or (not relative and not 0 <= value <= 100):
            raise Error("Absolute volume must be between 0 and 100")
        return "command", {"command": "set_volume", "volume_step" if relative else "volume": value}
    if verb in ("status", "providers"):
        if tail:
            raise Error("Use " + verb + " without arguments")
        return verb, {}
    if verb == "provider":
        if not tail:
            raise Error("provider requires a provider ID, index or name")
        return "provider", {"query": text}
    if verb == "provider-config":
        if len(tail) != 1:
            raise Error("Use provider-config FILE.json")
        data = read_settings_json(tail[0], "provider")
        if not isinstance(data, dict) or set(data) != {"provider", "settings"} or not isinstance(data["settings"], dict):
            raise Error('Expected {"provider":"xtream","settings":{...}}')
        if data["provider"] == "plex":
            data["settings"] = validate_plex_settings(data["settings"])
        elif data["provider"] == "vportal":
            settings = data["settings"]
            if (not {"profile", "vportal"} <= set(settings) or set(settings) - {"profile", "vportal", "name"}
                    or type(settings["profile"]) is not int or not 1 <= settings["profile"] <= 15):
                raise Error("VPortal settings require profile 1–15, vportal and optional name")
            validate_profile_settings({key: value for key, value in settings.items() if key != "profile"})
        return "provider_settings", data
    if verb == "plex":
        operation = tail[0].casefold() if tail else ""
        if operation in ("libraries", "library", "shuffle"):
            return "plex_library", parse_plex_library(tail)
        operation = {"queue": "status", "prev": "previous"}.get(operation, operation)
        if operation in PLEX_QUEUE_OPERATIONS:
            ids = tail[1:]
            shuffle = operation == "play" and ids[:1] == ["--shuffle"]
            if shuffle:
                ids = ids[1:]
            if operation in ("play", "preview"):
                if not plex_queue_ids(ids, 1):
                    raise Error("Use plex play/preview with 1–500 positive decimal Plex IDs (no leading zeros, URLs or titles)")
                if shuffle:
                    secrets.SystemRandom().shuffle(ids)
                return "plex_queue", {"op": operation, "ids": ids}
            if ids:
                raise Error("Use plex queue/status, next, prev/previous or stop without extra arguments")
            return "plex_queue", {"op": operation}
        return "provider_settings", {"provider": "plex", "settings": parse_plex_settings(tail)}
    if verb == "playlist":
        if len(tail) != 1:
            raise Error("Use playlist URL")
        return "provider_settings", {"provider": "m3u", "settings": {"playlist": text}}
    if verb == "msg":
        if not tail:
            raise Error("message requires text")
        return "command", {"command": "popup_message", "message": text}
    if verb in ("random", "rr"):
        params = {"command": "random_channel"}
        if verb == "rr":
            params["random_mode"] = "adult"
        if tail:
            if (len(tail) != 2 or not all(len(x) <= 16 and re.fullmatch(r"[1-9]\d*", x) for x in tail)
                    or not 0 < int(tail[0]) <= int(tail[1]) <= 9007199254740991):
                raise Error("Use random or random FROM TO (one-based channel numbers)")
            params["random_range"] = [int(x) for x in tail]
        return "command", params
    if verb == "play":
        if not tail:
            raise Error("play requires a channel number or name")
        return "play", {"query": text}
    return "play", {"query": " ".join(words)}


def read_settings_json(path, kind):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate setting")
            result[key] = value
        return result
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            raw = stream.read(16 * 1024 + 1)
        if len(raw.encode("utf-8")) > 16 * 1024:
            raise ValueError("Oversized settings")
        return json.loads(raw, object_pairs_hook=unique_fields)
    except (OSError, ValueError):
        raise Error(f"Could not read {kind} settings: use a valid JSON object without duplicate fields, at most 16 KiB") from None


def read_profile_settings(path):
    return read_settings_json(path, "profile")


def plex_token(path=None):
    if path is not None:
        try:
            with Path(path).expanduser().open(encoding="utf-8") as stream:
                token = stream.read(4097)
            if len(token.encode("utf-8")) > 4096:
                raise ValueError()
            return token
        except (OSError, ValueError):
            raise Error("Could not read Plex token file: use a UTF-8 file containing one token") from None
    try:
        # Refuse getpass's echoing fallback on terminals without hidden input.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("Plex token: ")
    except (EOFError, OSError, getpass.GetPassWarning):
        raise Error("Hidden token input is unavailable; use a token file or provider-config JSON") from None


def parse_plex_settings(words):
    command = words[0].casefold() if words else ""
    values = words[1:]
    if command == "setup" and (len(values) == 1 or (len(values) == 2 and values[1] != "--token-file")
                               or (len(values) == 3 and values[1] == "--token-file")):
        # Validate the address before asking for a credential.
        settings = validate_plex_settings({"server": values[0]})
        settings["token"] = (plex_token(values[2]) if len(values) == 3 else
                             values[1] if len(values) == 2 else plex_token())
    elif command == "server" and len(values) == 1:
        settings = {"server": values[0]}
    elif command == "token" and len(values) <= 1:
        settings = {"token": values[0] if values else plex_token()}
    elif command == "token-file" and len(values) == 1:
        settings = {"token": plex_token(values[0])}
    else:
        raise Error("Use plex setup URL [TOKEN|--token-file FILE], plex server URL, plex token [TOKEN], or plex token-file FILE")
    return validate_plex_settings(settings)


def validate_plex_settings(settings):
    if not isinstance(settings, dict) or not settings or set(settings) - {"server", "token"}:
        raise Error("Plex settings must contain server and/or token")
    result = {}
    for key, value in settings.items():
        if not isinstance(value, str):
            raise Error("Plex settings must contain text values")
        value = value.strip()
        if not epg_text(value, 8192 if key == "server" else 1024):
            raise Error("Plex settings must be text: server up to 8192 characters, token up to 1024 characters")
        if key == "server":
            value = value.rstrip("/")
            try:
                parsed = urllib.parse.urlsplit(value)
                valid = (re.fullmatch(r"https?://(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?(?:/[a-z0-9_./~-]*)?", value, re.I)
                         and not re.search(r"(?:^|/)\.\.(?:/|$)", value)
                         and parsed.hostname and (parsed.port is None or 1 <= parsed.port <= 65535))
            except ValueError:
                valid = False
            if not valid:
                raise Error("Use a Plex HTTP(S) server address without credentials, query, fragment or parent path segments")
        elif not value or re.search(r"[\s\x00-\x1f\x7f]", value):
            raise Error("Plex token must be nonempty and contain no whitespace or control characters")
        result[key] = value
    return result


def plex_settings_metadata(data, settings):
    if (not isinstance(data, dict) or data.get("provider") != "plex" or data.get("saved") is not True
            or not isinstance(data.get("fields"), list) or len(data["fields"]) != len(settings)
            or any(not isinstance(field, str) for field in data["fields"])
            or set(data["fields"]) != set(settings)):
        raise Error("The player did not confirm saving Plex settings. The request may have executed; do not repeat the change blindly.")
    return {"provider": "plex", "saved": True, "fields": data["fields"]}


def validate_profile_settings(settings):
    if (not isinstance(settings, dict) or not settings
            or set(settings) - {"name", "playlist", "history_hours", "vportal"}):
        raise Error("Profile settings must contain only name, playlist, history_hours or vportal, with at least one field")
    for key, value in settings.items():
        if key == "history_hours":
            if type(value) is not int or not 0 <= value <= 8760:
                raise Error("Profile history must be an integer from 0 to 8760 hours")
        elif (not epg_text(value, 256 if key == "name" else 8192, "utf-8")
              or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            raise Error("Profile settings must be UTF-8 text without control characters: name up to 256 bytes, playlist and VPortal links up to 8192 bytes")


def offline_profile_text(value, maximum, *, nonempty=False):
    return (epg_text(value, maximum, "utf-8")
            and all(ord(char) >= 32 and ord(char) != 127 for char in value)
            and (not nonempty or bool(value.strip())))


def offline_source_name(value, maximum, *, relative=False):
    if (not offline_profile_text(value, maximum, nonempty=True) or "\\" in value
            or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", value)):
        return False
    if not relative:
        return "/" not in value and value not in (".", "..")
    return not value.startswith("/") and all(part not in ("", ".", "..") for part in value.split("/"))


def offline_profile_document(value):
    """Portable description only: authority and actual media stay on the player."""
    invalid = "Invalid Local videos profile: use a version 1 portable export without URLs, paths, media or access grants"
    if (not isinstance(value, dict)
            or set(value) != {"version", "provider", "name", "binding_id", "sources", "selected"}
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("provider") != "offline"
            or not offline_profile_text(value.get("name"), 256)
            or not isinstance(value.get("binding_id"), str)
            or not re.fullmatch(OFFLINE_BINDING_PATTERN, value["binding_id"])
            or not isinstance(value.get("sources"), list) or len(value["sources"]) > 32):
        raise Error(invalid)
    for index, source in enumerate(value["sources"]):
        if (not isinstance(source, dict) or set(source) != {"key", "name", "kind"}
                or source.get("key") != "s" + str(index)
                or source.get("kind") not in ("folder", "file")
                or not offline_source_name(source.get("name"), 512)):
            raise Error(invalid)
    selected = value["selected"]
    if selected is not None and (not isinstance(selected, dict) or set(selected) != {"source_key", "name"}
            or selected.get("source_key") not in [row["key"] for row in value["sources"]]
            or not offline_source_name(selected.get("name"), 1024, relative=True)):
        raise Error(invalid)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 12288:
        raise Error("Local videos profile exceeds the 12 KiB document limit")
    # Every export must also fit back into the bounded import request envelope.
    request = {"action": "profile_import", "params": {"number": 15, "profile": value}}
    if len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
        raise Error("Local videos profile exceeds the 16 KiB request limit")
    return value


def offline_profile_metadata(row, fail):
    keys = {"number", "name", "active", "configured", "binding_required", "source_count", "selected_name"}
    if (not isinstance(row, dict) or set(row) != keys
            or type(row.get("number")) is not int or not 1 <= row["number"] <= 15
            or not offline_profile_text(row.get("name"), 256)
            or any(type(row.get(key)) is not bool for key in ("active", "configured", "binding_required"))
            or type(row.get("source_count")) is not int or not 0 <= row["source_count"] <= 32
            or not offline_profile_text(row.get("selected_name"), 1024)
            or (not row["configured"] and (row["active"] or row["source_count"] or row["selected_name"] or row["binding_required"]))
            or (row["selected_name"] and not row["source_count"])):
        fail()
    return dict(row)


def offline_profile_command(client, device, action, params, timeout):
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    sent = False
    operation = action.removeprefix("profile_")
    try:
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        if operation not in controls.get("offline_profiles", {}).get("operations", []):
            raise Error("Local videos profile operation is unavailable. Select provider offline, check caps and local restrictions, and update the player/controller. No profile command was sent")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Profile capability discovery exceeded --timeout; no profile command was sent")
        client.timeout = remaining
        wire = {key: value for key, value in params.items() if key != "output"}
        sent = True
        data = client.call(device, action, wire)
        if time.monotonic() >= deadline:
            raise Error("Profile response exceeded --timeout. The request may have executed; it was not repeated")
        if action != "profile_export":
            return profile_metadata(data, action, params, expected_provider="offline")
        if (not isinstance(data, dict) or set(data) != {"provider", "number", "profile"}
                or data.get("provider") != "offline" or type(data.get("number")) is not int
                or data["number"] != params["number"]):
            raise Error("The player returned an invalid Local videos profile export")
        document = offline_profile_document(data["profile"])
        path = Path(params["output"]).expanduser()
        try:
            write_private(path, document, exclusive=True)
        except FileExistsError:
            raise Error("Export file already exists; choose a new path. The existing file was not changed") from None
        except OSError:
            raise Error("Could not create the Local videos export file") from None
        return {"provider": "offline", "number": params["number"], "path": str(path.resolve()), "exported": True}
    except (PlayerRejected, PlayerUnsupported):
        outcome = "The request was not repeated" if sent else "No profile command was sent"
        raise Error("Local videos profile request was rejected or unsupported; check provider offline, capabilities and local permissions. " + outcome) from None
    except HTTPError as exc:
        if exc.code in (400, 404):
            raise Error("This controller does not accept Local videos profile requests; update the controller and player. The request was not repeated") from None
        raise
    finally:
        client.timeout = previous_timeout


def profile_metadata(data, action, params, *, expected_provider=None):
    message = "The player returned invalid profile metadata"
    if action != "profiles":
        message += ". The request may have executed; do not repeat the change blindly."
    def fail():
        raise Error(message)
    provider = data.get("provider") if isinstance(data, dict) else None
    def profile(row):
        if provider == "offline":
            return offline_profile_metadata(row, fail)
        keys = ("number", "name", "active", "history_hours", "playlist_configured")
        if provider == "vportal":
            keys += ("vportal_configured",)
        if (not isinstance(row, dict) or type(row.get("number")) is not int or not 1 <= row["number"] <= 15
                or not epg_text(row.get("name"), 256, "utf-8")
                or any(type(row.get(key)) is not bool for key in ("active", "playlist_configured"))
                or ((provider == "vportal" or "vportal_configured" in row)
                    and type(row.get("vportal_configured")) is not bool)
                or "history_hours" not in row or (row["history_hours"] is not None
                    and (type(row["history_hours"]) is not int or not 0 <= row["history_hours"] <= 8760))):
            fail()
        return {key: row[key] for key in keys}
    if (not isinstance(data, dict) or data.get("provider") not in ("m3u", "vportal", "offline")
            or (expected_provider is not None and data.get("provider") != expected_provider)):
        fail()
    if action == "profiles":
        if not isinstance(data.get("profiles"), list) or len(data["profiles"]) != 15:
            fail()
        rows = [profile(row) for row in data["profiles"]]
        if (any(row["number"] != number for number, row in enumerate(rows, 1))
                or sum(row["active"] for row in rows) not in ((0, 1) if provider == "offline" else (1,))):
            fail()
        return {"provider": data["provider"], "profiles": rows}
    row = profile(data.get("profile"))
    if row["number"] != params["number"]:
        fail()
    if action == "profile":
        if data.get("dispatched") is not True or row["active"] is not True:
            fail()
        result = {"provider": data["provider"], "profile": row, "dispatched": True}
        if provider == "offline":
            if type(data.get("binding_required")) is not bool or data["binding_required"] != row["binding_required"]:
                fail()
            result["binding_required"] = data["binding_required"]
        return result
    if data.get("saved") is not True:
        fail()
    if action in ("profile_save", "profile_import"):
        if (provider != "offline" or row["configured"] is not True
                or type(data.get("binding_required")) is not bool or data["binding_required"] != row["binding_required"]):
            fail()
        expected_name = params.get("name") if action == "profile_save" else params["profile"]["name"]
        if expected_name is not None and row["name"] != expected_name:
            fail()
        if action == "profile_import":
            document = params["profile"]
            selected = document["selected"]
            if row["source_count"] != len(document["sources"]) or row["selected_name"] != (selected["name"] if selected else ""):
                fail()
        return {"provider": provider, "profile": row, "saved": True, "binding_required": row["binding_required"]}
    if provider == "offline":
        fail()
    for key, value in params["settings"].items():
        if key == "vportal" and provider != "vportal":
            fail()
        if key in ("name", "history_hours"):
            if row[key] != value:
                fail()
        elif row[key + "_configured"] != bool(value):
            fail()
    return {"provider": data["provider"], "profile": row, "saved": True}


def vportal_profile_command(client, device, params, timeout):
    """Require standalone VPortal before sending link settings."""
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    sent = False
    try:
        profiles = profile_metadata(client.call(device, "profiles", {}), "profiles", {})
        if profiles["provider"] != "vportal":
            raise Error("VPortal links belong to standalone VPortal profiles. Select provider vportal first; no profile settings were sent")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Profile discovery exceeded --timeout; no profile settings were sent")
        client.timeout = remaining
        sent = True
        data = client.call(device, "profile_settings", params)
        if time.monotonic() >= deadline:
            raise Error("Profile response exceeded --timeout. The request may have executed; it was not repeated")
        return profile_metadata(data, "profile_settings", params, expected_provider="vportal")
    except (PlayerRejected, PlayerUnsupported):
        outcome = "The request was not repeated" if sent else "No profile settings were sent"
        raise Error("VPortal profile settings are unavailable; select provider vportal and check its settings. " + outcome) from None
    finally:
        client.timeout = previous_timeout


def vportal_settings_metadata(data, params, write=False):
    key = "profile" if write else "number"
    number = params["settings"]["profile"] if write else params["number"]
    required = {"provider", key, "runtime", "context", "revision"}
    required |= {"fields", "saved", "reloaded", "reload_required"} if write else {"matched"}
    valid = (isinstance(data, dict) and set(data) == required and data["provider"] == "vportal"
             and type(data[key]) is int and data[key] == number and data["runtime"] == params["runtime"]
             and all(isinstance(data[field], str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", data[field])
                     for field in ("context", "revision")))
    if write:
        valid = (valid and data["saved"] is True and data["context"] == params["context"]
                 and type(data["reloaded"]) is bool and type(data["reload_required"]) is bool
                 and not (data["reloaded"] and data["reload_required"])
                 and isinstance(data["fields"], list) and len(data["fields"]) == len(params["settings"])
                 and all(isinstance(field, str) for field in data["fields"])
                 and set(data["fields"]) == set(params["settings"]))
    else:
        valid = valid and type(data["matched"]) is bool
    if not valid:
        raise Error("The player did not confirm the exact VPortal settings operation; the request was not repeated")
    return data


def vportal_compare(client, device, row):
    """Read the dedicated slot and capture a source-bound write receipt."""
    deadline, previous = time.monotonic() + client.timeout, client.timeout
    try:
        caps = capabilities_metadata(client.call(device, "capabilities", {}))
        if "vportal_settings" not in caps:
            raise Error("Dedicated VPortal settings require an updated player and controller; no VPortal settings were sent")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("VPortal discovery exceeded --timeout; no settings were sent")
        client.timeout = remaining
        params = {"provider": "vportal", "number": row["number"], "settings": row["settings"],
                  "runtime": caps["player"]["runtime"]}
        data = client.call(device, "settings_compare", params)
        if time.monotonic() >= deadline:
            raise Error("VPortal comparison exceeded --timeout; no settings were sent")
        return vportal_settings_metadata(data, params)
    except (PlayerRejected, PlayerUnsupported, HTTPError):
        raise Error("Dedicated VPortal comparison was rejected or unsupported; no settings were sent") from None
    finally:
        client.timeout = previous


def vportal_settings_command(client, device, row, session=None, label=None):
    """Save a named slot without selecting its provider, opening UI or waking it."""
    deadline, previous = time.monotonic() + client.timeout, client.timeout
    try:
        receipt = vportal_compare(client, device, row)
        if receipt["matched"]:
            if session is not None:
                session.complete(label)
            return receipt
        params = {"provider": "vportal", "settings": {"profile": row["number"], **row["settings"]},
                  **{key: receipt[key] for key in ("runtime", "context", "revision")}}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("VPortal comparison exceeded --timeout; no settings were sent")
        client.timeout = remaining
        if session is not None:
            session.retry(label, "provider_settings", params)
        data = session.call("provider_settings", params) if session is not None else client.call(device, "provider_settings", params)
        if time.monotonic() >= deadline:
            raise Error("VPortal save exceeded --timeout; inspect the slot before retrying. The request was not repeated")
        data = vportal_settings_metadata(data, params, write=True)
        if session is not None:
            session.complete(label)
        return data
    except (PlayerRejected, PlayerUnsupported, HTTPError):
        raise Error("Dedicated VPortal save was rejected or unsupported; the request was not repeated") from None
    finally:
        client.timeout = previous


def preset_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", value)


def preset_names(config):
    presets = config.get("presets", {}) if isinstance(config, dict) else None
    if not isinstance(presets, dict) or any(not preset_name(name) for name in presets):
        raise Error("Presets must be a mapping of short names using letters, digits, underscores or hyphens")
    if len({name.casefold() for name in presets}) != len(presets):
        raise Error("Preset names must be unique ignoring case")
    return sorted(presets, key=str.casefold)


def preset_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        if ":" not in host:
            host = urllib.parse.unquote(host, errors="strict")
            valid_host = not any(ord(char) <= 32 or ord(char) == 127 or char in "#%/:<>?@[\\]^|" for char in host)
            valid_host = valid_host and not parsed.netloc.startswith("[")
            # Browsers interpret a numeric final label as IPv4, even in host.123.
            # Require the unambiguous four-decimal-octet form for preset URLs.
            numeric_host = host.rstrip(".")
            if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", numeric_host.rsplit(".", 1)[-1], re.I):
                ipaddress.IPv4Address(numeric_host)
        else:
            # urlsplit validates IPv6 brackets; browser URLs do not allow zone IDs.
            valid_host = "%" not in host
        return (bool(re.match(r"https?://", value, re.I)) and parsed.scheme in ("http", "https")
                and bool(host) and valid_host and parsed.username is None and parsed.password is None
                and not re.search(r"[\\\s\x00-\x1f\x7f]", value)
                and (parsed.port is None or 1 <= parsed.port <= 65535))
    except (TypeError, ValueError):
        return False


def validate_preset(config, requested):
    names = preset_names(config)
    if not preset_name(requested):
        raise Error("Invalid preset name")
    matches = [name for name in names if name.casefold() == requested.casefold()]
    if len(matches) != 1:
        raise Error("Unknown preset; use ott presets to list local names")
    name = matches[0]
    value = config["presets"][name]
    if (not isinstance(value, dict) or not {"m3u", "plex", "active_profile"} <= set(value)
            or set(value) - {"m3u", "plex", "active_profile", "stalker", "vportal"}):
        raise Error("A preset must contain m3u, plex and active_profile, with optional stalker and vportal profiles")
    if not isinstance(value["m3u"], list) or not 1 <= len(value["m3u"]) <= 15:
        raise Error("A preset must contain 1–15 complete M3U profiles")
    profiles = []
    portals = {}
    numbers = set()
    for row in value["m3u"]:
        if (not isinstance(row, dict) or not {"number", "name", "playlist", "history_hours"} <= set(row)
                or set(row) - {"number", "name", "playlist", "history_hours", "vportal"}
                or type(row["number"]) is not int or not 1 <= row["number"] <= 15 or row["number"] in numbers):
            raise Error("Preset M3U profiles require complete fields and unique numbers from 1 to 15")
        # Legacy links target the independent VPortal slot, never M3U storage.
        # Preserve that slot's name: an M3U label is not a VPortal label.
        if "vportal" in row:
            validate_profile_settings({"vportal": row["vportal"]})
            if row["vportal"]:
                portals[row["number"]] = {"vportal": row["vportal"]}
        settings = {key: row[key] for key in ("name", "playlist", "history_hours")}
        validate_profile_settings(settings)
        if not preset_url(settings["playlist"]):
            raise Error("Preset profiles require an HTTP(S) playlist")
        profiles.append({"number": row["number"], "settings": settings})
        numbers.add(row["number"])
    if "vportal" in value:
        if not isinstance(value["vportal"], list) or not 1 <= len(value["vportal"]) <= 15:
            raise Error("A VPortal preset requires 1–15 profiles")
        seen = set()
        for row in value["vportal"]:
            if (not isinstance(row, dict) or not {"number", "vportal"} <= set(row)
                    or set(row) - {"number", "vportal", "name"}
                    or type(row["number"]) is not int or not 1 <= row["number"] <= 15 or row["number"] in seen):
                raise Error("VPortal profiles require unique numbers 1–15, vportal and optional name")
            settings = {key: row[key] for key in ("vportal", "name") if key in row}
            validate_profile_settings(settings)
            old = portals.get(row["number"])
            if old is not None and old["vportal"] != settings["vportal"]:
                raise Error("Explicit and legacy VPortal links conflict in one slot; no settings were sent")
            portals[row["number"]] = settings
            seen.add(row["number"])
    active = value["active_profile"]
    if type(active) is not int or active not in numbers:
        raise Error("Preset active_profile must name one of its M3U profiles")
    if not isinstance(value["plex"], dict) or set(value["plex"]) != {"server", "token"}:
        raise Error("A preset requires both Plex server and token")
    plex = validate_plex_settings(value["plex"])
    if not preset_url(plex["server"]):
        raise Error("Preset Plex server must be a valid HTTP(S) address")
    stalker = []
    if "stalker" in value:
        if not isinstance(value["stalker"], list) or not 1 <= len(value["stalker"]) <= 15:
            raise Error("A Stalker preset requires 1–15 complete profiles")
        seen = set()
        for row in value["stalker"]:
            if (not isinstance(row, dict) or set(row) != {"number", "name", "server", "mac"}
                    or type(row["number"]) is not int or not 1 <= row["number"] <= 15 or row["number"] in seen
                    or not epg_text(row["name"], 256, "utf-8") or re.search(r"[\x00-\x1f\x7f]", row["name"])
                    or not epg_text(row["server"], 8192, "utf-8")
                    or not preset_url(row["server"]) or not isinstance(row["mac"], str)
                    or not re.fullmatch(r"(?:[a-f0-9]{2}:){5}[a-f0-9]{2}", row["mac"], re.I)):
                raise Error("Stalker profiles require unique numbers 1–15, a name, HTTP(S) server and MAC")
            seen.add(row["number"])
            stalker.append({"profile": row["number"], **{key: row[key] for key in ("name", "server", "mac")}})
        stalker.sort(key=lambda row: row["profile"])
    requests = [("provider_settings", {"provider": "plex", "settings": plex})]
    requests.extend(("profile_settings", row) for row in profiles)
    requests.extend(("provider_settings", {"provider": "stalker", "settings": row}) for row in stalker)
    requests.extend(("provider_settings", {"provider": "vportal", "settings": {"profile": number, **settings}})
                    for number, settings in portals.items())
    for action, params in requests:
        if len(json.dumps({"action": action, "params": params}, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
            raise Error("A preset settings request exceeds the 16 KiB limit")
    result = {"m3u": sorted(profiles, key=lambda row: row["number"]), "plex": plex, "active_profile": active}
    if stalker:
        result["stalker"] = stalker
    if portals:
        result["vportal"] = [{"number": number, "settings": settings} for number, settings in sorted(portals.items())]
    return name, result


def load_preset(client, device, name, preset, session=None):
    """Apply acknowledged steps; repeat only proven pre-write mount rejections."""
    completed = []
    stage = "start"
    budget = client.timeout

    def step(label, action, params, validate, retry_errors=()):
        nonlocal stage
        stage = label
        if session is not None and session.prepare(label, action, params):
            completed.append(label)
            return True
        deadline = time.monotonic() + budget
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Error("Preset phase timed out")
            pending = False
            try:
                client.timeout = remaining
                data = (session.call(action, params) if session is not None else client.call(device, action, params))
            except PlayerRejected as exc:
                if not isinstance(exc.data, dict) or exc.data.get("error") not in retry_errors:
                    raise
                if session is not None:
                    session.retry(label, action, params)
                pending = True
            finally:
                client.timeout = budget
            if time.monotonic() >= deadline:
                raise Error("Preset phase timed out")
            if not pending:
                result = validate(data)
                if result is not None:
                    if session is not None:
                        session.complete(label)
                    completed.append(label)
                    return result
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))

    def provider(expected):
        def validate(data):
            if not isinstance(data, dict) or data.get("dispatched") is not True or data.get("provider") != expected:
                raise Error("Provider switch was not confirmed")
            return True
        return validate

    def plex_status(data):
        if not isinstance(data, dict) or not isinstance(data.get("provider"), str):
            raise Error("Invalid provider status")
        # This may report the selected ID before the driver mounts. Never wait for
        # ready: a Plex credential editor or empty catalogue is still configurable.
        return True if data["provider"] == "plex" else None

    def profiles(data):
        # Presets own M3U slots even though standalone commands also accept VPortal.
        return profile_metadata(data, "profiles", {}, expected_provider="m3u")["profiles"]

    def save_profile(row, active):
        def validate(data):
            result = profile_metadata(data, "profile_settings", row, expected_provider="m3u")
            if result["profile"]["active"] != (row["number"] == active):
                raise Error("The active profile changed during loading")
            return True
        step("save_profile_" + str(row["number"]), "profile_settings", row, validate)

    try:
        # Every dedicated slot must support exact comparison before any mutation.
        # Capture again before each save because an earlier save changes revision.
        for row in preset.get("vportal", []):
            stage = "preflight_vportal_" + str(row["number"])
            vportal_compare(client, device, row)
        for row in preset.get("vportal", []):
            stage = "save_vportal_" + str(row["number"])
            vportal_settings_command(client, device, row, session, stage)
            completed.append(stage)
        if preset.get("stalker"):
            step("select_stalker", "provider", {"query": "stalker"}, provider("stalker"))
            for row in preset["stalker"]:
                def confirmed(data):
                    if (not isinstance(data, dict) or data.get("provider") != "stalker" or data.get("saved") is not True
                            or type(data.get("profile")) is not int or data["profile"] != row["profile"]
                            or not isinstance(data.get("fields"), list) or len(data["fields"]) != 4
                            or set(data["fields"]) != {"profile", "name", "server", "mac"}):
                        raise Error("Stalker profile save was not confirmed")
                    return True
                step("save_stalker_" + str(row["profile"]), "provider_settings",
                     {"provider": "stalker", "settings": row}, confirmed,
                     ("Select this provider before changing its settings.",))
        step("select_plex", "provider", {"query": "plex"}, provider("plex"))
        step("wait_plex", "status", {}, plex_status)
        settings = {"provider": "plex", "settings": preset["plex"]}
        step("save_plex", "provider_settings", settings,
             lambda data: plex_settings_metadata(data, preset["plex"]),
             ("Select this provider before changing its settings.", "Plex settings are unavailable on this player."))
        step("select_m3u", "provider", {"query": "m3u"}, provider("m3u"))
        rows = step("wait_m3u", "profiles", {}, profiles,
                    ("Select the M3U provider before managing profiles.",))
        current = next(row["number"] for row in rows if row["active"])
        desired = preset["active_profile"]
        for row in preset["m3u"]:
            if row["number"] != current:
                save_profile(row, current)
        if current == desired:
            save_profile(next(row for row in preset["m3u"] if row["number"] == current), current)
        params = {"number": desired}
        def selected(data):
            result = profile_metadata(data, "profile", params, expected_provider="m3u")
            expected = next(row for row in preset["m3u"] if row["number"] == desired)
            profile_metadata({"provider": "m3u", "saved": True, "profile": result["profile"]},
                             "profile_settings", expected)
            return True
        step("select_profile_" + str(desired), "profile", params, selected)
        if current != desired:
            for row in preset["m3u"]:
                if row["number"] == current:
                    save_profile(row, desired)
        def verified(data):
            final = profiles(data)
            if next(row["number"] for row in final if row["active"]) != desired:
                raise Error("Final active profile differs from the preset")
            for row in preset["m3u"]:
                profile_metadata({"provider": "m3u", "saved": True, "profile": final[row["number"] - 1]},
                                 "profile_settings", row)
            return True
        step("verify_profiles", "profiles", {}, verified)
        for row in preset.get("vportal", []):
            stage = "verify_vportal_" + str(row["number"])
            if not vportal_compare(client, device, row)["matched"]:
                raise Error("VPortal saved settings differ from the preset")
            completed.append(stage)
    except KeyboardInterrupt:
        return {"preset": name, "status": "interrupted", "stage": stage, "completed": completed,
                "error": "Loading interrupted. A request may have executed; inspect the player before retrying."}
    except (Error, KeyError, TypeError, ValueError, OSError):
        return {"preset": name, "status": "failed", "stage": stage, "completed": completed,
                "error": "Loading stopped. A request may have executed; inspect the player before retrying."}
    return {"preset": name, "status": "loaded", "provider": "m3u", "active_profile": desired,
            "completed": completed}


def preset_command(config, words, timeout, json_output, on_receipt=None):
    if len(words) > 3:
        path = Path(__file__).resolve().with_name("preset_session.py")
        if not path.is_file():
            raise Error("Install preset_session.py beside ott.py")
        spec = importlib.util.spec_from_file_location("ott_preset_session", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.run(SimpleNamespace(**globals()), config, words, timeout, json_output, on_receipt)
    receipt = {"status": "failed", "stage": "validate", "completed": [], "error": "Invalid preset configuration or command."}
    try:
        _, params = parse_command(words[1:])
        name, preset = validate_preset(config, params["preset"])
        receipt["preset"] = name
        receipt["stage"] = "connect"
        receipt["error"] = "Could not prepare the player connection."
        client = Client(config, timeout)
        client.on_receipt = on_receipt
        device = client.device(words[0])
        receipt = load_preset(client, device, name, preset)
    except Error as exc:
        if receipt["stage"] == "validate":
            # Local validators use fixed messages, never credential values.
            receipt["error"] = str(exc)
    except (KeyError, TypeError, ValueError, OSError):
        pass
    except KeyboardInterrupt:
        receipt.update(status="interrupted", error="Preset loading interrupted before connecting.")
    if json_output:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    elif receipt["status"] == "loaded":
        print(f"Preset loaded: {receipt['preset']} (M3U profile {receipt['active_profile']}).")
    else:
        done = ", ".join(receipt["completed"]) or "none"
        print(f"Preset loading stopped at {receipt['stage']}. Completed: {done}. {receipt['error']}", file=sys.stderr)
    return 0 if receipt["status"] == "loaded" else 130 if receipt["status"] == "interrupted" else 1


def kiosk_metadata(data, mode, require_strict=False):
    message = "The player did not confirm the kiosk policy. Check kiosk status before repeating the change."
    if (not isinstance(data, dict) or type(data.get("enabled")) is not bool
            or data.get("state") not in ("off", "waiting", "locked")
            or data["enabled"] != (data["state"] != "off")
            or data.get("retry_seconds") != 10
            or type(data.get("retries")) is not int or data["retries"] < 0
            or data.get("health") not in ("idle", "waiting", "starting", "playing", "retrying", "error", "source-unavailable", "channel-unavailable")
            or (mode == "off" and data["enabled"])
            or (mode in ("on", "set") and not data["enabled"])
            or ("strict" in data and type(data["strict"]) is not bool)
            or (not data["enabled"] and data.get("strict", False))
            or (require_strict and data.get("strict") is not True)
            or (mode == "set" and data["state"] != "locked")):
        raise Error(message)
    channel = data.get("channel")
    media = data.get("media")
    if media is not None:
        if (data["state"] != "locked" or data.get("provider") not in ("vportal", "plex") or channel is not None
                or not isinstance(media, dict) or not epg_text(media.get("title"), 65536, "utf-8")
                or type(media.get("total")) is not int or not 1 <= media["total"] <= 1000
                or type(media.get("index")) is not int or not 0 <= media["index"] < media["total"]):
            raise Error(message)
        media = {key: media[key] for key in ("title", "index", "total")}
    elif data["state"] == "locked":
        if not isinstance(channel, dict) or not isinstance(channel.get("id"), str) or not channel["id"] or not isinstance(channel.get("name"), str):
            raise Error(message)
        channel = {"id": channel["id"], "name": channel["name"]}
    elif channel is not None:
        raise Error(message)
    provider = data.get("provider")
    if (data["enabled"] and not isinstance(provider, str)) or (not data["enabled"] and provider is not None):
        raise Error(message)
    if ("screen_suspended" in data and
            (type(data["screen_suspended"]) is not bool or
             (data["screen_suspended"] and data["state"] != "locked"))):
        raise Error(message)
    if ("startup_grace_seconds" in data and
            (type(data["startup_grace_seconds"]) is not int or
             data["startup_grace_seconds"] != 60 or media is None)):
        raise Error(message)
    return {**{key: data[key] for key in ("enabled", "state", "retry_seconds", "retries", "health")},
            "channel": channel, "provider": provider, **({"media": media} if media is not None else {}),
            **{key: data[key] for key in ("strict", "screen_suspended", "startup_grace_seconds") if key in data}}


def restart_metadata(data, target):
    if (not isinstance(data, dict) or data.get("accepted") is not True or data.get("target") != target
            or (target == "stream" and data.get("dispatched") is not True)
            or (target == "player" and (data.get("dispatched") is not False or data.get("effect") != "reload-after-ack"))):
        raise Error("The player did not confirm the restart request. It may have executed; do not repeat the change blindly.")
    result = {"accepted": True, "target": target, "dispatched": target == "stream"}
    if target == "player":
        result["effect"] = "reload-after-ack"
    return result


def volume_metadata(data, mutating=False):
    """Project the platform reading before either text or JSON output."""
    if not isinstance(data, dict) or (mutating and data.get("dispatched") is not True):
        raise Error("The player did not confirm the volume request. It may have executed; do not repeat the change blindly." if mutating else
                    "The player returned invalid volume metadata")
    value = data.get("volume")
    if value is None:
        raise Error("The command was sent, but the platform does not report volume" if mutating else
                    "The platform does not report volume")
    if type(value) not in (int, float) or not 0 <= value <= 100 or not math.isfinite(value):
        raise Error("The player returned invalid volume metadata" +
                    (". The request may have executed; do not repeat the change blindly." if mutating else ""))
    return {"volume": value, **({"dispatched": True} if mutating else {})}


def screenshot_png(image, width, height):
    """Validate a bounded PNG without loading an imaging library or trusting IHDR."""
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset, header, palette, ended, idat_done = 8, None, None, False, False
    compressed = bytearray()
    while offset < len(image):
        if len(image) - offset < 12:
            return False
        length = struct.unpack_from(">I", image, offset)[0]
        end = offset + 8 + length
        if end + 4 > len(image):
            return False
        kind, chunk = image[offset + 4:offset + 8], image[offset + 8:end]
        if (not re.fullmatch(b"[A-Za-z]{4}", kind) or kind[2] & 32
                or zlib.crc32(image[offset + 4:end]) != struct.unpack_from(">I", image, end)[0]):
            return False
        if header is None and kind != b"IHDR":
            return False
        if compressed and kind != b"IDAT":
            idat_done = True
        if kind == b"IHDR":
            if header is not None or length != 13:
                return False
            header = struct.unpack(">IIBBBBB", chunk)
            w, h, depth, color, compression, filtering, interlace = header
            legal_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if ((w, h) != (width, height) or depth not in legal_depths.get(color, ())
                    or compression or filtering or interlace not in (0, 1)):
                return False
        elif kind == b"PLTE":
            if palette is not None or compressed or not length or length % 3 or length > 768 or header[3] in (0, 4):
                return False
            palette = length // 3
        elif kind == b"IDAT":
            if idat_done or (header[3] == 3 and (palette is None or palette > 2 ** header[2])):
                return False
            compressed.extend(chunk)
        elif kind == b"IEND":
            if length or not compressed or end + 4 != len(image):
                return False
            ended = True
        elif not kind[0] & 32:
            return False
        offset = end + 4
    if not ended or header is None:
        return False
    _, _, depth, color, _, _, interlace = header
    bits = depth * {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    rows = []
    for x, y, dx, dy in passes:
        cols, count = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if cols and count:
            rows.extend([1 + (cols * bits + 7) // 8] * count)
    expected = sum(rows)
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected + 1)
        if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            return False
    except zlib.error:
        return False
    offset = 0
    for length in rows:
        if pixels[offset] > 4:
            return False
        offset += length
    return True


def screenshot_metadata(data, runtime, source):
    invalid = "The player returned an invalid screenshot; no image was saved"
    fields = {"version", "runtime", "mime", "encoding", "image", "width", "height", "captured_at", "source", "video"}
    if (not isinstance(data, dict) or set(data) != fields or type(data.get("version")) is not int
            or data["version"] != 1 or data.get("runtime") != runtime or data.get("source") != source
            or data.get("mime") != "image/png" or data.get("encoding") != "base64"
            or type(data.get("width")) is not int or not 1 <= data["width"] <= 1280
            or type(data.get("height")) is not int or not 1 <= data["height"] <= 720
            or type(data.get("captured_at")) is not int or not 1 <= data["captured_at"] <= 9007199254740991
            or data.get("video") not in ("unknown", "excluded") or not isinstance(data.get("image"), str)
            or not 0 < len(data["image"]) <= ((MAX_SCREENSHOT_BYTES + 2) // 3) * 4):
        raise Error(invalid)
    try:
        image = base64.b64decode(data["image"], validate=True)
    except (ValueError, UnicodeEncodeError):
        raise Error(invalid) from None
    if (len(image) > MAX_SCREENSHOT_BYTES or base64.b64encode(image).decode("ascii") != data["image"]
            or not screenshot_png(image, data["width"], data["height"])):
        raise Error(invalid)
    return {key: data[key] for key in fields - {"image", "encoding"}}, image


def screenshot_destination(output, name):
    """Open an existing directory without following symlinks; never use remote filenames."""
    if output is None:
        alias = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:32] or "player"
        output = f"ott-{alias}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}.png"
    if not isinstance(output, str) or not output or any(ord(c) < 32 or ord(c) == 127 for c in output):
        raise Error("Screenshot output must be a local filename without control characters")
    path = Path(output).expanduser()
    if ".." in path.parts or path.name in ("", ".", "..") or str(output).endswith(("/", "\\")):
        raise Error("Screenshot output must name a file without parent traversal")
    path = Path.cwd() / path if not path.is_absolute() else path
    parent_fd = None
    try:
        if os.open in os.supports_dir_fd and os.link in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            parent_fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            for part in path.parent.parts[1:]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = following
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Error("Screenshot output already exists; choose another filename")
        else:
            # Windows uses an exclusive hard-link install below as well. Reject
            # existing links/junctions and unsafe parents before contacting a player.
            if path.parent.resolve(strict=True) != path.parent or any(
                    not stat.S_ISDIR(parent.lstat().st_mode) for parent in (path.parent, *path.parent.parents)):
                raise Error("Screenshot output parent must be an existing directory without symlinks")
            if os.path.lexists(path):
                raise Error("Screenshot output already exists; choose another filename")
        return path, parent_fd
    except (OSError, Error) as exc:
        if parent_fd is not None:
            os.close(parent_fd)
        if isinstance(exc, Error):
            raise
        raise Error("Screenshot output parent must be an existing writable directory without symlinks") from None


def save_screenshot(path, parent_fd, image):
    temporary = ".ott-shot-" + secrets.token_hex(16)
    target = temporary if parent_fd is not None else str(path.parent / temporary)
    options = {"dir_fd": parent_fd} if parent_fd is not None else {}
    created = False
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, **options)
        created = True
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(image)
            stream.flush()
            os.fsync(stream.fileno())
        if parent_fd is not None:
            os.link(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        else:
            os.link(target, path)
    except FileExistsError:
        raise Error("Screenshot output already exists; choose another filename. The capture was not repeated") from None
    except OSError:
        raise Error("Could not save the screenshot privately; check output permissions and disk space. The capture was not repeated") from None
    finally:
        if created:
            os.unlink(target, **options)


def screenshot_setup_guidance():
    return ("Screenshots need an enabled Remote control connection and a capture source. "
            "In a browser, use Settings → Remote control → Select screenshot source in browser. "
            "Older players may still require their local screenshot permission.")


def screenshot_command(client, device, name, output, timeout):
    # Player transport policy protects the upload. Apply the same policy to the CLI's
    # download before sending any request or opening an output directory.
    try:
        server = urllib.parse.urlsplit(client.server if isinstance(client.server, str) else "")
        secure = bool(server.hostname) and (server.scheme == "https" or
                 (server.scheme == "http" and server.hostname in ("localhost", "127.0.0.1", "::1")))
    except ValueError:
        secure = False
    if not secure:
        raise Error("Screenshots require an HTTPS controller or HTTP loopback address; no capture was requested")
    path, parent_fd = screenshot_destination(output, name)
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    try:
        client.timeout = max(0, deadline - time.monotonic())
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        shot = controls.get("screenshot")
        if shot is None or shot["state"] == "unsupported":
            raise Error("Screenshots are not supported by this player/controller; update both and check caps")
        if shot["state"] == "permission_required":
            raise Error(screenshot_setup_guidance() + " No capture was requested")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Screenshot capability discovery exceeded --timeout; no capture was requested")
        client.timeout = remaining
        runtime = controls["player"]["runtime"]
        data = client.call(device, "screenshot", {"runtime": runtime})
        if time.monotonic() >= deadline:
            raise Error("Screenshot response exceeded --timeout; no image was saved. The capture was not repeated")
        metadata, image = screenshot_metadata(data, runtime, shot["source"])
        save_screenshot(path, parent_fd, image)
        return dict(metadata, path=str(path), bytes=len(image))
    except (PlayerRejected, PlayerUnsupported):
        raise Error("Screenshot unavailable or rejected; check the connection, current runtime, capture source and OS/adapter support. Older players may still require local permission. No image was saved; the capture was not repeated") from None
    except HTTPError as exc:
        if exc.code in (400, 404):
            raise Error("This controller does not accept screenshot requests; update the controller and player. No image was saved") from None
        raise
    finally:
        client.timeout = previous_timeout
        if parent_fd is not None:
            os.close(parent_fd)


def parse_favorites(words):
    """Keep selection on the target player; never snapshot its current URL here."""
    if not words:
        return {"op": "list"}
    verb = words[0].casefold()
    operation = {"current": "add", "rm": "remove"}.get(verb, verb)
    if operation in FAVORITES_OPERATIONS:
        values = words[1:]
    else:
        operation, values = "play", words
    if values and values[0] == "--":
        values = values[1:]
    if operation == "list":
        if values:
            raise Error("Use fav or fav list without arguments")
        return {"op": "list"}
    text = " ".join(values).strip()
    if operation == "add" and not values:
        return {"op": "add"}
    if not plex_library_text(text, 160):
        raise Error("Use fav add [NAME], fav play ID_OR_NAME or fav remove ID_OR_NAME; names use 1–160 UTF-8 bytes without control characters")
    if operation == "add":
        return {"op": "add", "name": text}
    if text.casefold().startswith("id:"):
        item_id = text[3:]
        if not re.fullmatch(FAVORITE_ID_PATTERN, item_id):
            raise Error("Favorite IDs must match an ID shown by fav, for example fav-abc123")
        return {"op": operation, "id": item_id}
    if re.fullmatch(FAVORITE_ID_PATTERN, text):
        return {"op": operation, "id": text}
    return {"op": operation, "query": text}


def favorites_metadata(data, runtime, params, max_items=MAX_FAVORITES):
    invalid = "The player returned invalid favorites metadata. Check fav and player status before another change; the request was not repeated."
    try:
        if len(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > 1024 * 1024:
            raise Error(invalid)
    except (TypeError, ValueError, UnicodeEncodeError):
        raise Error(invalid) from None
    base = {"version", "runtime", "op", "state"}
    if (not isinstance(data, dict) or not base <= set(data)
            or type(data["version"]) is not int or data["version"] != 1
            or data["runtime"] != runtime or data["op"] != params["op"]):
        raise Error(invalid)
    if data["state"] == "error":
        if (set(data) != base | {"error"} or not isinstance(data["error"], str)
                or data["error"] not in FAVORITES_ERRORS):
            raise Error(invalid)
        return dict(data)
    expected_state = "dispatched" if params["op"] == "play" else "ready"
    if data["state"] != expected_state:
        raise Error(invalid)

    def entry(row):
        if (not isinstance(row, dict) or set(row) != {"id", "name", "provider", "profile_number", "profile_name", "kind", "title"}
                or not isinstance(row["id"], str) or not re.fullmatch(FAVORITE_ID_PATTERN, row["id"])
                or not isinstance(row["provider"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_./-]{0,63}", row["provider"])
                or not plex_library_text(row["name"], 160) or not plex_library_text(row["title"], 512)
                or (row["profile_name"] != "" and not (epg_text(row["profile_name"], 256, "utf-8")
                    and all(ord(char) >= 32 and ord(char) != 127 for char in row["profile_name"])))
                or row["kind"] not in ("live", "vod")
                or (row["profile_number"] is not None and
                    (type(row["profile_number"]) is not int or not 1 <= row["profile_number"] <= 15))):
            raise Error(invalid)
        return dict(row)

    if params["op"] == "list":
        rows = data.get("entries")
        if set(data) != base | {"entries"} or not isinstance(rows, list) or len(rows) > max_items:
            raise Error(invalid)
        rows = [entry(row) for row in rows]
        if len({row["id"] for row in rows}) != len(rows):
            raise Error(invalid)
        return dict(data, entries=rows)
    field = {"add": "created", "remove": "removed", "play": "dispatched"}.get(params["op"])
    if (field is None or set(data) != base | {"entry", field} or type(data[field]) is not bool
            or (field != "created" and data[field] is not True)):
        raise Error(invalid)
    row = entry(data["entry"])
    if "id" in params and row["id"] != params["id"]:
        raise Error(invalid)
    return dict(data, entry=row)


def favorites_command(client, device, params, timeout):
    deadline = time.monotonic() + timeout
    previous_timeout, runtime = client.timeout, None
    try:
        client.timeout = max(0, deadline - time.monotonic())
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        capability = controls.get("favorites", {})
        if params["op"] not in capability.get("operations", []):
            raise Error("Favorites require an updated player and controller advertising favorites. No favorites command was sent")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Favorite capability discovery exceeded --timeout; no favorites command was sent")
        client.timeout = remaining
        runtime = controls["player"]["runtime"]
        result = client.call(device, "favorites", dict(params, runtime=runtime))
        if time.monotonic() >= deadline:
            raise Error("Favorite response exceeded --timeout. Acceptance is uncertain; check fav and player status before another change. The request was not repeated")
        result = favorites_metadata(result, runtime, params, capability["max_items"])
        if result["state"] == "error":
            raise Error(FAVORITES_ERRORS[result["error"]] + " The request was not repeated")
        return result
    except (PlayerRejected, PlayerUnsupported) as exc:
        detail = ("Favorites require an updated player and controller advertising favorites." if isinstance(exc, PlayerUnsupported)
                  else "Favorites request was rejected; check the current player runtime and saved profile.")
        if runtime is not None:
            try:
                rejected = favorites_metadata(exc.data, runtime, params)
                if rejected["state"] == "error":
                    detail = FAVORITES_ERRORS[rejected["error"]]
            except Error:
                pass
        raise Error(detail + " The request was not repeated") from None
    except HTTPError as exc:
        if exc.code in (400, 404):
            raise Error("This controller does not accept favorites requests; update it and the player. The request was not repeated") from None
        raise
    finally:
        client.timeout = previous_timeout


def print_favorites(data):
    def label(row):
        profile = f"profile {row['profile_number']}" if row["profile_number"] is not None else "provider settings"
        if row["profile_name"]:
            profile += " " + clean(row["profile_name"])
        return f"{row['id']}  {clean(row['name'])} — {clean(row['title'])} ({row['provider']}, {profile}, {row['kind']})"
    if data["op"] == "list":
        if not data["entries"]:
            print("No saved favorites. Use fav add [NAME] while the desired item is playing.")
        for row in data["entries"]:
            print(label(row))
        return
    row = data["entry"]
    if data["op"] == "add":
        print(("Favorite saved: " if data["created"] else "Favorite saved (existing entry): ") + label(row))
    elif data["op"] == "remove":
        print("Favorite removed: " + label(row))
    else:
        print("Favorite playback dispatched: " + label(row))
        print("The saved profile was loaded and the item resolved; visible playback has not been confirmed.")


def parse_categories(words, singular=False):
    if not words or (len(words) == 1 and words[0].casefold() in ("list", "status")):
        return {"op": "list"}
    values = words
    if words[0].casefold() in ("select", "set"):
        values = words[1:]
    elif not singular:
        raise Error("Use categories [list] or categories select NAME|id:ID; category/cat NAME also selects")
    query = " ".join(values).strip()
    if query.casefold() == "all":
        return {"op": "select", "category_id": "all"}
    if query.casefold().startswith("id:"):
        identity = query[3:]
        if not media_text(identity, 1024):
            raise Error("Use id:ID with the exact nonempty category ID shown by categories")
        return {"op": "select", "category_id": identity}
    if not media_text(query, 512):
        raise Error("A category name must contain 1–512 UTF-8 bytes without control characters")
    return {"op": "select", "query": query}


def media_text(value, maximum):
    return (plex_library_text(value, maximum)
            and not any(127 <= ord(char) <= 159 for char in value))


def media_search_key(value):
    # Match the player's canonical, locale-independent Unicode search keys.
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", value).casefold())


def media_call(client, device, action, params, deadline):
    read_only = action in ("capabilities", "channels") or (action == "categories" and params.get("op") == "list")
    delay = 1
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Media catalog request exceeded --timeout; no further request was sent")
        client.timeout = remaining
        try:
            return client.call(device, action, params)
        except HTTPError as exc:
            # A 429 submission was rejected before being queued. Retrying
            # reads can wait out retained results without replaying writes
            # or refreshing the snapshot/deadline after a failure.
            if not read_only or exc.code != 429:
                raise
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 8)


def media_request(client, device, action, params, deadline):
    try:
        data = media_call(client, device, action, params, deadline)
    except PlayerRejected as exc:
        detail = "The media catalog request was rejected; the catalog, provider or profile may have changed."
        error = exc.data
        if (isinstance(error, dict) and set(error) == {"version", "runtime", "op", "state", "error"}
                and type(error["version"]) is int and error["version"] == 1
                and error["runtime"] == params.get("runtime")
                and error["op"] == params.get("op", action) and error["state"] == "error"
                and isinstance(error["error"], str) and error["error"] in CATEGORY_ERRORS):
            detail = CATEGORY_ERRORS[error["error"]]
        raise Error(detail + " The request was not repeated.") from None
    except PlayerUnsupported:
        raise Error("This player or controller does not support media catalog requests. Update both; the request was not repeated.") from None
    if time.monotonic() >= deadline:
        raise Error("Media catalog response exceeded --timeout. A change may have executed; inspect categories before retrying. The request was not repeated.")
    return data


def media_capabilities(client, device, deadline):
    try:
        data = media_call(client, device, "capabilities", {}, deadline)
    except PlayerUnsupported:
        return None
    except PlayerRejected:
        raise Error("Capability discovery was rejected; no catalog or playback request was sent") from None
    controls = capabilities_metadata(data)
    return controls if "categories" in controls else None


def media_page_metadata(data, runtime, kind, offset, limit, catalog=None, category=None):
    invalid = "The player returned invalid media catalog metadata; no further request was sent"
    common = {"version", "runtime", "catalog", "offset", "total", "has_more", "loading"}
    expected = common | ({"op", "provider", "selected", "categories"} if kind == "categories"
                         else {"category", "channels"})
    if (not isinstance(data, dict) or set(data) != expected
            or type(data["version"]) is not int or data["version"] != 1 or data["runtime"] != runtime
            or not isinstance(data["catalog"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", data["catalog"])
            or (catalog is not None and data["catalog"] != catalog)
            or type(data["offset"]) is not int or data["offset"] != offset
            or type(data["total"]) is not int or not offset <= data["total"] <= MAX_CATEGORY_TOTAL
            or type(data["has_more"]) is not bool or type(data["loading"]) is not bool):
        raise Error(invalid)
    if kind == "categories":
        if (data["op"] != "list" or not isinstance(data["provider"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", data["provider"])
                or not media_text(data["selected"], 1024)):
            raise Error(invalid)
    elif data["category"] != category or not media_text(data["category"], 1024):
        raise Error(invalid)
    rows = data[kind]
    if (not isinstance(rows, list) or len(rows) > limit or offset + len(rows) > data["total"]
            or data["has_more"] != (offset + len(rows) < data["total"])
            or (data["has_more"] and not rows)):
        raise Error(invalid)
    identities, previous = set(), 0
    for row in rows:
        number_key = "count" if kind == "categories" else "number"
        if (not isinstance(row, dict) or set(row) != {"id", "name", number_key}
                or not media_text(row["id"], 1024 if kind == "categories" else 2048)
                or row["id"] in identities or not media_text(row["name"], 512)
                or type(row[number_key]) is not int
                or not (0 if kind == "categories" else previous + 1) <= row[number_key] <= MAX_CATEGORY_TOTAL):
            raise Error(invalid)
        identities.add(row["id"])
        previous = row[number_key]
    return dict(data, **{kind: [dict(row) for row in rows]})


def read_categories(client, device, controls, deadline, first_page_only=False):
    runtime = controls["player"]["runtime"]
    limit = controls["categories"]["maxPageSize"]
    result, rows, identities = None, [], set()
    while True:
        params = {"op": "list", "runtime": runtime, "offset": len(rows), "limit": limit}
        if result is not None:
            params["catalog"] = result["catalog"]
        page = media_page_metadata(media_request(client, device, "categories", params, deadline), runtime,
                                   "categories", len(rows), limit, params.get("catalog"))
        if result is not None and any(page[key] != result[key] for key in ("provider", "selected", "total", "loading")):
            raise Error("The media category catalog changed between pages; no selection was sent")
        if any(row["id"] in identities for row in page["categories"]):
            raise Error("The player returned duplicate category IDs; no selection was sent")
        identities.update(row["id"] for row in page["categories"])
        rows.extend(page["categories"])
        result = page
        if first_page_only or not page["has_more"]:
            break
    if not result["loading"] and not result["has_more"] and ("all" not in identities or result["selected"] not in identities):
        raise Error("The complete media catalog omitted All or the selected category; no selection was sent")
    return dict(result, offset=0, categories=rows)


def categories_command(client, device, params, timeout):
    previous_timeout = client.timeout
    deadline = time.monotonic() + timeout
    try:
        controls = media_capabilities(client, device, deadline)
        if (controls is None or "list" not in controls["categories"]["operations"]
                or params["op"] not in controls["categories"]["operations"]):
            raise Error("Category commands require an updated player and controller advertising categories; no category request was sent")
        result = read_categories(client, device, controls, deadline)
        if params["op"] == "list":
            return result
        if "category_id" in params:
            matches = [row for row in result["categories"] if row["id"] == params["category_id"]]
        else:
            if result["loading"]:
                raise Error("The category catalog is still loading; name selection is incomplete and no selection was sent")
            query = media_search_key(params["query"])
            matches = [row for row in result["categories"] if media_search_key(row["name"]) == query]
            if not matches:
                matches = [row for row in result["categories"] if query in media_search_key(row["name"])]
        if not matches:
            raise Error("No category matches. Run categories to see available names and IDs; no selection was sent")
        if len(matches) != 1:
            choices = ", ".join(shlex.quote("id:" + row["id"]) for row in matches[:20])
            raise Error("Several categories have that name. Select an exact ID: " + choices + "; no selection was sent")
        selected = matches[0]
        request = {"op": "select", "runtime": result["runtime"], "catalog": result["catalog"], "category": selected["id"]}
        response = media_request(client, device, "categories", request, deadline)
        expected = {"version": 1, "runtime": result["runtime"], "op": "select", "catalog": result["catalog"],
                    "provider": result["provider"], "selected": selected["id"], "dispatched": True}
        if (not isinstance(response, dict) or response != expected or type(response.get("version")) is not int
                or response.get("dispatched") is not True):
            raise Error("The player did not confirm the category selection. It may have executed; inspect categories before retrying. The request was not repeated.")
        return dict(expected)
    finally:
        client.timeout = previous_timeout


def read_media_channels(client, device, search, deadline, require_modern=False, page_limit=None, page_offset=0,
                        expected_provider=None, controls=None, category_override=None,
                        source_categories=None, complete_only=False):
    controls = media_capabilities(client, device, deadline) if controls is None else controls
    if controls is None:
        if page_limit is not None:
            raise Error("Paged listing requires a player and controller advertising categories; no channel request was sent. Use s [TEXT] for legacy listing")
        if require_modern:
            raise Error("Playing an exact video ID requires a player and controller advertising categories; no playback was requested")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Capability discovery exceeded --timeout; no catalog request was sent")
        client.timeout = remaining
        return client.call(device, "channels", {"search": search})
    if "list" not in controls["categories"]["operations"]:
        raise Error("Media category listing is unavailable in the current player state; no legacy fallback or playback was requested")
    maximum = controls["categories"]["maxPageSize"]
    if page_limit is not None and page_limit > maximum:
        raise Error(f"This player supports --limit up to {maximum}; no catalog request was sent")
    # The first page carries the selected category and its source receipt even
    # when the selected category's row is on a later page.
    categories = (read_categories(client, device, controls, deadline, first_page_only=True)
                  if source_categories is None else source_categories)
    if expected_provider is not None and categories["provider"] != expected_provider:
        raise Error("The provider changed before its catalog was read; no playback was requested")
    runtime, category = categories["runtime"], category_override or categories["selected"]
    limit = maximum if page_limit is None else page_limit
    result, rows, identities = None, [], set()
    previous_number = 0
    # Only offset zero may exchange a source receipt for a category/query
    # receipt. A one-row primer binds nonzero page requests to that snapshot.
    priming = page_limit is not None and page_offset > 0
    while True:
        offset = 0 if priming else page_offset + len(rows)
        request_limit = 1 if priming else limit
        params = {"runtime": runtime, "catalog": result["catalog"] if result else categories["catalog"],
                  "category": category, "offset": offset, "limit": request_limit}
        if search:
            params["search"] = search
        page = media_page_metadata(media_request(client, device, "channels", params, deadline), runtime,
                                   "channels", offset, request_limit, result["catalog"] if result else None, category)
        if complete_only and page["loading"]:
            return dict(page, channels=[])
        if result is not None and any(page[key] != result[key] for key in ("total", "loading")):
            raise Error("The video catalog changed between pages; no playback was requested")
        if (any(row["id"] in identities for row in page["channels"])
                or (page["channels"] and page["channels"][0]["number"] <= previous_number)
                or any(media_search_key(search) not in media_search_key(row["name"]) for row in page["channels"])):
            raise Error("The player returned an inconsistent video catalog; no playback was requested")
        identities.update(row["id"] for row in page["channels"])
        if page["channels"]:
            previous_number = page["channels"][-1]["number"]
        result = page
        if priming:
            if page_offset > page["total"]:
                raise Error(f"--offset exceeds the {page['total']} matching videos; no page request was sent")
            priming = False
            continue
        rows.extend(page["channels"])
        if page_limit is not None or not page["has_more"]:
            break
    return dict(result, offset=page_offset, loading=result["loading"] or categories["loading"], channels=rows)


def channels_command(client, device, params, timeout):
    previous_timeout = client.timeout
    try:
        return read_media_channels(client, device, params["search"], time.monotonic() + timeout,
                                   page_limit=params.get("limit"), page_offset=params.get("offset", 0))
    finally:
        client.timeout = previous_timeout


def print_categories(data):
    if data["op"] == "select":
        print("Category selected: " + shlex.quote("id:" + data["selected"]) + ". Playback was not started.")
        return
    for row in data["categories"]:
        print(f"{'*' if row['id'] == data['selected'] else ' '} {shlex.quote('id:' + row['id'])}  {clean(row['name'])} ({row['count']})")


def parse_plex_library(words):
    operation = words[0].casefold()
    if operation == "libraries":
        if len(words) != 1:
            raise Error("Use plex libraries without arguments")
        return {"op": "list"}
    params = {"op": "play", "shuffle": operation == "shuffle"}
    title = []
    flags = set()
    options = True
    for word in words[1:]:
        flag = word.casefold()
        if options and flag == "--":
            options = False
        elif options and flag in ("--shuffle", "--preview"):
            if flag in flags or (flag == "--shuffle" and operation == "shuffle"):
                raise Error("Do not repeat Plex library options")
            flags.add(flag)
            if flag == "--shuffle":
                params["shuffle"] = True
            else:
                params["op"] = "preview"
        elif options and word.startswith("-"):
            raise Error("Use plex library [--shuffle] [--preview] NAME or id:NUMBER")
        else:
            title.append(word)
    query = " ".join(title).strip()
    if query.casefold().startswith("id:"):
        value = query[3:]
        if not re.fullmatch(r"[1-9][0-9]{0,19}", value):
            raise Error("Use id:NUMBER with a positive decimal Plex library ID (at most 20 digits)")
        params["library_id"] = value
    elif not plex_library_text(query, 256):
        raise Error("A Plex library name must contain 1–256 UTF-8 bytes without control characters")
    else:
        params["query"] = query
    return params


def plex_library_text(value, maximum):
    return (epg_text(value, maximum, "utf-8") and bool(value.strip())
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


def plex_library_metadata(data, runtime, params, max_items=MAX_PLEX_QUEUE_ITEMS):
    invalid = "The player returned invalid Plex library metadata. Acceptance may be uncertain; inspect plex status before another playback request."
    try:
        if len(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > 1024 * 1024:
            raise Error(invalid)
    except (TypeError, ValueError, UnicodeEncodeError):
        raise Error(invalid) from None
    base = {"version", "runtime", "op", "state"}
    if (not isinstance(data, dict) or not base <= set(data)
            or type(data["version"]) is not int or data["version"] != 1
            or data["runtime"] != runtime or data["op"] != params["op"]):
        raise Error(invalid)
    if data["state"] == "error":
        problem = data.get("error")
        if (set(data) != base | {"error"} or not isinstance(problem, str)
                or (problem not in PLEX_LIBRARY_ERRORS and problem not in PLEX_QUEUE_ERRORS)):
            raise Error(invalid)
        return dict(data)
    if data["state"] != "ready":
        raise Error(invalid)

    def library(row):
        if (not isinstance(row, dict) or set(row) != {"id", "title", "type"}
                or not isinstance(row["id"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", row["id"])
                or not plex_library_text(row["title"], 160) or row["type"] not in ("movie", "show")):
            raise Error(invalid)
        return dict(row)

    if params["op"] == "list":
        rows = data.get("libraries")
        if set(data) != base | {"libraries"} or not isinstance(rows, list) or len(rows) > 1000:
            raise Error(invalid)
        rows = [library(row) for row in rows]
        if len({row["id"] for row in rows}) != len(rows):
            raise Error(invalid)
        return dict(data, libraries=rows)
    truncated = "truncated" in data
    expected = base | {"library", "shuffled", "queue"} | ({"truncated"} if truncated else set())
    if (set(data) != expected or type(data["shuffled"]) is not bool
            or data["shuffled"] != params.get("shuffle", False)):
        raise Error(invalid)
    if truncated and (data["truncated"] is not True or params.get("allow_truncate") is not True):
        raise Error(invalid)
    row = library(data["library"])
    if "library_id" in params and row["id"] != params["library_id"]:
        raise Error(invalid)
    queue_data = data["queue"]
    if (not isinstance(queue_data, dict) or not plex_queue_ids(queue_data.get("ids"), 1)
            or len(queue_data["ids"]) > max_items or queue_data.get("state") == "error"
            or (truncated and len(queue_data["ids"]) != max_items)):
        raise Error(invalid)
    queue_data = plex_queue_metadata(queue_data, runtime, {"op": params["op"], "ids": queue_data["ids"]})
    return dict(data, library=row, queue=queue_data)


def plex_library_command(client, device, params, timeout):
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    runtime = None
    request_params = dict(params)
    try:
        client.timeout = max(0, deadline - time.monotonic())
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        capability = controls.get("plex_library", {})
        if params["op"] not in capability.get("operations", []):
            raise Error("Plex library commands require an updated player and controller advertising plex_library. No library command was sent")
        if params["op"] != "list" and "plex_library_truncation" in controls:
            request_params["allow_truncate"] = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Plex capability discovery exceeded --timeout; no library command was sent")
        client.timeout = remaining
        runtime = controls["player"]["runtime"]
        result = client.call(device, "plex_library", dict(request_params, runtime=runtime))
        if time.monotonic() >= deadline:
            raise Error("Plex library response exceeded --timeout. Acceptance is uncertain; inspect plex status before another playback request. The request was not repeated")
        result = plex_library_metadata(result, runtime, request_params, capability["max_items"])
        if result["state"] == "error":
            raise Error(PLEX_LIBRARY_ERRORS.get(result["error"], result["error"]) + " The request was not repeated")
        return result
    except PlayerRejected as exc:
        detail = "Plex library request was rejected; check the saved Plex settings and current player runtime."
        if runtime is not None:
            try:
                rejected = plex_library_metadata(exc.data, runtime, request_params)
                if rejected["state"] == "error":
                    detail = PLEX_LIBRARY_ERRORS.get(rejected["error"], rejected["error"])
            except Error:
                pass
        raise Error(detail + " The request was not repeated") from None
    except PlayerUnsupported:
        raise Error("Plex library commands require an updated player and controller advertising plex_library. The request was not repeated") from None
    except HTTPError as exc:
        if exc.code in (400, 404):
            raise Error("This controller does not accept Plex library requests; update it and the player. The request was not repeated") from None
        raise
    finally:
        client.timeout = previous_timeout


def print_plex_library(data):
    if data["op"] == "list":
        if not data["libraries"]:
            print("No movie or show libraries are available on the player's Plex server.")
        for row in data["libraries"]:
            print(f"id:{row['id']}  {clean(row['title'])} ({row['type']})")
    else:
        row = data["library"]
        if data.get("truncated") is True:
            count = len(data["queue"]["ids"])
            print(f"Warning: The Plex library exceeds the {count}-item queue limit; using {count} items.")
        print(f"Plex library: {clean(row['title'])} (id:{row['id']}); "
              + ("shuffled once." if data["shuffled"] else "library order."))
        print_plex_queue(data["queue"], preview=data["op"] == "preview")


def plex_queue_ids(ids, minimum=0):
    return (isinstance(ids, list) and minimum <= len(ids) <= MAX_PLEX_QUEUE_ITEMS
            and all(isinstance(item, str) and re.fullmatch(r"[1-9][0-9]{0,19}", item) for item in ids))


def plex_queue_title(value):
    return epg_text(value, 512, "utf-8") and all(ord(char) >= 32 and ord(char) != 127 for char in value)


def plex_queue_metadata(data, runtime=None, params=None):
    invalid = "The player returned invalid Plex queue metadata. The request may have executed; do not repeat the change blindly."
    preview = params is not None and params["op"] == "preview"
    required = ({"version", "runtime", "state", "ids", "titles", "order"} if preview else
                {"version", "runtime", "active", "state", "ids", "index", "repeat", "order"})
    optional = {"error"} if preview else {"title", "error"}
    if (not isinstance(data, dict) or not required <= set(data) or set(data) - required - optional
            or type(data.get("version")) is not int or data["version"] != 1
            or not isinstance(data.get("runtime"), str) or not re.fullmatch(r"[a-z0-9-]{1,64}", data["runtime"])
            or (runtime is not None and data["runtime"] != runtime)
            or not plex_queue_ids(data.get("ids")) or data.get("order") != "listed"
            or ("error" in data and (data.get("state") != "error" or not isinstance(data["error"], str)
                                     or data["error"] not in PLEX_QUEUE_ERRORS))):
        raise Error(invalid)
    ids = data["ids"]
    if params and params["op"] in ("play", "preview") and ids != params["ids"]:
        raise Error(invalid)
    if preview:
        titles = data["titles"]
        if (data["state"] not in ("ready", "error") or not isinstance(titles, list)
                or len(titles) not in (0, len(ids)) or (data["state"] == "ready" and len(titles) != len(ids))
                or not ids or any(not plex_queue_title(title) for title in titles)):
            raise Error(invalid)
        return dict(data, ids=list(ids), titles=list(titles))
    state, index = data["state"], data["index"]
    if (type(data["active"]) is not bool or data["repeat"] not in ("none", "all")
            or (ids and (type(index) is not int or not 0 <= index < len(ids)))
            or (not ids and index is not None)
            or ("title" in data and not plex_queue_title(data["title"]))):
        raise Error(invalid)
    if state == "idle":
        valid = not data["active"] and not ids
    elif state in ("preparing", "playing", "paused"):
        valid = data["active"] and bool(ids)
    elif state == "ended":
        valid = data["active"] and bool(ids) and index == len(ids) - 1
    elif state == "error":
        valid = data["active"] == bool(ids)
    else:
        valid = False
    if not valid or (params and ((params["op"] == "play" and index != 0) or (params["op"] == "stop" and state != "idle"))):
        raise Error(invalid)
    return dict(data, ids=list(ids))


def plex_queue_command(client, device, params, timeout):
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    try:
        client.timeout = max(0, deadline - time.monotonic())
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        if params["op"] not in controls.get("plex_queue", {}).get("operations", []):
            raise Error("Plex queues are not supported by this player/controller; update both and check caps. No queue command was sent")
        if len(params.get("ids", [])) > controls["plex_queue"]["max_items"]:
            raise Error("This player supports at most " + str(controls["plex_queue"]["max_items"]) + " Plex items; update it. No queue command was sent")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Plex capability discovery exceeded --timeout; no queue command was sent")
        client.timeout = remaining
        runtime = controls["player"]["runtime"]
        result = client.call(device, "plex_queue", dict(params, runtime=runtime))
        if time.monotonic() >= deadline:
            raise Error("Plex queue response exceeded --timeout. Acceptance is uncertain and the request may still execute before its server deadline; do not repeat it blindly. Inspect plex status")
        result = plex_queue_metadata(result, runtime, params)
        if result["state"] == "error" and params["op"] != "status":
            raise Error(result.get("error", "Plex playback could not start.") + " Inspect plex status before another playback request")
        return result
    except PlayerRejected as exc:
        detail = exc.data.get("error") if isinstance(exc.data, dict) else None
        if not isinstance(detail, str) or detail not in PLEX_QUEUE_ERRORS:
            detail = "Plex queue request was rejected; check saved Plex settings, the current player runtime and availability."
        raise Error(detail + " The request was not repeated") from None
    except PlayerUnsupported:
        raise Error("Plex queues are not supported by this player/controller; update both and check caps. The request was not repeated") from None
    except HTTPError as exc:
        if exc.code == 400:
            raise Error("This controller does not accept Plex queue requests; update the controller and player. The request was not repeated") from None
        raise
    finally:
        client.timeout = previous_timeout


def print_plex_queue(data, preview=False):
    if preview:
        print("Plex preview ready; playback was not started:")
        for item, title in zip(data["ids"], data["titles"]):
            print(f"  {item}: {clean(title)}")
        return
    state = data["state"]
    position = "" if data["index"] is None else f" {data['index'] + 1}/{len(data['ids'])} (ID {data['ids'][data['index']]})"
    title = "" if not data.get("title") else ": " + clean(data["title"])
    print(f"Plex queue: {state}{position}{title}; listed order, repeat={data['repeat']}.")
    if state == "preparing":
        print("Accepted and preparing; playback has not been confirmed. Use plex status to check progress.")
    if data.get("error"):
        print(data["error"])


def aspect_metadata(data, runtime, params):
    mutating = params["operation"] == "set"
    invalid = ("The player did not confirm the aspect request. It may have executed; do not repeat the change blindly. Read aspect to check the current mode."
               if mutating else "The player returned invalid aspect metadata; read aspect again to check the current mode")
    fields = {"version", "runtime", "operation", "mode"}
    fields.update(("accepted", "dispatched", "effect") if mutating else ("saved_mode", "persisted"))
    if (not isinstance(data, dict) or set(data) != fields
            or type(data.get("version")) is not int or data["version"] != 1
            or data.get("runtime") != runtime or data.get("operation") != params["operation"]
            or not isinstance(data.get("mode"), str) or data["mode"] not in ASPECT_MODES):
        raise Error(invalid)
    if mutating:
        if (data["mode"] != params["mode"] or data["accepted"] is not True
                or data["dispatched"] is not False or data["effect"] != "aspect-after-ack"):
            raise Error(invalid)
    else:
        saved = data["saved_mode"]
        if ((saved is not None and (not isinstance(saved, str) or saved not in ASPECT_MODES))
                or type(data["persisted"]) is not bool
                or data["persisted"] != (saved is not None and saved == data["mode"])):
            raise Error(invalid)
    return dict(data)


def aspect_command(client, device, params, timeout):
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    sent = False
    try:
        client.timeout = max(0, deadline - time.monotonic())
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        aspect = controls.get("aspect", {})
        if (params["operation"] not in aspect.get("operations", [])
                or (params["operation"] == "set" and params["mode"] not in aspect.get("modes", []))):
            raise Error("The requested aspect control is not supported by this player/controller; update both and check caps. No aspect command was sent")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error("Aspect capability discovery exceeded --timeout; no aspect command was sent")
        client.timeout = remaining
        runtime = controls["player"]["runtime"]
        sent = True
        result = client.call(device, "aspect", dict(params, runtime=runtime))
        if time.monotonic() >= deadline:
            if params["operation"] == "set":
                raise Error("Aspect response exceeded --timeout. Acceptance is uncertain and the request may still execute before its server deadline; do not repeat it blindly. Read aspect to check the current mode")
            raise Error("Aspect response exceeded --timeout; read aspect again to check the current mode")
        return aspect_metadata(result, runtime, params)
    except (PlayerRejected, PlayerUnsupported) as exc:
        reason = "not supported by this player/controller" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
        outcome = "The request was not repeated" if sent else "No aspect command was sent"
        raise Error(f"Aspect ratio control was {reason}; check caps, the current runtime and local restrictions. {outcome}") from None
    except HTTPError as exc:
        if exc.code in (400, 404):
            outcome = "The request was not repeated" if sent else "No aspect command was sent"
            raise Error(f"This controller does not accept aspect control requests; update the controller and player. {outcome}") from None
        raise
    finally:
        client.timeout = previous_timeout


def print_aspect(data, name):
    mode = data["mode"]
    label = f"{ASPECT_MODES[mode]} ({mode})"
    if data["operation"] == "set":
        print(f"Aspect ratio request accepted: {label}; waiting for the acknowledgement to reach the player.")
        print(f"Application and persistence are not yet confirmed. Run ott {shlex.quote(clean(name))} aspect to check the current and saved modes.")
    else:
        print(f"Aspect ratio: {label}.")
        saved = data["saved_mode"]
        print("Saved mode: " + (f"{ASPECT_MODES[saved]} ({saved})." if saved is not None else "unavailable."))


def notifications_metadata(data, params, runtime, status="ok"):
    invalid = "The player returned invalid notification preference metadata; read notifications status before another change"
    keys = {"version", "runtime", "operation", "enabled", "persisted"} if status == "ok" else {
        "version", "runtime", "operation", "error"}
    if (not isinstance(data, dict) or set(data) != keys
            or type(data.get("version")) is not int or data["version"] != 1
            or data.get("runtime") != runtime or data.get("operation") != params["operation"]):
        raise Error(invalid)
    if status != "ok":
        if not isinstance(data.get("error"), str) or data["error"] not in NOTIFICATIONS_ERRORS:
            raise Error(invalid)
        return data
    if (type(data.get("enabled")) is not bool or data.get("persisted") is not True
            or (params["operation"] == "set" and data["enabled"] != params["enabled"])):
        raise Error(invalid)
    return dict(data)


def notifications_command(client, device, params, timeout):
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout
    try:
        client.timeout = max(0, deadline - time.monotonic())
        try:
            controls = capabilities_metadata(client.call(device, "capabilities", {}))
        except (PlayerRejected, PlayerUnsupported):
            raise Error("Notification controls are not supported by this player/controller; no preference command was sent") from None
        if params["operation"] not in controls.get("notifications", {}).get("operations", []):
            raise Error("Notification controls are not supported by this player/controller; no preference command was sent")
        client.timeout = deadline - time.monotonic()
        if client.timeout <= 0:
            raise Error("Notification capability discovery exceeded --timeout; no preference command was sent")
        runtime = controls["player"]["runtime"]
        request = dict(params, version=1, runtime=runtime)
        try:
            raw = client.call(device, "notifications", request)
        except (PlayerRejected, PlayerUnsupported) as exc:
            checked = notifications_metadata(exc.data, params, runtime, "rejected")
            messages = {
                "storage_unavailable": "The player could not verify durable notification storage",
                "runtime_mismatch": "The player runtime changed before the notification request",
                "unsupported": "The player does not support notification preferences",
                "invalid_request": "The player rejected the notification request",
            }
            raise Error(messages[checked["error"]] + "; the request was not repeated") from None
        if time.monotonic() >= deadline:
            raise Error("The notification response exceeded --timeout; the preference may have changed. Read notifications status; the request was not repeated")
        return notifications_metadata(raw, params, runtime)
    except HTTPError as exc:
        if exc.code in (400, 404, 405):
            raise Error("This controller does not support notification preferences; update the controller and player. The request was not repeated") from None
        raise
    finally:
        client.timeout = previous_timeout


def print_notifications(data):
    print("Notifications: " + ("on" if data["enabled"] else "off") + " (saved on player; banners and camera media).", flush=True)


def capabilities_metadata(data):
    invalid = "The player returned invalid capabilities metadata"
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise Error(invalid)
    player = data.get("player")
    patterns = {"version": r"[A-Za-z0-9_.+\-]{1,64}", "platform": r"[a-z0-9_-]{1,32}", "runtime": r"[a-z0-9-]{1,64}"}
    if not isinstance(player, dict) or any(not isinstance(player.get(key), str) or not re.fullmatch(pattern, player[key])
                                           for key, pattern in patterns.items()):
        raise Error(invalid)
    result = {"version": 1, "player": {key: player[key] for key in patterns}}
    if "player_settings" in data:
        settings = data["player_settings"]
        if (not isinstance(settings, dict) or set(settings) != {"version", "operations", "max_page_size"}
                or type(settings["version"]) is not int or settings["version"] != 1
                or type(settings["max_page_size"]) is not int or settings["max_page_size"] != 20
                or settings["operations"] != ["schema", "get", "set"]):
            raise Error(invalid)
        result["player_settings"] = settings
    if "vportal_settings" in data:
        settings = data["vportal_settings"]
        if not isinstance(settings, dict) or set(settings) != {"version"} or type(settings["version"]) is not int or settings["version"] != 1:
            raise Error(invalid)
        result["vportal_settings"] = {"version": 1}
    for key, allowed in (("lifecycle", LIFECYCLE_OPERATIONS), ("input", INPUT_KEYS), ("playback", PLAYBACK_OPERATIONS)):
        values = data.get(key)
        if (not isinstance(values, list) or len(values) > len(allowed) or
                any(not isinstance(value, str) or value not in allowed for value in values) or len(set(values)) != len(values)):
            raise Error(invalid)
        result[key] = list(values)
    if "random" in data:
        random = data["random"]
        if not isinstance(random, dict) or set(random) != {"version"} or type(random["version"]) is not int or random["version"] != 1:
            raise Error(invalid)
        result["random"] = {"version": 1}
    if data.get("offline_profiles") is not None:
        offline = data["offline_profiles"]
        operations = offline.get("operations") if isinstance(offline, dict) else None
        if (not isinstance(offline, dict) or set(offline) != {"version", "operations", "max_profiles", "max_sources"}
                or type(offline.get("version")) is not int or offline["version"] != 1
                or type(offline.get("max_profiles")) is not int or offline["max_profiles"] != 15
                or type(offline.get("max_sources")) is not int or offline["max_sources"] != 32
                or not isinstance(operations, list) or len(operations) > len(OFFLINE_PROFILE_OPERATIONS)
                or any(not isinstance(op, str) or op not in OFFLINE_PROFILE_OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise Error(invalid)
        result["offline_profiles"] = dict(offline, operations=list(operations))
    if "aspect" in data:
        aspect = data["aspect"]
        if (not isinstance(aspect, dict) or set(aspect) != {"version", "operations", "modes"}
                or type(aspect.get("version")) is not int or aspect["version"] != 1):
            raise Error(invalid)
        for key, allowed in (("operations", ASPECT_OPERATIONS), ("modes", ASPECT_MODES)):
            values = aspect[key]
            if (not isinstance(values, list) or len(values) > len(allowed)
                    or any(not isinstance(value, str) or value not in allowed for value in values)
                    or len(set(values)) != len(values)):
                raise Error(invalid)
        if bool(aspect["operations"]) != bool(aspect["modes"]):
            raise Error(invalid)
        result["aspect"] = {"version": 1, "operations": list(aspect["operations"]), "modes": list(aspect["modes"])}
    if "notifications" in data:
        notifications = data["notifications"]
        operations = notifications.get("operations") if isinstance(notifications, dict) else None
        if (not isinstance(notifications, dict) or set(notifications) != {"version", "operations"}
                or type(notifications.get("version")) is not int or notifications["version"] != 1
                or not isinstance(operations, list) or len(operations) > len(NOTIFICATIONS_OPERATIONS)
                or any(not isinstance(op, str) or op not in NOTIFICATIONS_OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise Error(invalid)
        result["notifications"] = {"version": 1, "operations": list(operations)}
    if data.get("app_update") is not None:
        update = data["app_update"]
        if (not isinstance(update, dict) or set(update) != {"version", "operations"}
                or type(update.get("version")) is not int or update["version"] != 1
                or update.get("operations") != ["status", "prepare", "install"]):
            raise Error(invalid)
        result["app_update"] = {"version": 1, "operations": list(update["operations"])}
    if "screenshot" in data:
        shot = data["screenshot"]
        if (not isinstance(shot, dict) or set(shot) != {"state", "source"}
                or shot.get("state") not in ("ready", "permission_required", "unsupported")
                or (shot.get("source") is not None and
                    (not isinstance(shot["source"], str) or shot["source"] not in SCREENSHOT_SOURCES))
                or (shot["state"] == "ready" and shot["source"] is None)):
            raise Error(invalid)
        result["screenshot"] = dict(shot)
    if "plex_queue" in data:
        queue = data["plex_queue"]
        operations = queue.get("operations") if isinstance(queue, dict) else None
        if (not isinstance(queue, dict) or set(queue) != {"version", "operations", "max_items"}
                or type(queue.get("version")) is not int or queue["version"] != 1
                or type(queue.get("max_items")) is not int or not 1 <= queue["max_items"] <= MAX_PLEX_QUEUE_ITEMS
                or not isinstance(operations, list) or len(operations) > len(PLEX_QUEUE_OPERATIONS)
                or any(not isinstance(op, str) or op not in PLEX_QUEUE_OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise Error(invalid)
        result["plex_queue"] = {"version": 1, "operations": list(operations), "max_items": queue["max_items"]}
    if "favorites" in data:
        favorites = data["favorites"]
        operations = favorites.get("operations") if isinstance(favorites, dict) else None
        if (not isinstance(favorites, dict) or set(favorites) != {"version", "operations", "max_items"}
                or type(favorites.get("version")) is not int or favorites["version"] != 1
                or type(favorites.get("max_items")) is not int or not 1 <= favorites["max_items"] <= MAX_FAVORITES
                or not isinstance(operations, list) or len(operations) > len(FAVORITES_OPERATIONS)
                or any(not isinstance(op, str) or op not in FAVORITES_OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise Error(invalid)
        result["favorites"] = {"version": 1, "operations": list(operations), "max_items": favorites["max_items"]}
    if "plex_library" in data:
        library = data["plex_library"]
        operations = library.get("operations") if isinstance(library, dict) else None
        if (not isinstance(library, dict) or set(library) != {"version", "operations", "max_items"}
                or type(library.get("version")) is not int or library["version"] != 1
                or type(library.get("max_items")) is not int or not 1 <= library["max_items"] <= MAX_PLEX_QUEUE_ITEMS
                or not isinstance(operations, list) or len(operations) > len(PLEX_LIBRARY_OPERATIONS)
                or any(not isinstance(op, str) or op not in PLEX_LIBRARY_OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise Error(invalid)
        result["plex_library"] = {"version": 1, "operations": list(operations), "max_items": library["max_items"]}
    if "plex_library_truncation" in data:
        truncation = data["plex_library_truncation"]
        if ("plex_library" not in result or result["plex_library"]["max_items"] != MAX_PLEX_QUEUE_ITEMS
                or not isinstance(truncation, dict)
                or set(truncation) != {"version"} or type(truncation.get("version")) is not int
                or truncation["version"] != 1):
            raise Error(invalid)
        result["plex_library_truncation"] = {"version": 1}
    if "categories" in data:
        categories = data["categories"]
        operations = categories.get("operations") if isinstance(categories, dict) else None
        if (not isinstance(categories, dict) or set(categories) != {"version", "operations", "maxPageSize"}
                or type(categories.get("version")) is not int or categories["version"] != 1
                or type(categories.get("maxPageSize")) is not int
                or not 1 <= categories["maxPageSize"] <= MAX_CATEGORY_PAGE_SIZE
                or not isinstance(operations, list) or len(operations) > len(CATEGORIES_OPERATIONS)
                or any(not isinstance(op, str) or op not in CATEGORIES_OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise Error(invalid)
        result["categories"] = dict(categories, operations=list(operations))
    if "inspect" in data:
        inspection = data["inspect"]
        sections = inspection.get("sections") if isinstance(inspection, dict) else None
        if (not isinstance(inspection, dict) or set(inspection) != {"version", "sections"}
                or type(inspection.get("version")) is not int or inspection["version"] != 1
                or not isinstance(sections, list) or not 1 <= len(sections) <= 3
                or any(not isinstance(section, str) or section not in ("doctor", "snapshot", "operation") for section in sections)
                or len(set(sections)) != len(sections)):
            raise Error(invalid)
        result["inspect"] = {"version": 1, "sections": list(sections)}
    if "debug" in data:
        debug = data["debug"]
        if (not isinstance(debug, dict) or set(debug) != {"version"}
                or type(debug.get("version")) is not int or debug["version"] != 1):
            raise Error(invalid)
        result["debug"] = {"version": 1}
    if "debug_versions" in data:
        versions = data["debug_versions"]
        if ("debug" not in result or not isinstance(versions, list)
                or any(type(value) is not int for value in versions)
                or versions != [1, 2]):
            raise Error(invalid)
        result["debug_versions"] = list(versions)
    return result


def status_capabilities(client, device, data, deadline):
    """Enrich a successful status within the original timeout, without mutations."""
    if not isinstance(data, dict):
        raise Error("The player returned invalid status metadata")
    result = dict(data, capabilities=None)
    player = data.get("player")
    if isinstance(player, dict):
        player = dict(player)
        if "standby" in player and type(player["standby"]) is not bool:
            raise Error("The player returned invalid standby metadata")
        if "background" in player:
            background = player["background"]
            if (not isinstance(background, dict) or set(background) != {"state", "pending"}
                    or background.get("state") not in ("running", "suspending", "suspended", "unsupported", "error")
                    or (background.get("pending") is not None and
                        (type(background["pending"]) is not int or not 0 <= background["pending"] <= 2147483647))):
                raise Error("The player returned invalid background activity metadata")
            player["background"] = dict(background)
        result["player"] = player
    result.pop("capabilities_error", None)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        result["capabilities_error"] = "No time remains to read available controls; run status again."
        return result
    previous_timeout = client.timeout
    try:
        client.timeout = remaining
        controls = capabilities_metadata(client.call(device, "capabilities", {}))
        identity = data.get("player")
        if time.monotonic() >= deadline:
            result["capabilities_error"] = "Available controls did not arrive within --timeout; run status again."
        elif identity is not None and (not isinstance(identity, dict) or any(
                identity.get(key) != value for key, value in controls["player"].items())):
            result["capabilities_error"] = "The player changed while reading status; run status again."
        else:
            result["capabilities"] = controls
    except PlayerUnsupported:
        result["capabilities_error"] = "Available controls are not reported by this player/controller."
    except Error:
        # A secondary request must not hide a valid status or expose raw errors.
        result["capabilities_error"] = "Available controls could not be read; run status again."
    finally:
        client.timeout = previous_timeout
    return result


def print_player_help(data, name):
    quoted_name = shlex.quote(clean(name))
    prefix = "ott " + quoted_name
    controls = data["capabilities"]
    if controls is None:
        print(data["capabilities_error"])
        print(f"Local diagnostic help: {prefix} test list")
        return
    lifecycle = {
        "reload_player": ("restart", "reload the player (alias: restart player)"),
        "restart_stream": ("restart stream", "restart only the current stream (alias: restart s)"),
        "restart_app": ("restart app", "relaunch the native app"),
        "standby": ("standby", "stop playback and enter standby; stay connected (alias: stop)"),
        "wake": ("go", "leave standby and start configured playback (aliases: start, wake)"),
        "exit_app": ("exit", "exit the app"),
        "reboot_device": ("reboot", "reboot the device OS"),
    }
    print("Available controls now:")
    if "player_settings" in controls:
        print(f"  {prefix} settings  — list typed preferences for this player and source")
        print(f"  {prefix} setting bufSize [3]  — read/save buffering seconds; applies to the next stream")
        print(f"  {prefix} setting players [hls.js]  — read/save a supported engine for this provider/profile")
    provider = data.get("provider")
    provider_commands = isinstance(provider, str) and bool(provider.strip())
    if provider_commands:
        kiosk = data.get("kiosk")
        kiosk_locked = isinstance(kiosk, dict) and kiosk.get("enabled") is True
        print(f"  {prefix} providers  — list allowed providers (alias: provs)")
        if not kiosk_locked:
            print(f"  {prefix} provider m3u  — request the M3U provider (alias: prov m3u)")
            print(f"  {prefix} provider offline  — request Local videos (when listed in providers)")
            print(f"  {prefix} provider ID_OR_NAME  — select a listed provider by ID, index or name")
            print(f"  {prefix} provider m3u 1 / R / NAME  — wait for loading, then play by number, random or name")
            print("    Multiple players: ott t1,t2,t3,t4 provider m3u  — request each player in order")
        if provider in ("m3u", "vportal") or (provider == "offline" and "list" in controls.get("offline_profiles", {}).get("operations", [])):
            print(f"  {prefix} profiles  — show profile names; * marks the active profile (alias: profs)")
            if not kiosk_locked and (provider != "offline" or "load" in controls.get("offline_profiles", {}).get("operations", [])):
                print(f"  {prefix} profile N  — select profile 1–15 (alias: prof N)")
                print(f"    Example: {prefix} profile 1  — select the first profile listed by profiles")
            if provider == "m3u" and type(data.get("channels")) is int and data["channels"] == 0:
                print("    No channels loaded: check the active profile with profiles; selecting M3U alone does not choose a profile.")
        offline = controls.get("offline_profiles", {}).get("operations", [])
        if provider == "offline":
            examples = {
                "save": ("profile save 1 Movies", "save local sources and selected video in profile 1"),
                "load": ("profile load 1", "load saved sources and selection; playback follows player settings"),
                "export": ("profile export 1 movies.json", "export configuration without videos or access grants"),
                "import": ("profile import 2 movies.json", "save configuration in profile 2; bind sources locally if needed"),
            }
            for operation, (example, explanation) in examples.items():
                if operation in offline:
                    print(f"  {prefix} {example}  — {explanation}")
        if kiosk_locked:
            print(f"    Provider/profile switching is locked by kiosk; run {prefix} kiosk off first.")
        else:
            print("    Provider/profile commands are checked against player settings and platform policy when run.")
    for operation, (command, description) in lifecycle.items():
        if operation in controls["lifecycle"]:
            print(f"  {prefix} {command}  — {description}")
    for operation in controls["playback"]:
        if operation == "step_channel":
            print(f"  {prefix} +N  — move forward N channels in the current category, wrapping")
            print(f"  {prefix} -N  — move back N channels in the current category, wrapping")
            continue
        command = {"seek": "seek SECONDS", "previous_channel": "prev", "next_channel": "next"}.get(operation, operation)
        print(f"  {prefix} {command}")
    if "random" in controls:
        print(f"  {prefix} R / random  — safe random channel from All; prefer numbers 1–1000")
        print(f"  {prefix} random 1 1000  — safe uniform choice within an inclusive range")
    if controls["input"]:
        print(f"  {prefix} key KEY  — " + ", ".join(controls["input"]))
    shot = controls.get("screenshot", {})
    if shot.get("state") == "ready":
        print(f"  {prefix} screenshot / shot [-o FILE.png]  — capture {shot['source']}")
    elif shot.get("state") == "permission_required":
        print("  " + screenshot_setup_guidance())
    elif shot.get("state") == "unsupported":
        print("  Screenshots are not supported by this player/platform.")
    queue = controls.get("plex_queue", {}).get("operations", [])
    if queue:
        print(f"  {prefix} plex " + " / ".join(op + " ID [ID ...]" if op in ("play", "preview") else op for op in queue))
        print("    next/prev use the retained Plex queue until plex stop clears it; kiosk can repeat its order.")
    libraries = controls.get("plex_library", {}).get("operations", [])
    if "list" in libraries:
        print(f"  {prefix} plex libraries  — list movie/show libraries on the player's Plex server")
    if "play" in libraries:
        print(f'  {prefix} plex shuffle "NAME" / plex library "NAME"  — play up to 500 library items (or id:NUMBER)')
    if "preview" in libraries:
        print(f'  {prefix} plex library --preview "NAME"  — check a whole library without playing')
    categories = controls.get("categories", {}).get("operations", [])
    if "list" in categories:
        print(f"  {prefix} categories / cats  — list media categories; * marks the selected category")
        page_size = controls["categories"]["maxPageSize"]
        print(f"  {prefix} s --limit {page_size} [--offset {page_size}] [TEXT]  — list one page of matching videos")
    if "select" in categories:
        print(f'  {prefix} category "NAME" / cat id:ID  — select a category without starting playback')
        print(f"  {prefix} cat all  — show all videos again; use s to list the selected category")
    favorites = controls.get("favorites", {}).get("operations", [])
    for operation, command, description in (
            ("list", "fav", "list this player's saved favorites"),
            ("add", "fav add [NAME]", "save the current item and profile"),
            ("play", "fav play ID_OR_NAME", "load the saved profile and play the exact item"),
            ("remove", "fav remove ID_OR_NAME", "remove one saved favorite")):
        if operation in favorites:
            print(f"  {prefix} {command}  — {description}")
    aspect = controls.get("aspect", {})
    if "get" in aspect.get("operations", []):
        print(f"  {prefix} aspect  — show current and saved aspect modes")
    if "set" in aspect.get("operations", []):
        print(f"  {prefix} aspect " + " / ".join(aspect["modes"]) + "  — request an aspect mode")
    notifications = controls.get("notifications", {}).get("operations", [])
    if "get" in notifications:
        print(f"  {prefix} notifications  — read the saved banner and camera preference (alias: notif)")
    if "set" in notifications:
        print(f"  {prefix} notifications on / off  — save one preference for banners and camera media")
        print("    Multiple players: ott t1,t2,t3,t4 notifications off")
    update = controls.get("app_update", {}).get("operations", [])
    if update:
        print(f"  {prefix} update status  — read installed and pending APK versions")
        print(f"  {prefix} update --variant api22 --prepare-only  — verify and stage for Android 5.1/6")
        print(f"  {prefix} update --variant full --prepare-only  — verify and stage for Android 7+")
        print(f"  {prefix} update prepare HTTPS_APK_URL SHA256  — stage a specific APK")
        print(f"  {prefix} update install SHA256  — request installation of the staged APK")
        print("    Android confirmation may be required. Acceptance does not confirm installation; read update status after reopening the app.")
    if (not provider_commands and not any(controls[key] for key in ("lifecycle", "playback", "input"))
            and shot.get("state") != "ready" and not queue and not libraries and not categories and not favorites and not aspect.get("operations") and not notifications and not update):
        print("  No controls are currently advertised by the player.")
    sections = controls.get("inspect", {}).get("sections", [])
    if sections or "debug" in controls:
        print("\nAvailable diagnostics now:")
        if "debug" in controls:
            print(f"  {prefix} debug  — read bounded runtime and native diagnostics")
        if "doctor" in sections:
            print(f"  {prefix} doctor  — read runtime health and identity")
        if "snapshot" in sections:
            print(f"  {prefix} inspect --view ui,media  — read interface and decoder state")
            print(f"  {prefix} bundle --out DIR  — save diagnostic evidence")
            print(f"  {prefix} test run health --report DIR  — check observation lanes")
            print(f"  {prefix} test run media-progress --report DIR  — observe decoder progress")
        if "operation" in sections:
            print(f"  {prefix} operation REQUEST_ID  — inspect a recent operation")
    print(f"Local diagnostic help: {prefix} test list")
    print(f"Request IDs: ott --receipt {quoted_name} COMMAND  — write RPC receipts to stderr")


def status_text(value, pattern=None):
    """Keep display names short and single-line; never render a URL as a title."""
    if not isinstance(value, str) or not value or len(value) > 512:
        return "Not reported"
    if pattern is not None:
        return value if re.fullmatch(pattern, value) else "Not reported"
    if re.search(r"[a-z][a-z0-9+.-]*://|www\.|\b(?:token|password|authorization)=", value, re.I):
        return "Not reported"
    value = " ".join("".join(" " if unicodedata.category(char)[0] == "C" else char
                             for char in value).split())
    return (value[:157] + "..." if len(value) > 160 else value) or "Not reported"


def print_player_summary(data, name):
    """Project only known public status fields; catalog readiness is not playback."""
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    standby = player.get("standby")
    state = "Standby" if standby is True else "Awake" if standby is False else "Connected (standby not reported)"
    provider = status_text(data.get("provider"), r"[a-z0-9_-]{1,32}")
    provider = {"m3u": "M3U", "vportal": "VPortal", "plex": "Plex", "stalker": "Stalker",
                "offline": "Local videos (offline)"}.get(provider, provider)
    volume = data.get("volume")
    volume = f"{volume:g}%" if type(volume) in (int, float) and 0 <= volume <= 100 and math.isfinite(volume) else "Not reported"
    channels = data.get("channels")
    channels = str(channels) if type(channels) is int and 0 <= channels <= 2147483647 else "Not reported"
    ready = data.get("ready")
    catalog = "ready" if ready is True else "not ready" if ready is False else "readiness not reported"
    background = player.get("background") if isinstance(player.get("background"), dict) else {}
    activity = background.get("state")
    if activity not in ("running", "suspending", "suspended", "unsupported", "error"):
        activity = "Not reported"
    pending = background.get("pending")
    pending = str(pending) if type(pending) is int and 0 <= pending <= 2147483647 else "unknown"
    kiosk = data.get("kiosk") if isinstance(data.get("kiosk"), dict) else {}
    enabled = kiosk.get("enabled")
    kiosk_state = ("On (strict)" if kiosk.get("strict") is True else "On") if enabled is True else "Off" if enabled is False else "Not reported"
    rows = [
        ("Player", status_text(name)),
        ("Status", state),
        ("Now playing", "Nothing (standby)" if standby is True else "Not reported"),
        ("Provider", provider),
        ("Volume", volume),
        ("Channels", channels + " (catalog " + catalog + ")"),
        ("Platform", status_text(player.get("platform"), r"[a-z0-9_-]{1,32}")),
        ("Version", status_text(player.get("version"), r"[0-9][A-Za-z0-9_.+\-]{0,63}")),
        ("Background", activity + " (" + pending + " pending)"),
        ("Kiosk", kiosk_state),
    ]
    if enabled is True:
        channel, media = kiosk.get("channel"), kiosk.get("media")
        target = channel.get("name") if isinstance(channel, dict) else media.get("title") if isinstance(media, dict) else None
        rows.append(("Kiosk target", status_text(target)))
    print("\nPlayer summary:")
    for label, value in rows:
        print(f"  {label + ':':<14}{value}")


def print_player_status(data, name):
    print_player_help(data, name)
    print_player_summary(data, name)


def deferred_control_metadata(data, action, params):
    field = "key" if action == "input" else "operation"
    if (not isinstance(data, dict) or data.get(field) != params[field] or data.get("accepted") is not True or
            data.get("dispatched") is not False or data.get("effect") != action + "-after-ack"):
        raise Error("The player did not confirm the " + action + " request. It may have executed; do not repeat the change blindly.")
    return {field: params[field], "accepted": True, "dispatched": False, "effect": action + "-after-ack"}


def playback_metadata(data, params):
    invalid = "The player did not confirm the playback request. It may have executed; do not repeat the change blindly."
    if not isinstance(data, dict) or data.get("operation") != params["operation"] or data.get("dispatched") is not True:
        raise Error(invalid)
    result = {"operation": params["operation"], "dispatched": True}
    if "plex_queue" in data:
        if params["operation"] not in CHANNEL_STEPS.values() or set(data) != {"operation", "dispatched", "plex_queue"}:
            raise Error(invalid)
        result["plex_queue"] = plex_queue_metadata(data["plex_queue"])
        return result
    if params["operation"] == "step_channel":
        if type(data.get("offset")) is not int or data["offset"] != params["offset"]:
            raise Error(invalid)
        result["offset"] = data["offset"]
    if params["operation"] in CHANNEL_OPERATIONS:
        channel = data.get("channel")
        if (not isinstance(channel, dict) or channel_identity(channel.get("id")) is None
                or type(channel.get("number")) is not int or not 0 < channel["number"] <= 9007199254740991
                or not epg_text(channel.get("name"), 16384) or not channel["name"].strip()):
            raise Error(invalid)
        result["channel"] = {key: channel[key] for key in ("id", "number", "name")}
    if params["operation"] == "seek":
        position = data.get("position")
        if type(position) not in (int, float) or position != params["position"] or not math.isfinite(position) or position < 0:
            raise Error(invalid)
        result["position"] = position
    return result


def vportal_metadata(data, playing, shuffled=False):
    """Validate queue acknowledgement and expose only public video metadata."""
    message = "The player returned invalid VPortal metadata"
    if playing:
        message += ". The request may have executed; do not repeat the change blindly."
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise Error(message)
    items = data["items"]
    if type(data.get("total")) is not int or data["total"] != len(items):
        raise Error(message)
    if playing and (not items or data.get("loop") is not True or data.get("dispatched") is not True):
        raise Error(message)
    if shuffled and data.get("shuffled") is not True:
        raise Error("The player did not confirm shuffling the VPortal queue. Update the player; the request may have executed, so do not repeat it blindly.")
    public = []
    for number, row in enumerate(items, 1):
        if (not isinstance(row, dict) or type(row.get("number")) is not int or row["number"] != number
                or not isinstance(row.get("title"), str)):
            raise Error(message)
        public.append({"number": number, "title": row["title"]})
    result = {"items": public, "total": len(public)}
    if playing:
        result.update(loop=True, dispatched=True)
    if shuffled:
        result["shuffled"] = True
    return result


def provision_kubernetes(client, config_path, name, device, *, resume_only=False):
    kube = client.config["kubernetes"]
    journal_path = Path(config_path).expanduser().with_name(Path(config_path).name + ".pending-add.json")
    identity = {"name": name.casefold(), "device": device, "kubernetes": kube, "server": client.server,
                "server_config": str(Path(client.config["server_config"]).expanduser().resolve())}
    resuming = journal_path.exists()
    if resume_only and not resuming:
        raise Error("The pending-add.json is missing; a new addition with a reserved name was not started")
    journal = read_json(journal_path) if resuming else None
    if resuming:
        if (not isinstance(journal, dict) or set(journal) != {"version", "identity", "before", "after"}
                or type(journal["version"]) is not int or journal["version"] != 1 or not isinstance(journal["identity"], dict)
                or not all(isinstance(journal[field], dict) and isinstance(journal[field].get("devices"), list)
                           for field in ("before", "after"))):
            raise Error("Invalid pending-add.json; reconcile it with the configuration before recovery")
        if journal.get("identity") != identity or client.credentials not in (journal.get("before"), journal.get("after")):
            raise Error("An unfinished ott add belongs to another configuration; complete it or reconcile pending-add.json first")
    else:
        updated = json.loads(json.dumps(client.credentials))
        updated["devices"].append({"id": device, "token": secrets.token_urlsafe(32)})
        journal = {"version": 1, "identity": identity, "before": client.credentials, "after": updated}
    base = ["kubectl", "--context", kube["context"], "-n", kube["namespace"], "--request-timeout=15s"]

    def current_config(require_alias=False):
        # kubectl/rollout may take seconds: preserve edits made meanwhile, but
        # never bind this operation's token to another server or target alias.
        current = read_json(config_path)
        try:
            same_target = (isinstance(current, dict) and current.get("server", "").rstrip("/") == identity["server"]
                           and current.get("kubernetes") == identity["kubernetes"]
                           and str(Path(current["server_config"]).expanduser().resolve()) == identity["server_config"])
        except (AttributeError, KeyError, TypeError, ValueError):
            same_target = False
        if not same_target:
            raise Error("The server address or Kubernetes/server_config settings in cli.json changed; reconcile pending-add.json before continuing")
        aliases = current.get("players", {})
        if not isinstance(aliases, dict):
            raise Error("The players mapping in cli.json changed; reconcile pending-add.json before continuing")
        matching = [key for key in aliases if key.casefold() == name.casefold()]
        if (len(matching) > 1 or any(aliases[key] != device for key in matching)
                or (require_alias and not matching)):
            raise Error("The target alias in cli.json changed; reconcile pending-add.json before continuing")
        current.setdefault("players", {})[matching[0] if matching else name] = device
        return current

    try:
        resource = json.loads(subprocess.check_output(base + ["get", "secret", kube["secret"], "-o", "json"], timeout=20, stderr=subprocess.PIPE))
        live = json.loads(base64.b64decode(resource["data"]["config.json"]))
        if live not in (journal["before"], journal["after"]):
            raise Error("The cluster configuration changed; reconcile server_config and pending-add.json before retrying")
        if not resuming:
            # Save the generated credential before the first external mutation.
            # A lost kubectl response can then be reconciled against the live Secret.
            write_private(journal_path, journal, exclusive=True)
        if live != journal["after"]:
            resource["data"]["config.json"] = base64.b64encode(json.dumps(journal["after"]).encode()).decode()
            subprocess.run(base + ["replace", "-f", "-"], input=json.dumps(resource), text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        write_private(client.config["server_config"], journal["after"])
        client.credentials = journal["after"]
        client.config = current_config()
        write_private(config_path, client.config)
        subprocess.run(base + ["rollout", "restart", "deployment/" + kube["deployment"]], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        subprocess.run(base + ["rollout", "status", "deployment/" + kube["deployment"], "--timeout=90s"], check=True, stdout=sys.stderr, stderr=subprocess.PIPE, timeout=100)
        client.config = current_config(require_alias=True)
        journal_path.unlink()
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise Error(f"Device addition is incomplete. Retry ott add {name} {device}; the saved access code will be reused. Do not delete pending-add.json before reconciliation.") from exc


def server_debug_metadata(data):
    invalid = "The controller returned invalid debug metadata"
    schema = {
        "process": "uptimeMs goroutines heapAllocBytes heapSysBytes".split(),
        "control": "devices queues pending queueBytes resultEntries resultBytes commandTtlMs maxPendingPerDevice".split(),
        "diagnostics": "runtimes sessions repairs eventBytes reservedStops controlSlotsUsed controlSlotsCapacity eventSlotsUsed eventSlotsCapacity".split(),
    }
    integer = lambda value: type(value) is int and 0 <= value <= 9007199254740991
    if (not isinstance(data, dict) or set(data) != {"version", "sampledAt", "consistent", *schema}
            or type(data.get("version")) is not int or data["version"] != 1
            or not integer(data.get("sampledAt")) or data.get("consistent") is not False):
        raise Error(invalid)
    for name, keys in schema.items():
        row = data[name]
        if (not isinstance(row, dict) or set(row) != set(keys) | ({"configured"} if name == "diagnostics" else set())
                or any(not integer(row[key]) for key in keys)):
            raise Error(invalid)
    diagnostic = data["diagnostics"]
    if (type(diagnostic["configured"]) is not bool
            or diagnostic["controlSlotsUsed"] > diagnostic["controlSlotsCapacity"]
            or diagnostic["eventSlotsUsed"] > diagnostic["eventSlotsCapacity"]):
        raise Error(invalid)
    return data


def home_assistant_metadata(raw):
    """Project only public health fields; never print server configuration."""
    invalid = "The controller returned invalid Home Assistant status"
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise Error(invalid)
    bridge = raw.get("bridge")
    states = {"disabled", "connecting", "authenticating", "subscribing", "connected",
              "reconnecting", "auth_failed", "token_error", "configuration_error", "stopped"}
    if (not isinstance(bridge, dict) or type(bridge.get("enabled")) is not bool
            or not isinstance(bridge.get("state"), str) or bridge["state"] not in states):
        raise Error(invalid)
    result = {"version": 1, "bridge": {"enabled": bridge["enabled"], "state": bridge["state"]}, "players": []}
    count = lambda value: type(value) is int and 0 <= value <= 9007199254740991
    for key in ("reconnects", "events_received", "events_matched", "notifications_queued", "notifications_skipped", "events_dropped",
                "camera_acquired", "camera_failed", "camera_skipped"):
        if key in bridge:
            if not count(bridge[key]):
                raise Error(invalid)
            result["bridge"][key] = bridge[key]
    errors = {"invalid_configuration", "token_file_unavailable", "authentication_rejected",
              "authentication_protocol_error", "connection_failed", "connection_lost", "heartbeat_timeout",
              "message_too_large", "subscription_protocol_error", "subscription_rejected"}
    if bridge.get("last_error_code"):
        if not isinstance(bridge["last_error_code"], str) or bridge["last_error_code"] not in errors:
            raise Error(invalid)
        result["bridge"]["last_error_code"] = bridge["last_error_code"]
    if "delivery" in raw:
        delivery = raw["delivery"]
        if not isinstance(delivery, dict) or any(not count(delivery.get(key)) for key in ("queued", "polled", "skipped", "dropped")):
            raise Error(invalid)
        result["delivery"] = {key: delivery[key] for key in ("queued", "polled", "skipped", "dropped")}
    players = raw.get("players")
    if not isinstance(players, list) or len(players) > 64:
        raise Error(invalid)
    seen = set()
    for player in players:
        if (not isinstance(player, dict) or not isinstance(player.get("device_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", player["device_id"])
                or player["device_id"] in seen or type(player.get("active")) is not bool
                or not count(player.get("pending")) or player["pending"] > 4):
            raise Error(invalid)
        seen.add(player["device_id"])
        result["players"].append({key: player[key] for key in ("device_id", "active", "pending")})
        for key in ("notifications_enabled", "media_jpeg"):
            if key in player:
                if type(player[key]) is not bool:
                    raise Error(invalid)
                result["players"][-1][key] = player[key]
    return result


def registry_management(client, config_path, words, json_output=False):
    path = Path(__file__).resolve().with_name("device_registry.py")
    if not path.is_file():
        raise Error("Install device_registry.py beside ott.py from the same release")
    spec = importlib.util.spec_from_file_location("ott_device_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.dispatch(SimpleNamespace(**globals()), client, config_path, words, json_output)


def management(client, config_path, words, json_output=False):
    verb = words[0].casefold()
    registry_verbs = ("registry", "remove", "unalias", "add", "alias", "pair", "approve")
    legacy_reserved = verb in ("add", "alias") and len(words) > 1 and words[1].casefold() in TOP_LEVEL_COMMANDS
    if verb in registry_verbs and not legacy_reserved and registry_management(client, config_path, words, json_output):
        return True
    if verb == "ha":
        tail = [word for word in words[1:] if word not in ("-j", "--json")]
        if len(tail) != 1 or tail[0].casefold() != "status":
            raise Error("Use ott ha status [-j]")
        try:
            status, raw = client.api("/api/home-assistant/status")
        except HTTPError as exc:
            if exc.code in (404, 405):
                raise Error("This controller does not support Home Assistant status; update the controller") from None
            raise
        if status != 200:
            raise Error("The controller returned invalid Home Assistant status")
        data = home_assistant_metadata(raw)
        if json_output or any(word in ("-j", "--json") for word in words):
            print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
        else:
            print("Home Assistant: " + data["bridge"]["state"])
            if "last_error_code" in data["bridge"]:
                print("Reason: " + data["bridge"]["last_error_code"])
            for player in data["players"]:
                preference = ("; notifications=" + ("on" if player["notifications_enabled"] else "off")) if "notifications_enabled" in player else ""
                print(f"  {player['device_id']}: {'active' if player['active'] else 'inactive or not reporting'}; pending={player['pending']}" + preference)
            if "delivery" in data:
                delivery = data["delivery"]
                print(f"Banners: queued={delivery['queued']}, polled={delivery['polled']}, skipped={delivery['skipped']}, dropped={delivery['dropped']}")
            print("Queued/polled does not confirm that a banner was displayed.")
        return True
    if verb == "server":
        tail = [word for word in words[1:] if word not in ("-j", "--json")]
        if len(tail) != 1 or tail[0].casefold() != "debug":
            raise Error("Use ott server debug [-j]")
        try:
            status, raw = client.api("/api/debug")
        except HTTPError as exc:
            if exc.code == 404:
                raise Error("This controller does not support server debug") from None
            raise
        if status != 200:
            raise Error("The controller returned invalid debug metadata")
        data = server_debug_metadata(raw)
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
        return True
    if verb == "discover" and len(words) == 1:
        _, data = client.api("/api/discovery")
        if json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for row in data["servers"]:
                print(f"{clean(row['id'])}  {clean(row['address'])}  domain={clean(row['domain'])}")
            if not data["servers"]:
                print("No OTT-play controllers are advertised in network DNS.")
        return True
    if verb in ("pending", "approve"):
        if (verb == "pending" and len(words) != 1) or (verb == "approve" and len(words) != 3):
            raise Error("Use ott pending or ott approve NAME CODE")
        device = client.device(words[1]) if verb == "approve" else None
        code = words[2].upper() if verb == "approve" else None
        if code is not None and not re.fullmatch(r"[A-Z0-9]{8}", code):
            raise Error("Enter the eight-character pairing code displayed by the player")
        _, data = client.api("/api/pairings")
        rows = data.get("pairings")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise Error("The server returned an invalid pairing list")
        if verb == "pending":
            if json_output:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    aliases = [key for key, value in client.config.get("players", {}).items() if value == row["device_id"]]
                    print(f"{clean(','.join(aliases) or row['device_id'])}  code={clean(row['code'])}  {clean(row['address'])}  expires_in={row['expires_in']}s")
                if not rows:
                    print("No players are waiting for pairing approval.")
            return True
        matches = [row for row in rows if row.get("device_id") == device and row.get("code") == code]
        if len(matches) != 1:
            raise Error("No unique pending request matches this player and code. Check the code on the player, then run ott pending")
        row = matches[0]
        if not isinstance(row.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", row["id"]):
            raise Error("The server returned an invalid pairing ID")
        try:
            status, result = client.api("/api/pairings/approve", {"id": row["id"], "code": code})
        except Error as exc:
            if isinstance(exc, HTTPError) and exc.code < 500:
                raise
            raise Error("Approval may have succeeded. Check the player's connection and ott pending before trying again") from exc
        if status != 200 or not isinstance(result, dict) or result.get("status") != "approved":
            raise Error("The server did not confirm approval. Check the player's connection before trying again")
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Pairing approved for {clean(words[1])}. The player will save its own access code and connect automatically.")
        return True
    if verb == "devices" and len(words) == 1:
        _, data = client.api("/api/devices")
        for row in data["devices"]:
            aliases = [key for key, value in client.config.get("players", {}).items() if value == row["id"]]
            print(f"{','.join(aliases) or '—'}  {row['id']}  last_seen={row.get('last_seen') or 'never'}  pending={row['pending']}")
        return True
    if verb == "pair" and len(words) == 2:
        device = client.device(words[1])
        row = next((x for x in client.credentials["devices"] if x["id"] == device), None)
        if not row:
            raise Error("No local access code is available for this device")
        print("Settings → Remote control → Command server")
        print("Address:", client.config.get("player_server", client.server))
        print("Access code:", row["token"])
        print("Device UUID / queue:", device)
        print("Select Connect. Do not use this access code in another player.")
        return True
    if verb in ("alias", "add") and len(words) == 3:
        name, device = words[1:]
        if not re.fullmatch(r"[a-zA-Z0-9\u0430-\u044f\u0410-\u042f\u0451\u0401_-]{1,32}", name):
            raise Error("Choose a name of up to 32 letters/digits, underscores or hyphens, without spaces")
        reserved = name.casefold() in TOP_LEVEL_COMMANDS
        journal_path = Path(config_path).expanduser().with_name(Path(config_path).name + ".pending-add.json")
        # Old CLIs permitted some reserved names. Recovery must validate the
        # existing journal, and must never start a fresh addition if it vanished.
        if reserved and not (verb == "add" and client.config.get("kubernetes") and journal_path.exists()):
            raise Error(f"The name {name!r} is reserved for a CLI command. Choose another player alias")
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", device):
            raise Error("Invalid device UUID")
        aliases = client.config.setdefault("players", {})
        existing = next((key for key in aliases if key.casefold() == name.casefold()), name)
        if existing in aliases and aliases[existing] != device:
            raise Error("The name is already bound to another UUID; edit cli.json to reassign it")
        registered = any(x["id"] == device for x in client.credentials["devices"])
        if verb == "add" and client.config.get("kubernetes") and (reserved or not registered or journal_path.exists()):
            if not registered and len(client.credentials["devices"]) >= 64:
                raise Error("The limit of 64 devices has been reached")
            provision_kubernetes(client, config_path, existing, device, resume_only=reserved)
            print(f"{existing} → {device}. Connection settings: ott pair {existing}")
            return True
        elif not registered:
            if verb == "alias":
                raise Error("The UUID is not registered yet. Use ott add NAME UUID")
            if len(client.credentials["devices"]) >= 64:
                raise Error("The limit of 64 devices has been reached")
            updated = json.loads(json.dumps(client.credentials))
            updated["devices"].append({"id": device, "token": secrets.token_urlsafe(32)})
            write_private(client.config["server_config"], updated)
            print("Server configuration updated. Restart the command server.", file=sys.stderr)
        aliases[existing] = device
        write_private(config_path, client.config)
        print(f"{existing} → {device}. Connection settings: ott pair {existing}")
        return True
    return False


def resolve_main(argv):
    """Private, read-only IPC for launchers; never load controller credentials."""
    help_text = """ott resolve --request-stdin
Read one version 1 JSON search request from stdin and return one JSON result.
The result contains private media URLs: stdout must be redirected or captured.
No controller configuration, player RPC or playback is used.
See docs/cli.md for the request and result contract.
"""
    if argv in (["--help"], ["-h"]):
        print(help_text, end="")
        return 0

    def failure(code, message, status=1):
        # Never interpolate request data, module exceptions or URLs into errors.
        if sys.stdout.isatty():
            print("Error: " + message, file=sys.stderr)
        else:
            print(json.dumps({"version": 1, "error": {"code": code, "message": message}}))
        return status

    if argv != ["--request-stdin"]:
        return failure("invalid_request", "Use ott resolve --request-stdin with a version 1 JSON request.")
    if sys.stdout.isatty():
        return failure("private_output_required", "Capture or redirect resolver output; it contains private media URLs.")
    if sys.stdin.isatty():
        return failure("invalid_request", "Send the resolver JSON request through standard input.")
    try:
        limit = 64 * 1024
        source = getattr(sys.stdin, "buffer", sys.stdin)
        raw = source.read(limit + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > limit:
            return failure("invalid_request", "The resolver request exceeds the 64 KiB limit.")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
            return failure("invalid_request", "The resolver requires a version 1 JSON object.")
    except KeyboardInterrupt:
        return failure("interrupted", "The resolver search was interrupted.", 130)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return failure("invalid_request", "The resolver requires a valid UTF-8 JSON request.")
    try:
        module_path = Path(__file__).resolve().with_name("playlist_search.py")
        if not module_path.is_file() or not module_path.with_name("programme_search.py").is_file():
            return failure("resolver_unavailable", "Install playlist_search.py and programme_search.py beside ott.py.")
        spec = importlib.util.spec_from_file_location("ott_playlist_search", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.resolve(request)
        if (not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1
                or result.get("kind") not in ("playlist", "live", "archive", "none")
                or not isinstance(result.get("matches"), list)):
            raise ValueError("Invalid resolver response")
        # ASCII escapes keep the UTF-8 JSON wire format independent of the
        # host's pipe encoding (for example Windows cp1252).
        encoded = json.dumps(result, ensure_ascii=True)
        if len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("Oversized resolver response")
    except KeyboardInterrupt:
        return failure("interrupted", "The resolver search was interrupted.", 130)
    except Exception:
        # This boundary receives provider data and private URLs. Even unexpected
        # failures must not expose them in a traceback or exception message.
        return failure("resolution_failed", "Could not resolve the playlist search. Check the source settings and network.")
    print(encoded)
    return 0



def android_update_metadata(value, agent_version):
    """Validate installed component evidence; never project paths or command output."""
    def require(ok):
        if not ok:
            raise Error("Invalid native update metadata")

    require(isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1)
    result = {"version": 1}
    if "bundle_state" in value:
        require(value["bundle_state"] in ("absent", "pending", "committed", "rolled_back", "unavailable"))
        result["bundle_state"] = value["bundle_state"]
    if "recovery_failed" in value:
        require(type(value["recovery_failed"]) is bool)
        result["recovery_failed"] = value["recovery_failed"]
    for name in ("agent", "apk", "touch_guard"):
        row = value.get(name)
        require(isinstance(row, dict) and row.get("state") in ("ready", "unavailable"))
        clean_row = {"state": row["state"]}
        if name == "agent":
            require(isinstance(row.get("version"), str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", row["version"])
                    and row["version"] == agent_version)
            clean_row["version"] = row["version"]
        if name == "apk":
            require(row.get("package") == "play.ott.kitkat")
            clean_row["package"] = row["package"]
        if row["state"] == "ready":
            require(isinstance(row.get("sha256"), str)
                    and re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) and "reason" not in row)
            clean_row["sha256"] = row["sha256"]
            if name == "apk":
                require(isinstance(row.get("version_name"), str)
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+\-]{0,63}", row["version_name"])
                        and type(row.get("version_code")) is int and 1 <= row["version_code"] <= 2147483647
                        and row.get("app_state") in ("enabled", "disabled", "unavailable"))
                clean_row.update({key: row[key] for key in ("version_name", "version_code", "app_state")})
        else:
            reasons = ("file_unavailable", "package_metadata_unavailable", "package_changed") if name == "apk" else ("file_unavailable",)
            require(row.get("reason") in reasons
                    and not set(row).intersection(("sha256", "version_name", "version_code", "app_state")))
            clean_row["reason"] = row["reason"]
        result[name] = clean_row
    return result


def android_metadata(data, action, params):
    """Project public agent metadata; never print arbitrary diagnostics/URLs."""
    invalid = "Invalid native receipt; inspect android status and do not repeat blindly"
    number = lambda v: type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 9007199254740991
    boolean = lambda v: type(v) is bool
    text = lambda v: isinstance(v, str) and len(v) <= 1024
    token = lambda v: isinstance(v, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", v)
    runtime = lambda v: isinstance(v, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,96}", v)
    boot_id = lambda v: v == "" or isinstance(v, str) and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", v)
    identity = {"version": number, "available": boolean, "web_runtime": runtime,
                "generation": number, "handle_id": number, "captured_at": number,
                "consistent": boolean, "kind": lambda v: v in ("live", "archive", "vod", "unknown")}
    evidence_state = lambda v: v in ("observed", "unavailable")
    evidence_reason = lambda v: v in ("no_frame_timestamps", "unsupported_dump", "no_completed_fences", "too_many_tracks", "no_matching_audio_track", "app_surface_unavailable", "audio_service_unavailable", "process_changed")
    system_evidence = {
        "app_pid": number, "captured_uptime_seconds": number,
        "physical_display_verified": lambda v: v is False, "physical_audio_verified": lambda v: v is False,
        "surface": {"state": evidence_state, "reason": evidence_reason,
                    "scope": lambda v: v == "app_surface", "period_ns": number,
                    "latest_present_ns": number, "completed_frames": number, "video_verified": lambda v: v is False},
        "audio": {"state": evidence_state, "reason": evidence_reason, "scope": lambda v: v == "app_process",
                  "audible_verified": lambda v: v is False,
                  "tracks": [{"session_id": number, "sample_rate": number, "server_frames": number,
                              "underrun_frames": number, "active": boolean}]}}
    decoder = {"source": lambda v: v == "html_video", "decoded_frames": number, "dropped_frames": number,
               "audio_decoded_bytes": number, "volume": lambda v: number(v) and v <= 1,
               "muted": boolean, "presented_frames": lambda v: v is None, "audible_verified": lambda v: v is False}
    item = {"id": number, "title": text}
    queue = {"index": number, "total": number, "items": [item]}
    receipt = {"request_id": lambda v: isinstance(v, str) and re.fullmatch(r"[a-f0-9]{32}", v),
               "action": lambda v: v in ("lifecycle", "maintenance", "playback", "vportal_queue"),
               "operation": lambda v: v in ("restart_app", "reload_player", "reboot_device", "wake", "standby", "recover_video", "update", "pause", "resume", "seek", "play", "next", "previous", "restart", "stop"),
               "runtime": runtime, "boot_id": boot_id,
               "state": lambda v: v in ("started", "accepted", "claimed", "handler_completed", "failed", "rejected", "unknown"),
               "updated_at": number, "evidence": lambda v: v in ("none", "handler_completed")}
    schema = {
        "version": number, "agent_version": token, "runtime": runtime, "boot_id": boot_id, "app_pid": number,
        "uptime_seconds": number, "battery_percent": number, "watchdog_suspended": boolean,
        "watchdog_attempts": number, "webview_responsive": boolean, "system_evidence": system_evidence,
        "operation_history_reset": boolean,
        "last_operation": {**receipt, "operation": token, "state": token}, "operations": [receipt],
        "events": [{"time": number, "event": token, "runtime": runtime, "boot_id": boot_id}],
        "player": {
            "ready": boolean, "provider": token, "touch": token, "identity": identity, "decoder": decoder,
            "kiosk": {"enabled": boolean, "state": token, "health": token,
                      "provider": token, "strict": boolean, "retries": number,
                      "retry_seconds": number, "startup_grace_seconds": number,
                      "media": {"index": number, "total": number, "title": text},
                      "channel": {"id": text, "name": text}},
            "video": {"position": number, "duration": number, "paused": boolean,
                      "ended": boolean, "ready": number, "error": number,
                      "width": number, "height": number, "source": token},
            "queue": queue},
        "operation": token, "accepted": boolean, "completion": token,
        "dispatched": boolean, "position": number, "stopped": boolean,
        "loop": boolean, "title": text, **queue,
    }
    def require(condition, message=invalid):
        if not condition:
            raise Error(message)

    def integer(value, low=0, high=9007199254740991):
        return type(value) is int and low <= value <= high

    def complete(value, spec, nullable=(), message=invalid):
        require(isinstance(value, dict) and all(key in value for key in spec), message)
        result = {}
        for key, rule in spec.items():
            if value[key] is None:
                require(key in nullable, message)
                result[key] = None
            else:
                try:
                    result[key] = project(value[key], rule)
                except Error:
                    raise Error(message) from None
        return result

    def checked_identity(value):
        result = complete(value, identity, ("web_runtime", "generation", "handle_id"))
        require(type(result["version"]) is int and result["version"] == 1
                and integer(result["captured_at"]))
        if result["available"]:
            require(result["consistent"] is True and result["web_runtime"] is not None
                    and integer(result["generation"]) and integer(result["handle_id"])
                    and result["captured_at"] > 0 and result["kind"] in ("live", "archive", "vod"))
        else:
            require(result["consistent"] is False and result["generation"] is None
                    and result["handle_id"] is None and result["kind"] == "unknown")
        return result

    def checked_surface(value):
        require(isinstance(value, dict))
        if value.get("state") == "unavailable":
            result = complete(value, {"state": evidence_state, "reason": evidence_reason})
            require(result["reason"] in ("no_frame_timestamps", "unsupported_dump", "no_completed_fences",
                                         "app_surface_unavailable", "process_changed")
                    and not set(value).intersection(("scope", "period_ns", "latest_present_ns", "completed_frames", "video_verified")))
            return result
        result = complete(value, {key: rule for key, rule in system_evidence["surface"].items() if key != "reason"})
        require(result["state"] == "observed" and "reason" not in value
                and integer(result["period_ns"], 1, 1000000000)
                and integer(result["latest_present_ns"], 1, 9007199254740990)
                and integer(result["completed_frames"], 1))
        return result

    def checked_audio(value):
        require(isinstance(value, dict))
        if value.get("state") == "unavailable":
            result = complete(value, {"state": evidence_state, "reason": evidence_reason})
            require(result["reason"] in ("unsupported_dump", "too_many_tracks", "no_matching_audio_track",
                                         "audio_service_unavailable", "process_changed")
                    and not set(value).intersection(("scope", "tracks", "audible_verified")))
            return result
        result = complete(value, {key: rule for key, rule in system_evidence["audio"].items() if key != "reason"})
        require(result["state"] == "observed" and "reason" not in value and 1 <= len(result["tracks"]) <= 16)
        result["tracks"] = [complete(row, system_evidence["audio"]["tracks"][0]) for row in result["tracks"]]
        require(all(integer(row[key], 0, 4294967295)
                    for row in result["tracks"] for key in ("session_id", "sample_rate", "server_frames", "underrun_frames")))
        return result

    def checked_system(value):
        require(isinstance(value, dict) and "surface" in value and "audio" in value)
        surface, audio = checked_surface(value["surface"]), checked_audio(value["audio"])
        changed = {"state": "unavailable", "reason": "process_changed"}
        # The producer deliberately drops PID/clock claims after a process change.
        if surface == changed or audio == changed:
            require(surface == audio == changed and not set(value).intersection(
                ("app_pid", "captured_uptime_seconds", "physical_display_verified", "physical_audio_verified")))
            return {"surface": surface, "audio": audio}
        result = complete(value, {key: rule for key, rule in system_evidence.items() if key not in ("surface", "audio")})
        require(integer(result["app_pid"], 0, 2147483647) and integer(data.get("app_pid"), 0, 2147483647)
                and result["app_pid"] == data["app_pid"])
        require(result["app_pid"] > 0 or surface["state"] == audio["state"] == "unavailable")
        return {**result, "surface": surface, "audio": audio}

    def checked_receipt(value):
        message = "Invalid native operation receipt"
        result = complete(value, {key: rule for key, rule in receipt.items() if key != "boot_id"}, message=message)
        if "boot_id" in value:
            require(value["boot_id"] is not None and boot_id(value["boot_id"]), message)
            result["boot_id"] = value["boot_id"]
        operations = {
            "maintenance": ("recover_video", "update"),
            "lifecycle": ("restart_app", "reload_player", "reboot_device", "wake", "standby"),
            "playback": ("pause", "resume", "seek"),
            "vportal_queue": ("play", "next", "previous", "restart", "stop"),
        }
        require(result["operation"] in operations[result["action"]]
                and integer(result["updated_at"], 1)
                and (result["state"] == "handler_completed") == (result["evidence"] == "handler_completed"), message)
        # Retention is enforced by the producer's clock. Historical executor
        # runtime/boot IDs need not match this health response's current process.
        return result

    def project(value, spec):
        if spec is identity:
            return checked_identity(value)
        if spec is decoder:
            return complete(value, decoder, ("decoded_frames", "dropped_frames", "audio_decoded_bytes",
                                              "volume", "muted", "presented_frames"))
        if spec is system_evidence:
            return checked_system(value)
        if spec is receipt:
            return checked_receipt(value)
        if value is None:
            return None
        if isinstance(spec, dict):
            if not isinstance(value, dict):
                raise Error(invalid)
            return {key: project(value[key], rule) for key, rule in spec.items() if key in value}
        if isinstance(spec, list):
            # The web player can retain a whole series (up to 1,000 rows).
            # The 100-ID native play limit does not limit reading that queue.
            limit = 1000 if spec is queue["items"] else 100
            if not isinstance(value, list) or len(value) > limit:
                raise Error(invalid)
            return [project(row, spec[0]) for row in value]
        if not spec(value):
            raise Error(invalid)
        return clean(value) if isinstance(value, str) else value
    if not isinstance(data, dict):
        raise Error(invalid)
    result = project(data, schema)
    if "updates" in data:
        result["updates"] = android_update_metadata(data["updates"], result.get("agent_version"))
    if "operations" in result:
        history = result["operations"]
        require(isinstance(history, list) and len(history) <= 64, "Invalid native operation history")
        require(len({row["request_id"] for row in history}) == len(history), "Ambiguous native operation history")
    op = params.get("operation")
    if action == "maintenance" and op in ("health", "logs"):
        if type(result.get("version")) is not int or result["version"] != 1:
            raise Error(invalid)
        if op == "health" and (not isinstance(result.get("runtime"), str) or
                               type(result.get("webview_responsive")) is not bool):
            raise Error(invalid)
        if op == "logs" and not isinstance(result.get("events"), list):
            raise Error(invalid)
    elif result.get("operation") != op:
        raise Error(invalid)
    elif action == "lifecycle" or op == "update":
        if result.get("accepted") is not True or result.get("completion") != "inspect_status":
            raise Error(invalid)
    elif action == "vportal_queue" and op == "status":
        if type(result.get("total")) is not int or not isinstance(result.get("items"), list) or result["total"] != len(result["items"]):
            raise Error(invalid)
    elif action == "vportal_queue" and op == "play":
        items = result.get("items")
        if (result.get("dispatched") is not True or result.get("loop") is not True or
                type(result.get("total")) is not int or result["total"] != len(params["ids"]) or
                not isinstance(items, list) or [row.get("id") for row in items] != params["ids"]):
            raise Error(invalid)
    elif op == "stop":
        if result.get("stopped") is not True:
            raise Error(invalid)
    elif result.get("dispatched") is not True:
        raise Error(invalid)
    return result


def native_operation_metadata(data, operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise Error("Invalid native operation ID")
    data = android_metadata(data, "maintenance", {"operation": "health"})
    history = data.get("operations")
    if "operations" in data and not isinstance(history, list):
        raise Error("Invalid native operation history")
    rows = [row for row in (history or []) if isinstance(row, dict) and row.get("request_id") == operation_id]
    if len(rows) > 1:
        raise Error("Ambiguous native operation history")
    receipt = rows[0] if rows else {"request_id": operation_id, "state": "unknown", "evidence": "none"}
    if rows:
        # The generic legacy projector permits omitted/null optional fields.
        # A matching durable receipt requires a complete executor and operation.
        required = ("action", "operation", "runtime", "updated_at", "state", "evidence")
        if (any(receipt.get(key) is None for key in required)
                or type(receipt["updated_at"]) is not int or receipt["updated_at"] <= 0
                or (receipt["state"] == "handler_completed") != (receipt["evidence"] == "handler_completed")):
            raise Error("Invalid native operation receipt")
    return {"version": 1, "runtime": data["runtime"], "operation_id": operation_id,
            "state": receipt["state"], "receipt": receipt,
            "history_available": isinstance(history, list), "effect_observed": False}


def android_command(client, config_path, device, name, words, json_output):
    """The native token has its own queue; never race the WebView consumer."""
    bindings = client.config.get("native_devices", {})
    if not isinstance(bindings, dict):
        raise Error("native_devices must map player IDs to native device IDs")
    if words[:1] == ["bind"]:
        if len(words) != 2:
            raise Error("Use android bind NATIVE_ALIAS")
        native = client.device(words[1])
        if (native == device or native in bindings or device in bindings.values() or any(
                parent != device and target == native for parent, target in bindings.items()) or not any(
                row.get("id") == native for row in client.credentials.get("devices", []))):
            raise Error("Use a separate native device queue, bound to only one player")
        client.config.setdefault("native_devices", {})[device] = native
        write_private(config_path, client.config)
        return {"bound": True, "player": device, "native_device": native}
    native = bindings.get(device)
    if not isinstance(native, str) or native == device or not any(
            row.get("id") == native for row in client.credentials.get("devices", [])):
        raise Error("No native agent is bound. Provision it once, then use android bind NATIVE_ALIAS")
    op = words[0].casefold() if words else "status"
    op = {"stop": "standby", "go": "wake", "start": "wake"}.get(op, op)
    tail = words[1:]
    if op == "screenshot":
        if tail and (len(tail) != 2 or tail[0] not in ("-o", "--output")):
            raise Error("Use android screenshot [-o FILE.png]")
        return screenshot_command(client, native, name, tail[1] if tail else None, client.timeout)
    operation_id = None
    if op == "operation" and len(tail) == 1 and re.fullmatch(r"[a-f0-9]{32}", tail[0]):
        operation_id = tail[0]
        action, params = "maintenance", {"operation": "health"}
    elif op in ("status", "doctor", "logs", "recover") and not tail:
        action, params = "maintenance", {"operation": {"status": "health", "doctor": "health", "recover": "recover_video"}.get(op, op)}
    elif op in ("restart", "reload", "reboot", "wake", "standby") and not tail:
        action, params = "lifecycle", {"operation": {"restart": "restart_app", "reload": "reload_player", "reboot": "reboot_device"}.get(op, op)}
    elif op in ("pause", "resume") and not tail:
        action, params = "playback", {"operation": op}
    elif op == "seek" and len(tail) == 1:
        try:
            position = float(tail[0])
        except ValueError:
            raise Error("Seek position must be a nonnegative number") from None
        if not math.isfinite(position) or not 0 <= position <= 9007199254740991:
            raise Error("Seek position must be a nonnegative finite number")
        action, params = "playback", {"operation": "seek", "position": position}
    elif op == "queue":
        sub = tail[0] if tail else "status"
        sub = {"prev": "previous"}.get(sub, sub)
        if sub == "play" and len(tail) > 1:
            ids = tail[1:]
            loop = True
            if not 1 <= len(ids) <= 100 or any(not re.fullmatch(r"[1-9][0-9]{0,15}", v) or int(v) > 9007199254740991 for v in ids):
                raise Error("Use queue play with 1–100 positive VPortal IDs")
            ids = [int(v) for v in ids]
            if len(set(ids)) != len(ids):
                raise Error("VPortal queue IDs must be unique")
            action, params = "vportal_queue", {"operation": "play", "ids": ids, "loop": loop}
        elif sub in ("status", "next", "previous", "restart", "stop") and len(tail) <= 1:
            action, params = "vportal_queue", {"operation": sub}
        else:
            raise Error("Use queue play ID..., status, next, prev, restart or stop")
    elif op == "update" and len(tail) == 2:
        try:
            u = urllib.parse.urlsplit(tail[0])
            valid_host = u.hostname and (u.port is None or 0 < u.port < 65536)
        except ValueError:
            raise Error("Invalid HTTPS manifest URL") from None
        if len(tail[0]) > 2048 or any(ord(c) < 33 for c in tail[0]) or not valid_host or u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment or not re.fullmatch(r"[0-9a-f]{64}", tail[1]):
            raise Error("Use update HTTPS_MANIFEST_URL SHA256; the native agent also verifies its signature")
        action, params = "maintenance", {"operation": "update", "manifest": tail[0], "sha256": tail[1]}
    else:
        raise Error("Unknown android command; see ott --help")
    try:
        data = client.call(native, action, params)
    except (PlayerRejected, PlayerUnsupported):
        raise Error("Native operation rejected or unsupported; inspect android status before retrying. Pause requires kiosk off") from None
    result = android_metadata(data, action, params)
    if operation_id is not None:
        return native_operation_metadata(result, operation_id)
    return result


def handoff_command(client, source, target):
    source_id, target_id = client.device(source), client.device(target)
    if source_id == target_id:
        raise Error("Choose two different players for handoff")
    try:
        data = client.call(source_id, "playback_handoff_export", {})
        handoff = data.get("handoff") if isinstance(data, dict) else None
        if not isinstance(handoff, dict):
            raise Error("The source player returned an invalid handoff")
        keys = {"version", "provider", "source_id", "item_id", "kind"}
        archive = handoff.get("kind") == "archive"
        if archive:
            keys.add("archive_start")
        if (set(data) != {"handoff"} or set(handoff) != keys
                or type(handoff.get("version")) is not int or handoff["version"] != 1
                or handoff.get("provider") not in ("m3u", "xtream", "stalker")
                or handoff.get("kind") not in ("live", "archive")
                or not isinstance(handoff.get("item_id"), str)
                or not re.fullmatch(r"channel-v1:[0-9a-f]{8}\.[0-9a-f]{8}", handoff["item_id"])
                or any(not epg_text(handoff.get(k), limit, "utf-8") or not handoff[k].strip()
                       or re.search(r"[\x00-\x1f\x7f]", handoff[k]) for k, limit in (("source_id",256),("item_id",512)))
                or (archive and (type(handoff.get("archive_start")) is not int or not 1 <= handoff["archive_start"] <= 9007199254740991))):
            raise Error("The source player returned an invalid handoff")
        result = client.call(target_id, "playback_handoff_import", {"handoff": handoff})
        if (not isinstance(result, dict) or set(result) != {"dispatched", "kind"}
                or result["dispatched"] is not True or result["kind"] != handoff["kind"]):
            raise Error("Target handoff was not confirmed; inspect playback before retrying")
    except (PlayerRejected, PlayerUnsupported):
        raise Error("Handoff is unavailable or rejected. Both players require the same configured source and a unique matching channel") from None
    return {"source": source_id, "target": target_id, "dispatched": True, "kind": handoff["kind"], "source_stopped": False}


def provider_request(client, device, action, params, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Error("Provider operation exceeded --timeout; no further request was sent")
    client.timeout = remaining
    return client.call(device, action, params)


def provider_identifier(value):
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and re.fullmatch(r"[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)*", value) is not None)


def provider_play_plan(client, device, words, deadline):
    """Keep existing whole-name queries; split only an unambiguous provider prefix."""
    tail = words[1:]
    query = " ".join(tail).strip()
    if not query:
        raise Error("provider requires a provider ID, index or name")
    if len(tail) == 1 and tail[0] != "--":
        return {"query": query}, None, None
    boundary = tail.index("--") if "--" in tail else None
    if boundary is not None and (boundary == 0 or boundary == len(tail) - 1 or tail.count("--") != 1):
        raise Error("Use provider ID_OR_NAME -- CHANNEL, R or NAME")
    data = provider_request(client, device, "providers", {}, deadline)
    rows = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        raise Error("The player returned an invalid provider list; no provider change was requested")
    ids, indices = set(), set()
    for row in rows:
        if (not isinstance(row, dict) or not provider_identifier(row.get("id"))
                or row["id"] in ids or type(row.get("index")) is not int or row["index"] < 0
                or row["index"] in indices or not media_text(row.get("name"), 1024)):
            raise Error("The player returned an invalid provider list; no provider change was requested")
        ids.add(row["id"])
        indices.add(row["index"])

    def resolve(value):
        key = value.strip().casefold()
        exact = [row for row in rows if row["id"].casefold() == key or str(row["index"]) == key]
        exact = exact or [row for row in rows if media_search_key(row["name"]) == media_search_key(key)]
        matches = exact or [row for row in rows if media_search_key(key) in media_search_key(row["name"])]
        if len(matches) > 1:
            raise Error("Several providers match; use an exact provider ID or index. No provider change was requested")
        return matches[0] if matches else None

    if boundary is None:
        if resolve(query) is not None:
            return {"query": query}, None, None
        prefixes = range(len(tail) - 1, 0, -1)
    else:
        prefixes = [boundary]
    for count in prefixes:
        provider = resolve(" ".join(tail[:count]))
        if provider is None:
            continue
        playback = " ".join(tail[count + (boundary is not None):]).strip()
        if not media_text(playback, 1024):
            raise Error("A channel number, R or channel name is required; no provider change was requested")
        return {"query": provider["id"]}, provider["id"], playback
    raise Error("No provider matches; use providers to see IDs and names. No provider change was requested")


def provider_switch_metadata(data, expected=None):
    provider = data.get("provider") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("dispatched") is not True
            or not provider_identifier(provider)
            or (expected is not None and provider != expected)):
        raise Error("The player did not confirm the provider switch. The request may have executed; "
                    "check its status before another change. The request was not repeated.")
    return {"dispatched": True, "provider": provider}


def provider_ready(client, device, expected, deadline, wait, wake_report=None):
    wake_sent = False
    while True:
        data = provider_request(client, device, "status", {}, deadline)
        if (not isinstance(data, dict) or not isinstance(data.get("provider"), str)
                or type(data.get("ready")) is not bool or not isinstance(data.get("player", {}), dict)
                or ("standby" in data.get("player", {}) and type(data["player"]["standby"]) is not bool)):
            raise Error("The player returned invalid provider readiness; no playback was requested")
        standby = data.get("player", {}).get("standby", False)
        if standby and wake_report is not None and not wake_sent:
            wake_sent = True
            params = {"operation": "wake"}
            try:
                wake_report["wake"] = deferred_control_metadata(
                    provider_request(client, device, "lifecycle", params, deadline), "lifecycle", params)
            except (PlayerRejected, PlayerUnsupported):
                raise Error("Wake was rejected or unsupported; no playback was requested. The wake request was not repeated") from None
            except Error as exc:
                raise Error("Wake was not confirmed; no playback was requested. " + str(exc)) from None
        if data["provider"] == expected and data["ready"] and not standby:
            return
        if not wait:
            raise Error("The provider changed or is no longer ready; no playback was requested")
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def random_controls(client, device, deadline):
    try:
        controls = capabilities_metadata(media_call(client, device, "capabilities", {}, deadline))
    except (PlayerUnsupported, PlayerRejected):
        raise Error("Safe random selection is not supported by this player/controller. Update both; no playback was requested") from None
    if "random" not in controls:
        raise Error("Safe random selection is not supported by this player/controller. Update both; no playback was requested")
    return controls


def random_catalog(client, device, controls, deadline, provider=None):
    if "categories" in controls:
        if "list" not in controls["categories"]["operations"]:
            raise Error("The player cannot read its catalog; no random playback was requested")
        while True:
            result = read_categories(client, device, controls, deadline, first_page_only=True)
            if provider is not None and result["provider"] != provider:
                raise Error("The provider changed before random selection; no playback was requested")
            if not result["loading"]:
                # A category-list receipt names the complete All catalog. No
                # channel pages, stream URLs or parental metadata are needed.
                return result["catalog"]
            if provider is None:
                raise Error("The catalog is still loading; no random playback was requested")
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            provider_ready(client, device, provider, deadline, wait=False)
    result = provider_request(client, device, "epg_catalog", {}, deadline)
    return legacy_provider_catalog(result)["catalog"]


def random_play(client, device, catalog, selector, deadline, candidates=None):
    if (not isinstance(catalog, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", catalog)
            or ("search" in selector and not media_text(selector["search"], 1024))):
        raise Error("The random selector or source receipt is invalid; no playback was requested")
    try:
        result = media_call(client, device, "play_catalog", {"catalog": catalog, "random": selector}, deadline)
    except PlayerUnsupported:
        raise Error("Safe random selection is not supported by this player/controller. Update both; the request was not repeated") from None
    except PlayerRejected as exc:
        if isinstance(exc.data, dict) and exc.data.get("error") == "No eligible channels are available, or playback is restricted by the player.":
            raise Error("No eligible channels are available, or playback is restricted by the player. The request was not repeated") from None
        raise Error("Random playback was rejected: no eligible channel or the catalog, provider, PIN or kiosk state changed. The request was not repeated") from None
    except HTTPError as exc:
        if exc.code == 400:
            raise Error("This controller does not accept safe random selection; update the controller and player. The request was not repeated") from None
        raise
    channel = result.get("channel") if isinstance(result, dict) else None
    if (time.monotonic() >= deadline or not isinstance(result, dict) or set(result) != {"dispatched", "channel"}
            or result["dispatched"] is not True or not isinstance(channel, dict) or set(channel) != {"id", "number", "name"}
            or not media_text(channel["id"], 2048) or not media_text(channel["name"], 16384)
            or type(channel["number"]) is not int or not 0 < channel["number"] <= 9007199254740991
            or ("range" in selector and not selector["range"][0] <= channel["number"] <= selector["range"][1])
            or ("search" in selector and media_search_key(selector["search"]) not in media_search_key(channel["name"]))
            or (candidates is not None and not any(channel_identity(channel["id"]) == channel_identity(row["id"])
                and all(channel[key] == row[key] for key in ("number", "name")) for row in candidates))):
        raise Error("The player did not confirm a valid random channel. Playback may have started; the request was not repeated")
    return {"dispatched": True, "channel": dict(channel)}


def random_command(client, device, params, timeout):
    previous_timeout = client.timeout
    deadline = time.monotonic() + timeout
    try:
        controls = random_controls(client, device, deadline)
        catalog = random_catalog(client, device, controls, deadline)
        selector = {}
        if "random_range" in params:
            selector["range"] = params["random_range"]
        if "random_mode" in params:
            selector["mode"] = params["random_mode"]
        return random_play(client, device, catalog, selector, deadline)
    finally:
        client.timeout = previous_timeout


def provider_play_catalog(client, device, provider, deadline):
    controls = media_capabilities(client, device, deadline)
    if controls is not None:
        while True:
            categories = read_categories(client, device, controls, deadline, first_page_only=True)
            if categories["provider"] != provider:
                raise Error("The provider changed before its catalog was read; no playback was requested")
            if not categories["loading"]:
                data = read_media_channels(client, device, "", deadline, require_modern=True,
                                           expected_provider=provider, controls=controls, category_override="all",
                                           source_categories=categories, complete_only=True)
                if not data["loading"]:
                    return data
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            provider_ready(client, device, provider, deadline, wait=False)
    data = provider_request(client, device, "epg_catalog", {}, deadline)
    return legacy_provider_catalog(data)


def legacy_provider_catalog(data):
    invalid = "The player returned an invalid complete channel catalog; no playback was requested"
    if (not isinstance(data, dict) or not media_text(data.get("catalog"), 128)
            or not isinstance(data.get("channels"), list) or len(data["channels"]) > 10000):
        raise Error(invalid)
    rows, identities, previous = [], set(), 0
    for row in data["channels"]:
        if (not isinstance(row, dict) or not epg_text(row.get("id"), 512) or not row["id"].strip()
                or row["id"] in identities or not media_text(row.get("name"), 16384)
                or type(row.get("number")) is not int or not previous < row["number"] <= 9007199254740991):
            raise Error(invalid)
        rows.append({key: row[key] for key in ("id", "number", "name")})
        identities.add(row["id"])
        previous = row["number"]
    return {"catalog": data["catalog"], "channels": rows}


def provider_play_selection(rows, query):
    if re.fullmatch(r"[0-9]+", query):
        matches = [row for row in rows if str(row["number"]) == query.lstrip("0")]
    else:
        key = media_search_key(query)
        matches = [row for row in rows if media_search_key(row["name"]) == key]
        if not matches:
            matches = [row for row in rows if key in media_search_key(row["name"])]
    if not matches:
        raise Error("No channels match in the selected provider's catalog; no playback was requested")
    if len(matches) == 1 and (query.isdecimal() or media_search_key(matches[0]["name"]) == media_search_key(query)):
        return matches[0]
    return None


def provider_command(client, device, words, project=False):
    previous_timeout = client.timeout
    deadline = time.monotonic() + previous_timeout
    try:
        params, provider, playback = provider_play_plan(client, device, words, deadline)
        random_word = playback is not None and playback.casefold() in ("r", "random", "rr")
        if random_word:
            random_controls(client, device, deadline)
        data = provider_request(client, device, "provider", params, deadline)
        if playback is None:
            return provider_switch_metadata(data) if project else data, None
        data = provider_switch_metadata(data, provider)
        stage, dispatched = "waiting", False
        try:
            provider_ready(client, device, provider, deadline, wait=True, wake_report=data)
            stage = "catalog"
            if random_word:
                # Category support belongs to the destination provider, not the
                # provider whose capabilities were checked before switching.
                controls = random_controls(client, device, deadline)
                catalog = {"catalog": random_catalog(client, device, controls, deadline, provider)}
                selected = None
            else:
                catalog = provider_play_catalog(client, device, provider, deadline)
                stage = "selection"
                selected = provider_play_selection(catalog["channels"], playback)
                if selected is None:
                    random_controls(client, device, deadline)
            provider_ready(client, device, provider, deadline, wait=False)
            if time.monotonic() >= deadline:
                raise Error("Provider operation exceeded --timeout; no playback was requested")
            stage, dispatched = "playback", None
            if selected is None:
                selector = ({} if playback.casefold() in ("r", "random") else
                            {"mode": "adult"} if playback.casefold() == "rr" else {"search": playback})
                result = random_play(client, device, catalog["catalog"], selector, deadline, catalog.get("channels"))
                selected = result["channel"]
            else:
                result = provider_request(client, device, "play_catalog",
                                          {"catalog": catalog["catalog"], "id": selected["id"]}, deadline)
            channel = result.get("channel") if isinstance(result, dict) else None
            if (not isinstance(result, dict) or result.get("dispatched") is not True
                    or not isinstance(channel, dict) or type(channel.get("number")) is not int
                    or any(channel.get(key) != value for key, value in selected.items())):
                raise Error("The player did not confirm the requested channel; playback may have started. The request was not repeated")
            data["playback"] = {"stage": "completed", "dispatched": True, "channel": selected}
            return data, None
        except (PlayerRejected, PlayerUnsupported) as exc:
            dispatched = False
            reason = "unsupported by this player" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
            error = Error(f"The provider playback step was {reason}; check its catalog and local restrictions. The request was not repeated")
        except Error as exc:
            error = exc
        except (KeyError, TypeError, ValueError, OSError):
            error = Error("The provider playback step did not complete; inspect this player before another change")
        data["playback"] = {"stage": stage, "dispatched": dispatched, "error": clean(str(error))}
        return data, error
    except (PlayerRejected, PlayerUnsupported) as exc:
        reason = "unsupported by this player" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
        raise Error(f"The provider request was {reason}; check its allowed providers and local restrictions. "
                    "The request was not repeated.") from None
    finally:
        client.timeout = previous_timeout


def provider_group_targets(client, selector):
    """Resolve a comma list locally before any provider request can be sent."""
    if "," not in selector:
        return None
    aliases = client.config.get("players", {})
    devices = client.credentials.get("devices", [])
    if (not isinstance(aliases, dict) or any(not isinstance(name, str) for name in aliases)
            or not isinstance(devices, list) or any(not isinstance(row, dict)
                or not isinstance(row.get("id"), str) for row in devices)):
        raise Error("Check the configured player names and device UUIDs")
    # Hand-edited configurations can contain commas. Preserve their exact
    # single-player meaning, including ambiguous names rejected by device().
    if (any(name.casefold() == selector.casefold() for name in aliases)
            or any(row["id"].casefold() == selector.casefold() for row in devices)):
        return None
    names = [name.strip() for name in selector.split(",")]
    if any(not name for name in names):
        raise Error("Player lists require a name between every comma; use t1,t2,t3,t4")
    targets = []
    seen = set()
    for name in names:
        device = client.device(name)
        if not isinstance(device, str) or not device:
            raise Error("Check the configured player UUIDs")
        # Device IDs are case-sensitive on the server; device() resolves aliases
        # and case-insensitive UUID lookups to their configured identity.
        if device not in seen:
            seen.add(device)
            targets.append((name, device))
    return targets


def provider_group_command(client, targets, words, json_output):
    if command_verb(words) != "provider":
        raise Error("Player lists support provider/prov or notifications; use ott t1,t2,t3,t4 provider m3u or notifications off")
    action, params = parse_command(words)
    if not params["query"].strip():
        raise Error("provider requires a provider ID, index or name")
    results = []
    on_receipt = getattr(client, "on_receipt", None)
    for name, device in targets:
        row = {"player": name, "ok": False}
        try:
            if callable(on_receipt):
                client.on_receipt = lambda receipt, name=name: on_receipt({**receipt, "player": name})
            data, playback_error = provider_command(client, device, words, project=True)
            row["data"] = provider_switch_metadata(data)
            if "playback" in data:
                row["data"]["playback"] = data["playback"]
            if "wake" in data:
                row["data"]["wake"] = data["wake"]
            if playback_error:
                raise playback_error
            row["ok"] = True
        except (PlayerRejected, PlayerUnsupported) as exc:
            reason = "unsupported by this player" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
            row["error"] = (f"The provider request was {reason}; check its allowed providers and local restrictions. "
                            "The request was not repeated.")
        except Error as exc:
            row["error"] = clean(str(exc))
        except (KeyError, TypeError, ValueError, OSError):
            row["error"] = "The provider request did not complete; inspect this player before another change."
        finally:
            client.on_receipt = on_receipt
        results.append(row)
        if not json_output:
            message = ("Provider switch requested: " + row["data"]["provider"] if row["ok"]
                       else "Error: " + row["error"])
            if "playback" in row.get("data", {}):
                message = "Provider switch acknowledged: " + row["data"]["provider"] + "; "
                message += ("channel requested: " + clean(row["data"]["playback"]["channel"]["name"]) if row["ok"]
                            else "Error: " + row["error"])
            print(f"{clean(name)}: {message}", flush=True)
    succeeded = sum(row["ok"] for row in results)
    if json_output:
        print(json.dumps({"action": action, "results": results, "succeeded": succeeded,
                          "failed": len(results) - succeeded}, ensure_ascii=False, indent=2))
    return 0 if succeeded == len(results) else 1


def notifications_group_command(client, targets, words, json_output):
    _, params = parse_command(words)
    results = []
    on_receipt = getattr(client, "on_receipt", None)
    for name, device in targets:
        row = {"player": name, "ok": False}
        try:
            if callable(on_receipt):
                client.on_receipt = lambda receipt, name=name: on_receipt({**receipt, "player": name})
            row["data"] = notifications_command(client, device, params, client.timeout)
            row["ok"] = True
        except Error as exc:
            row["error"] = clean(str(exc))
        finally:
            client.on_receipt = on_receipt
        results.append(row)
        if not json_output:
            print(clean(name) + ": ", end="")
            if row["ok"]:
                print_notifications(row["data"])
            else:
                print("Error: " + row["error"], flush=True)
    succeeded = sum(row["ok"] for row in results)
    if json_output:
        print(json.dumps({"action": "notifications", "results": results, "succeeded": succeeded,
                          "failed": len(results) - succeeded}, ensure_ascii=False, indent=2))
    return 0 if succeeded == len(results) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "resolve":
        return resolve_main(argv[1:])
    if argv and argv[0] == "diagnostics":
        # Keep scoped diagnostic credentials separate from the legacy admin config.
        module_path = Path(__file__).resolve().with_name("diagnostics.py")
        if not module_path.is_file():
            print("Error: Install diagnostics.py beside ott.py", file=sys.stderr)
            return 1
        spec = importlib.util.spec_from_file_location("ott_diagnostics", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(argv[1:])
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-c", "--config", default=os.environ.get("OTT_CONFIG", str(Path.home() / ".config/ottplay-control/cli.json")))
    parser.add_argument("-t", "--timeout", type=float, default=45)
    parser.add_argument("-j", "--json", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached EPG history and archive checks")
    parser.add_argument("--receipt", action="store_true", help="Write safe player RPC receipts to stderr")
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.help or not args.words or args.words == ["help"]:
        print(HELP)
        return 0
    try:
        if args.words[0].casefold() == "registry" and args.words[1:] in (["--help"], ["-h"]):
            registry_management(None, args.config, args.words, args.json)
            return 0
        if args.words[0].casefold() == "report":
            # Saved evidence must remain inspectable without credentials or a
            # reachable controller. Do not construct a Client on this path.
            modules = []
            for name in ("workbench", "report_verify"):
                path = Path(__file__).resolve().with_name(name + ".py")
                if not path.is_file():
                    raise Error("Install all CLI Python files from the same revision to verify reports")
                spec = importlib.util.spec_from_file_location("ott_" + name, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                modules.append(module)
            return modules[1].main(args.words[1:], SimpleNamespace(**globals()), modules[0], args.json)
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300:
            raise Error("--timeout must be between 1 and 300 seconds")
        config = read_json(args.config)
        if args.words[0].casefold() == "unalias":
            registry_management(None, args.config, args.words, args.json)
            return 0
        if args.words[0].casefold() == "presets":
            if len(args.words) != 1:
                raise Error("Use presets without arguments")
            names = preset_names(config)
            print(json.dumps({"presets": names}, indent=2) if args.json else "\n".join(names))
            return 0
        if (len(args.words) > 1 and args.words[1].casefold() == "load"
                and args.words[0].casefold() not in TOP_LEVEL_COMMANDS):
            return preset_command(config, args.words, args.timeout, args.json,
                                  print_request_receipt if args.receipt else None)
        client = Client(config, args.timeout)
        client.on_receipt = print_request_receipt if args.receipt else None
        if args.words[0].casefold() == "handoff":
            if len(args.words) != 3:
                raise Error("Use ott handoff SOURCE TARGET")
            print(json.dumps(handoff_command(client, args.words[1], args.words[2]), ensure_ascii=False, indent=2))
            return 0
        if management(client, args.config, args.words, json_output=args.json):
            return 0
        if args.words[0].casefold() in TOP_LEVEL_COMMANDS:
            raise Error(f"The name {args.words[0]!r} is reserved for a CLI command. Run ott --help for its syntax; use another player alias or the device UUID")
        targets = provider_group_targets(client, args.words[0])
        if targets is not None:
            if command_verb(args.words[1:]) == "notifications":
                return notifications_group_command(client, targets, args.words[1:], args.json)
            return provider_group_command(client, targets, args.words[1:], args.json)
        device = client.device(args.words[0])
        words = args.words[1:]
        if words and words[0].casefold() in ("setting", "settings"):
            module_path = Path(__file__).resolve().with_name("player_settings.py")
            if not module_path.is_file():
                raise Error("Install all CLI Python files from the same revision to manage settings")
            spec = importlib.util.spec_from_file_location("ott_player_settings", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.run(SimpleNamespace(**globals()), client, device, words, args.timeout, args.json)
        if (words and words[0].casefold() == "update" and
                (len(words) == 1 or words[1].casefold() not in ("status", "prepare", "install"))):
            module_path = Path(__file__).resolve().with_name("app_update.py")
            if not module_path.is_file():
                raise Error("Install app_update.py beside ott.py to use release updates")
            spec = importlib.util.spec_from_file_location("ott_app_update", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.run(SimpleNamespace(**globals()), client, device, words, args.json)
        if words and words[0].casefold() in ("doctor", "inspect", "operation", "bundle", "test", "debug", "dbg"):
            module_path = Path(__file__).resolve().with_name("workbench.py")
            if not module_path.is_file():
                raise Error("Install workbench.py beside ott.py to use the workbench")
            spec = importlib.util.spec_from_file_location("ott_workbench", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.run(SimpleNamespace(**globals()), client, device, words, args.timeout, args.json)
        if words and words[0].casefold() == "android":
            data = android_command(client, args.config, device, args.words[0], words[1:], args.json)
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        action, params = parse_command(words)
        status_overview = action == "status" and command_verb(words) != "v"
        status_deadline = time.monotonic() + args.timeout if status_overview else None
        catalog_play = None
        playback_error = None
        plex_settings = action == "provider_settings" and params.get("provider") == "plex"
        if action == "provider" and (len(words) > 2 or "--" in words[1:]):
            data, playback_error = provider_command(client, device, words)
        elif action in ("profile_save", "profile_export", "profile_import"):
            data = offline_profile_command(client, device, action, params, args.timeout)
        elif action == "profile_settings" and "vportal" in params["settings"]:
            data = vportal_profile_command(client, device, params, args.timeout)
        elif action == "provider_settings" and params.get("provider") == "vportal":
            row = {"number": params["settings"]["profile"],
                   "settings": {key: value for key, value in params["settings"].items() if key != "profile"}}
            data = vportal_settings_command(client, device, row)
        elif action == "screenshot":
            data = screenshot_command(client, device, args.words[0], params.get("output"), args.timeout)
        elif action == "plex_queue":
            data = plex_queue_command(client, device, params, args.timeout)
        elif action == "plex_library":
            data = plex_library_command(client, device, params, args.timeout)
        elif action == "categories":
            data = categories_command(client, device, params, args.timeout)
        elif action == "channels":
            data = channels_command(client, device, params, args.timeout)
        elif action == "favorites":
            data = favorites_command(client, device, params, args.timeout)
        elif action == "aspect":
            data = aspect_command(client, device, params, args.timeout)
        elif action == "notifications":
            data = notifications_command(client, device, params, args.timeout)
        elif action == "command" and params.get("command") == "random_channel":
            data = random_command(client, device, params, args.timeout)
        elif action == "programs" and "epg" in config:
            data, catalog_play = server_programs(client, device, config["epg"], params["search"], refresh=args.refresh)
        elif action == "play":
            data, playback_error = play_channel(client, device, params, args.timeout)
            if data.get("channels") == [] and "epg" in config and data.get("version") != 1:
                print("No channel matches. Searching current EPG and available archives...", file=sys.stderr)
                query = params["query"]
                data, catalog_play = server_programs(client, device, config["epg"], query, refresh=args.refresh)
                action, params, words = "programs", {"search": query}, ["p", query]
                playback_error = None
        else:
            try:
                data = client.call(device, action, params)
            except (PlayerRejected, PlayerUnsupported) as exc:
                if action == "restart" and params.get("target") == "stream":
                    if isinstance(exc.data, dict) and (exc.data.get("reason") == "no_restartable_stream" or exc.data.get("error") == "There is no owned, restartable stream."):
                        raise Error("No stream is ready to restart. Wait for playback to load, or use 'ott <player> restart' to reload the player. The request was not repeated.") from None
                    if isinstance(exc, PlayerUnsupported):
                        raise Error("Stream restart is unavailable in the current playback state or player version. Wait for loading to finish, check capabilities, or use 'ott <player> restart' to reload the player.") from None
                if action == "playback" and params.get("operation") in CHANNEL_STEPS.values() and isinstance(exc, PlayerRejected):
                    detail = exc.data.get("error") if isinstance(exc.data, dict) else None
                    if isinstance(detail, str) and detail in PLEX_QUEUE_ERRORS:
                        raise Error(detail + " The request was not repeated") from None
                if action in ("capabilities", "lifecycle", "input", "playback"):
                    reason = "unsupported by this player" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
                    raise Error(f"The {action} request was {reason}; use an updated player/controller and check capabilities and local restrictions") from None
                if action not in ("profiles", "profile", "profile_settings", "restart") and not plex_settings:
                    raise
                # Settings can contain credentials: do not echo player-supplied errors.
                reason = "unsupported by this player" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
                if plex_settings:
                    raise Error(f"The Plex settings request was {reason}; select Plex, unlock its settings and use a player with remote Plex support") from None
                raise Error(f"The {action} request was {reason}; check its settings on the player") from None
        if action in ("vportal", "vportal_random", "vportal_search"):
            data = vportal_metadata(data, action != "vportal_search", action == "vportal_random")
        elif action in ("profiles", "profile", "profile_settings"):
            data = profile_metadata(data, action, params)
        elif action == "app_update":
            data = app_update_metadata(data, params)
        elif action == "restart":
            data = restart_metadata(data, params["target"])
        elif action == "kiosk":
            data = kiosk_metadata(data, "set" if params.get("query") else params["mode"], params.get("strict", False))
        elif plex_settings:
            data = plex_settings_metadata(data, params["settings"])
        elif action == "status" and command_verb(words) == "v":
            data = volume_metadata(data)
        elif status_overview:
            data = status_capabilities(client, device, data, status_deadline)
        elif action == "command" and params.get("command") == "set_volume":
            data = volume_metadata(data, mutating=True)
        elif action == "capabilities":
            data = capabilities_metadata(data)
        elif action in ("lifecycle", "input"):
            data = deferred_control_metadata(data, action, params)
        elif action == "playback":
            data = playback_metadata(data, params)
        if action == "programs" and params["search"] and not listing_option(words):
            selected = select_programme(data["programs"])
            if selected:
                try:
                    number = selected["number"]
                    archive = selected.get("mode") == "archive"
                    key = (number, selected["start"]) if archive else number
                    target = catalog_play[key] if catalog_play is not None else None
                    # Use the returned catalogue number, even for duplicate or numeric names.
                    playback = (client.call(device, "play_archive_catalog" if archive else "play_catalog", target) if target is not None
                                else client.call(device, "play", {"query": str(number)}))
                    channel = playback.get("channel") if isinstance(playback, dict) else None
                    if (not isinstance(channel, dict) or playback.get("dispatched") is not True
                            or type(channel.get("number")) is not int or channel["number"] != number
                            or channel.get("name") != selected["channel"]
                            or (target is not None and channel.get("id") != target["id"])
                            or (archive and (playback.get("start") != selected["start"] or playback.get("end") != selected["end"]))):
                        raise Error("The player did not confirm the requested channel switch. The request may have executed; do not repeat the change blindly.")
                    if catalog_play is not None:
                        playback = {"dispatched": True, "channel": {key: channel[key] for key in ("id", "number", "name")}}
                        if archive:
                            playback.update({"mode": "archive", "start": selected["start"], "end": selected["end"]})
                    data["playback"] = playback
                except Error as exc:
                    playback_error = exc
                    data["playback"] = {"error": str(exc)}
            elif not data.get("partial"):
                print("No current programmes or available archives match the search." if "epg" in config else
                      "No current programmes match the search.", file=sys.stderr)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif action == "app_update":
            if params["operation"] == "install":
                print("Installer request accepted. Android may require confirmation on the device; use update status to check completion.")
            else:
                print("Update: " + data["phase"] + "; installed " + data["installed_version"] + "; target " + (data["target_version"] or "none"))
                if data["error"]:
                    print("Reason: " + data["error"])
                if not data["can_request_installs"]:
                    print("Android requires permission to install updates from this app.")
        elif action == "screenshot":
            print(f"Screenshot saved: {clean(data['path'])} ({data['width']}×{data['height']}; {data['source']}; video: {data['video']})")
        elif action == "plex_queue":
            print_plex_queue(data, preview=params["op"] == "preview")
        elif action == "plex_library":
            print_plex_library(data)
        elif action == "categories":
            print_categories(data)
        elif action == "favorites":
            print_favorites(data)
        elif action == "aspect":
            print_aspect(data, args.words[0])
        elif action == "notifications":
            print_notifications(data)
        elif action == "channels" or (action == "play" and "channels" in data):
            for row in data["channels"]:
                print(f"{row['number']}: {clean(row['name'])}")
            if action == "channels" and "limit" in params:
                print(f"Showing {len(data['channels'])} of {data['total']} matching videos "
                      f"(offset {data['offset']}); more: {'yes' if data['has_more'] else 'no'}.")
                if data["has_more"]:
                    command = ["ott", *argv[:len(argv) - len(args.words)], args.words[0], "s",
                               "--limit", str(params["limit"]), "--offset", str(data["offset"] + len(data["channels"]))]
                    if params["search"]:
                        command.extend(["--", params["search"]])
                    print("Next page (current catalog): " + clean(shlex.join(command)))
        elif action == "programs":
            for row in data["programs"]:
                detail = ""
                if row.get("mode") == "archive":
                    detail = " [archive from %s; %s consecutive programmes]" % (
                        time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(row["start"])), row["consecutive"])
                print(f"{clean(row['channel'])} — {clean(row['title'])}{detail}")
        elif action in ("vportal", "vportal_random", "vportal_search"):
            for row in data["items"]:
                print(f"{row['number']}: {clean(row['title'])}")
        elif action == "status" and command_verb(words) == "v":
            print(f"{data['volume']:g}%")
        elif status_overview:
            print_player_status(data, args.words[0])
        elif action == "providers":
            for row in data["providers"]:
                print(f"{'*' if row['active'] else ' '} {row['index']}: {clean(row['id'])} — {clean(row['name'])}")
        elif action == "profile_export":
            print("Local videos profile exported: " + clean(data["path"]) + ". Videos and access grants are not included.")
        elif action in ("profile_save", "profile_import"):
            print(f"Local videos profile saved: {data['profile']['number']}: {clean(data['profile']['name'])}. Playback was not started.")
            if data["binding_required"]:
                print(f"Local binding required: first load profile {data['profile']['number']}. In Settings → Provider → Local videos, "
                      "connect the original sources, wait for scanning to finish, and choose Bind loaded profile to connected sources. "
                      "Then load the same profile again.")
        elif action == "profiles":
            for row in data["profiles"]:
                if data["provider"] == "offline":
                    state = "binding required" if row["binding_required"] else ("configured" if row["configured"] else "empty")
                    print(f"{'*' if row['active'] else ' '} {row['number']}: {clean(row['name'])} | sources: {row['source_count']} | {state} | selected: {clean(row['selected_name']) or 'none'}")
                elif data["provider"] == "vportal":
                    print(f"{'*' if row['active'] else ' '} {row['number']}: {clean(row['name'])} | VPortal: {'set' if row['vportal_configured'] else 'empty'}")
                else:
                    history = "unknown" if row["history_hours"] is None else str(row["history_hours"])
                    print(f"{'*' if row['active'] else ' '} {row['number']}: {clean(row['name'])} | history: {history} h | "
                          f"playlist: {'set' if row['playlist_configured'] else 'empty'}")
        elif action == "profile":
            print(f"Profile switch requested: {data['profile']['number']}: {clean(data['profile']['name'])}")
            if data.get("binding_required"):
                print("Local source binding or permission is required on the player. Playback has not been confirmed.")
        elif action == "profile_settings":
            print(f"Profile settings saved: {data['profile']['number']}: {clean(data['profile']['name'])}")
        elif action == "restart":
            print("Stream restart requested." if data["target"] == "stream" else
                  "Player reload accepted; waiting for the acknowledgement to reach the player.")
        elif action == "lifecycle":
            if data["operation"] in ("standby", "wake"):
                label = "Standby" if data["operation"] == "standby" else "Wake"
                print(f"{label} request accepted ({data['operation']}); waiting for the acknowledgement to reach the player.")
            else:
                print(f"Lifecycle request accepted: {data['operation']}; waiting for the acknowledgement to reach the player.")
        elif action == "input":
            print(f"Input request accepted: {data['key']}; waiting for the acknowledgement to reach the player.")
        elif action == "playback":
            if "plex_queue" in data:
                print_plex_queue(data["plex_queue"])
            elif "channel" in data:
                print(f"Channel switch requested: {data['channel']['number']}: {clean(data['channel']['name'])}")
            else:
                print(f"Playback request dispatched: {data['operation']}; this does not confirm decoder recovery.")
        elif action == "kiosk":
            if data.get("strict"):
                if data["state"] == "waiting":
                    print("Strict kiosk is armed; local controls lock after the first channel selection.")
                else:
                    print("Strict kiosk: local controls locked; supported players allow timeline seeking within the current media episode.")
            if data["state"] == "off":
                print("Kiosk mode disabled.")
            elif data["state"] == "waiting":
                print("Kiosk mode enabled; waiting for the first channel selection in the player.")
            elif "media" in data:
                provider_name = "Plex" if data["provider"] == "plex" else "VPortal"
                print(f"Kiosk {provider_name}: {clean(data['media']['title'])} | {data['media']['index'] + 1}/{data['media']['total']} | {data['health']} | retries: {data['retries']}")
            else:
                print(f"Kiosk channel: {clean(data['channel']['name'])} | {data['health']} | retries: {data['retries']} (10 s)")
            if data.get("screen_suspended"):
                print("Kiosk playback is suspended while the screen is off or the app is in the background.")
        elif plex_settings:
            print("Plex settings saved: " + ", ".join(data["fields"]) + ".")
        elif action == "play":
            print(f"Channel switch requested: {data['channel']['number']}: {clean(data['channel']['name'])}")
        elif action == "command" and params.get("command") == "set_volume":
            print(f"{data['volume']:g}%")
        elif action == "command" and params.get("command") == "random_channel":
            print(f"Random channel requested: {data['channel']['number']}: {clean(data['channel']['name'])}")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        if action in ("programs", "play") and data.get("playback", {}).get("dispatched"):
            channel = data["playback"]["channel"]
            sys.stdout.flush()
            exact = (sum(media_search_key(row["name"]) == media_search_key(params["query"].strip())
                         for row in data.get("channels", [])) if action == "play" else 0)
            if len(data.get("programs", data.get("channels", []))) > 1 and exact != 1:
                print(f"Randomly selected channel: {channel['number']}: {clean(channel['name'])}", file=sys.stderr)
            print(f"Channel switch requested: {channel['number']}: {clean(channel['name'])}", file=sys.stderr)
        if action in ("vportal", "vportal_random"):
            shuffled = "shuffled; " if action == "vportal_random" else ""
            print(f"VPortal playback requested: {data['total']} videos; {shuffled}repeat enabled.", file=sys.stderr)
        elif action == "vportal_search" and not data["items"]:
            print("No VPortal videos match the search.", file=sys.stderr)
        if action == "programs" and data.get("partial"):
            print(f"Programme search is incomplete: checked {data['checked']} of {data['total']} channels on the player. "
                  "Unchecked channels may contain matches. This is search coverage, not EPG download progress.", file=sys.stderr)
        if action in ("categories", "channels", "play") and data.get("loading"):
            print("The media catalog is still loading; this listing is incomplete. Run the listing again when loading finishes.", file=sys.stderr)
        if playback_error:
            raise playback_error
        return 3 if (action == "programs" and data.get("partial")) or (action in ("categories", "channels") and data.get("loading")) else 0
    except (Error, KeyError, TypeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print("Error: " + (str(exc) if isinstance(exc, Error) else "Check the configuration; the operation did not complete"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
