#!/usr/bin/env python3
"""OTT-play remote CLI. Python 3 standard library; secrets stay in private files."""
import argparse
import base64
import http.client
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HELP = """ott [--config FILE] [--json] PLAYER [COMMAND ...]
  ott devices                        list devices and their last connection
  ott alias NAME UUID                name an existing device
  ott add NAME UUID                  create a device access code and queue
  ott pair NAME                      show the player connection settings
  ott discover                       show controllers advertised in network DNS
  ott pending                        list pending player pairing requests
  ott approve NAME CODE              approve the code displayed by that player
  ott NAME                           show player status
  ott NAME 12                        play channel 12 from the s listing
  ott NAME CHANNEL                   find and play a channel (case-insensitive)
  ott NAME play s                    play a channel whose name is reserved
  ott NAME s [TEXT]                  list channels, optionally matching TEXT
  ott NAME p                         list channel — current programme
  ott NAME p TEXT                    find programmes and play the first match
  ott NAME p --list [TEXT]           list programmes without switching channels
  ott NAME v                         show volume
  ott NAME v 35                      set volume to 35%
  ott NAME v +5 / v -5               increase / decrease volume
  ott NAME providers                 list providers (zero-based indices)
  ott NAME provider m3u              select a provider by ID, index or name
  ott NAME provider-config FILE      update active provider settings from JSON
  ott NAME playlist URL              update the M3U playlist
  ott NAME random [FROM TO]          play a random channel
  ott NAME msg TEXT                  show an on-screen message
  ott NAME exit                      close the player / enter standby

Configuration: ~/.config/ottplay-control/cli.json or OTT_CONFIG.
Player, channel, programme and provider searches are case-insensitive.
"""


class Error(Exception):
    pass


class TransportError(Error):
    pass


class HTTPError(Error):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Server returned HTTP {code}. " + {
            401: "Check admin_token in the private configuration.",
            403: "Insufficient permissions or a disallowed Origin.",
            404: "Check the address and update the server to support CLI requests.",
            429: "The queue or request limit has been reached; try again later.",
        }.get(code, "Check the request parameters."))


def read_json(path):
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            return json.load(stream)
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


class Client:
    def __init__(self, config, timeout=45):
        self.config = config
        self.timeout = timeout
        self.server = config["server"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.server)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Error("server must be an HTTP(S) address without credentials, query or fragment")
        self.credentials = read_json(config["server_config"])
        self.token = self.credentials["admin_token"]
        # Never forward an administrator credential through an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

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
                        return response.status, json.loads(body)
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise Error("The server response exceeds the size limit")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise HTTPError(code) from None
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError("Server unavailable; check the address and connection") from exc
        except ValueError as exc:
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

    def call(self, device, action, params):
        query = "?" + urllib.parse.urlencode({"device_id": device})
        deadline = time.monotonic() + self.timeout
        try:
            _, queued = self.api("/api/requests" + query, {"action": action, "params": params}, timeout=self.timeout)
        except Error as exc:
            # A lost POST response cannot prove whether the mutation was queued.
            # Replaying it would give a relative-volume command a second ID.
            if isinstance(exc, HTTPError) and exc.code < 500:
                raise
            raise Error(str(exc) + ". The request may have been accepted; do not repeat the change blindly.") from exc
        if not isinstance(queued, dict) or not isinstance(queued.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", queued["id"]):
            raise Error("The server did not return a request ID. The request may have been accepted; do not repeat the change blindly.")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.8, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                status, result = self.api("/api/requests" + query + "&id=" + queued["id"], timeout=remaining)
            except TransportError:
                continue
            except HTTPError as exc:
                if exc.code in (502, 503, 504):
                    continue
                if exc.code == 404:
                    raise Error("The request receipt has expired or is missing. The request may have been executed; do not repeat the change blindly.") from exc
                raise
            except Error as exc:
                raise Error(str(exc) + ". The request may have been executed; do not repeat the change blindly.") from exc
            if time.monotonic() >= deadline:
                break
            if status == 202:
                if not isinstance(result, dict) or result.get("status") != "pending":
                    raise Error("The server returned an invalid pending status. The request may have been executed; do not repeat the change blindly.")
                continue
            if status != 200 or not isinstance(result, dict) or result.get("status") not in ("ok", "rejected", "unsupported") or not isinstance(result.get("data"), dict):
                raise Error("The server returned an invalid player response. The request may have been executed; do not repeat the change blindly.")
            if result.get("status") != "ok":
                data = result["data"]
                matches = data.get("matches", [])
                if not isinstance(matches, list) or not all(isinstance(row, dict) for row in matches):
                    raise Error("The player rejected the request but returned an invalid match list")
                details = "\n".join(f"{row.get('number', row.get('index', ''))}: {clean(row.get('name', ''))}" for row in matches)
                raise Error(clean(data.get("error", "The player rejected the request")) + ("\n" + details if details else ""))
            return result["data"]
        raise Error("The player did not respond. Open it, check the address/access code and use a version that supports the CLI. The request may still execute before its TTL expires; do not repeat the change blindly.")


def clean(value):
    # Provider-controlled names must not send terminal control/escape sequences.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))


def parse_command(words):
    if not words:
        return "status", {}
    verb, tail = words[0].casefold(), words[1:]
    text = " ".join(tail)
    if verb in ("s", "p"):
        if verb == "p":
            text = " ".join(tail[1:] if tail[:1] == ["--list"] else tail).strip()
        return ("channels" if verb == "s" else "programs"), {"search": text}
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
    if verb in ("status", "providers") and not tail:
        return verb, {}
    if verb == "provider" and tail:
        return "provider", {"query": text}
    if verb == "provider-config":
        if len(tail) != 1:
            raise Error("Use provider-config FILE.json")
        data = read_json(tail[0])
        if not isinstance(data, dict) or set(data) != {"provider", "settings"} or not isinstance(data["settings"], dict):
            raise Error('Expected {"provider":"xtream","settings":{...}}')
        return "provider_settings", data
    if verb == "playlist" and len(tail) == 1:
        return "provider_settings", {"provider": "m3u", "settings": {"playlist": text}}
    if verb == "msg" and tail:
        return "command", {"command": "popup_message", "message": text}
    if verb == "exit" and not tail:
        return "command", {"command": "exit_player"}
    if verb == "random":
        params = {"command": "random_channel"}
        if tail:
            if len(tail) != 2 or not all(re.fullmatch(r"[1-9]\d*", x) for x in tail) or int(tail[0]) > int(tail[1]):
                raise Error("Use random or random FROM TO (one-based channel numbers)")
            params["random_range"] = [int(x) for x in tail]
        return "command", params
    if verb == "play":
        if not tail:
            raise Error("play requires a channel number or name")
        return "play", {"query": text}
    return "play", {"query": " ".join(words)}


def provision_kubernetes(client, config_path, name, device):
    kube = client.config["kubernetes"]
    journal_path = Path(config_path).expanduser().with_name(Path(config_path).name + ".pending-add.json")
    identity = {"name": name.casefold(), "device": device, "kubernetes": kube, "server": client.server,
                "server_config": str(Path(client.config["server_config"]).expanduser().resolve())}
    resuming = journal_path.exists()
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


def management(client, config_path, words, json_output=False):
    verb = words[0].casefold()
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
        if not re.fullmatch(r"[a-zA-Z0-9\u0430-\u044f\u0410-\u042f\u0451\u0401_-]{1,32}", name) or name.casefold() in {"devices", "alias", "add", "pair", "help", "discover", "pending", "approve"}:
            raise Error("Choose a name of up to 32 letters/digits, underscores or hyphens, without spaces")
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", device):
            raise Error("Invalid device UUID")
        aliases = client.config.setdefault("players", {})
        existing = next((key for key in aliases if key.casefold() == name.casefold()), name)
        if existing in aliases and aliases[existing] != device:
            raise Error("The name is already bound to another UUID; edit cli.json to reassign it")
        registered = any(x["id"] == device for x in client.credentials["devices"])
        journal_path = Path(config_path).expanduser().with_name(Path(config_path).name + ".pending-add.json")
        if verb == "add" and client.config.get("kubernetes") and (not registered or journal_path.exists()):
            if not registered and len(client.credentials["devices"]) >= 64:
                raise Error("The limit of 64 devices has been reached")
            provision_kubernetes(client, config_path, existing, device)
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


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=os.environ.get("OTT_CONFIG", str(Path.home() / ".config/ottplay-control/cli.json")))
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.help or not args.words or args.words == ["help"]:
        print(HELP)
        return 0
    try:
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300:
            raise Error("--timeout must be between 1 and 300 seconds")
        client = Client(read_json(args.config), args.timeout)
        if management(client, args.config, args.words, json_output=args.json):
            return 0
        device = client.device(args.words[0])
        words = args.words[1:]
        action, params = parse_command(words)
        data = client.call(device, action, params)
        playback_error = None
        if action == "programs" and params["search"] and words[1:2] != ["--list"]:
            if data["programs"]:
                try:
                    number = data["programs"][0].get("number")
                    if type(number) is not int or number < 1:
                        raise Error("The first programme has no valid channel number; no switch was requested")
                    # Use the returned catalogue number, even for duplicate or numeric names.
                    playback = client.call(device, "play", {"query": str(number)})
                    channel = playback.get("channel")
                    if (playback.get("dispatched") is not True or not isinstance(channel, dict)
                            or type(channel.get("number")) is not int or channel["number"] != number
                            or not isinstance(channel.get("name"), str)):
                        raise Error("The player did not confirm the requested channel switch. The request may have executed; do not repeat the change blindly.")
                    data["playback"] = playback
                except Error as exc:
                    playback_error = exc
                    data["playback"] = {"error": str(exc)}
            else:
                print("No current programmes match the search.", file=sys.stderr)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif action == "channels":
            for row in data["channels"]:
                print(f"{row['number']}: {clean(row['name'])}")
        elif action == "programs":
            for row in data["programs"]:
                print(f"{clean(row['channel'])} — {clean(row['title'])}")
        elif action == "status" and words and words[0].casefold() == "v":
            if data.get("volume") is None:
                raise Error("The platform does not report volume")
            print(f"{data['volume']:g}%")
        elif action == "providers":
            for row in data["providers"]:
                print(f"{'*' if row['active'] else ' '} {row['index']}: {clean(row['id'])} — {clean(row['name'])}")
        elif action == "play":
            print(f"Channel switch requested: {data['channel']['number']}: {clean(data['channel']['name'])}")
        elif action == "command" and params.get("command") == "set_volume":
            if data.get("volume") is None:
                raise Error("The command was sent, but the platform does not report volume")
            print(f"{data['volume']:g}%")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        if action == "programs" and data.get("playback", {}).get("dispatched"):
            channel = data["playback"]["channel"]
            print(f"Channel switch requested: {channel['number']}: {clean(channel['name'])}", file=sys.stderr)
        if action == "programs" and data.get("partial"):
            print(f"EPG is partially loaded: checked {data['checked']} of {data['total']} channels. Try again later.", file=sys.stderr)
        if playback_error:
            raise playback_error
        return 3 if action == "programs" and data.get("partial") else 0
    except (Error, KeyError, TypeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print("Error: " + (str(exc) if isinstance(exc, Error) else "Check the configuration; the operation did not complete"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
