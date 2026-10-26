"""Administrator registration with durable, source-bound operation recovery."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
import urllib.parse

HELP = """ott registry [list]
  ott registry add NAME UUID [--diagnostics on|off]
  ott registry approve NAME UUID CODE [--diagnostics on|off]
  ott registry revoke|rotate NAME_OR_UUID
  ott registry reconcile NAME_OR_UUID [--convert-to-dynamic]
  ott registry credential NAME_OR_UUID --output FILE | --show-secret
  ott registry resume [--checkpoint FILE]
  ott registry forget [--checkpoint FILE]
  ott remove NAME                    revoke access, then remove this local alias
  ott unalias NAME                   remove only the local alias

Mutations accept --checkpoint FILE. A private checkpoint is saved before sending.
Resume reads the original receipt before any explicit retry. Forget removes only
the local checkpoint. Revoke does not prove that playback stopped on the device.
"""
ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
HEX = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}")
NAME = re.compile(r"[a-zA-Z0-9а-яА-ЯёЁ_-]{1,32}")
LIMITS = {"active_devices": 64, "inventory_devices": 1088, "records": 1024,
          "operations": 192, "ordinary_operations": 128,
          "reserved_revocations": 64, "revoked_hashes": 64}


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def fail(api, message):
    raise api.Error(message)


def inventory(api, raw):
    invalid = "The controller returned invalid device registry metadata"
    if (not isinstance(raw, dict) or set(raw) != {"version", "store_id", "revision", "devices", "limits"}
            or type(raw["version"]) is not int or raw["version"] != 1 or not matches(HEX, raw["store_id"])
            or type(raw["revision"]) is not int or not 0 <= raw["revision"] < 9007199254740991
            or not isinstance(raw["limits"], dict) or raw["limits"] != LIMITS
            or any(type(v) is not int for v in raw["limits"].values())
            or not isinstance(raw["devices"], list) or len(raw["devices"]) > LIMITS["inventory_devices"]):
        fail(api, invalid)
    seen = set()
    for row in raw["devices"]:
        required = {"device_id", "generation", "origin", "state", "diagnostics_enabled"}
        if (not isinstance(row, dict) or not required <= set(row) <= required | {"static_fingerprint"}
                or not matches(ID, row["device_id"]) or row["device_id"] in seen
                or not matches(HEX, row["generation"]) or row["origin"] not in ("static", "dynamic", "static_override")
                or row["state"] not in ("active", "revoked", "conflict") or type(row["diagnostics_enabled"]) is not bool
                or ("static_fingerprint" in row and not matches(HASH, row["static_fingerprint"]))
                or (row["origin"] == "static" and "static_fingerprint" not in row)):
            fail(api, invalid)
        seen.add(row["device_id"])
    if sum(row["state"] == "active" for row in raw["devices"]) > 64:
        fail(api, invalid)
    return raw


def receipt(api, raw, request, kind):
    fields = {"version", "operation_id", "device_id", "generation", "state", "persisted"}
    if kind == "revoke":
        fields.add("device_stop_confirmed")
    if (not isinstance(raw, dict) or set(raw) != fields or type(raw["version"]) is not int or raw["version"] != 1
            or raw["operation_id"] != request["operation_id"] or raw["device_id"] != request["device_id"]
            or not matches(HEX, raw["generation"]) or raw["persisted"] is not True
            or raw["state"] != ("revoked" if kind == "revoke" else "active")
            or (kind == "revoke" and raw["device_stop_confirmed"] is not False)):
        fail(api, "Invalid registry receipt; the operation may have completed. Use registry resume")
    return raw


def private_read(api, path, limit=65536):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError()
        return api.decode_response_json(data)
    except (OSError, ValueError, RecursionError):
        fail(api, "Cannot read the bounded private registry file")


def sync_parent(path):
    if os.name != "nt":
        fd = os.open(str(Path(path).parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def save_private(api, path, value, exclusive=False):
    path, temporary = Path(path), None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".ott-registry-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        sync_parent(path)
    except (OSError, ValueError):
        fail(api, "Local registry persistence failed; reconcile the checkpoint before retrying. Existing output files are never overwritten")
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class Guard:
    def __init__(self, api, path):
        self.fd = None
        self.api, self.path = api, Path(path).expanduser().absolute()

    def __enter__(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = self.path.parent.lstat().st_mode
            if not stat.S_ISDIR(mode) or (os.name != "nt" and mode & 0o077):
                fail(self.api, "Registry checkpoint directory must be private (0700)")
            lock = Path(str(self.path) + ".lock")
            for candidate in (self.path, lock):
                if candidate.exists() or candidate.is_symlink():
                    mode = candidate.lstat().st_mode
                    if not stat.S_ISREG(mode) or (os.name != "nt" and mode & 0o077):
                        fail(self.api, "Registry checkpoint and lock must be private regular files (0600)")
            self.fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if os.name == "nt":
                import msvcrt
                os.write(self.fd, b"0")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if not isinstance(exc, Exception) or isinstance(exc, self.api.Error):
                raise
            fail(self.api, "Another registry operation is active or its private lock is unavailable")

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def remove_checkpoint(api, path):
    try:
        Path(path).unlink()
        sync_parent(path)
    except OSError:
        fail(api, "The operation completed but its checkpoint remains; use registry resume")


def binding(api, client, config_path, store_id):
    credentials = client.credentials
    if (not isinstance(credentials, dict) or credentials.get("operator_token") is not None
            or credentials.get("admin_token") != client.token or not matches(TOKEN, client.token)):
        fail(api, "Device registration requires an administrator credential profile")
    return {"server": client.server, "config": str(Path(config_path).expanduser().resolve()),
            "credentials": str(Path(client.config["server_config"]).expanduser().resolve()),
            "administrator": hashlib.sha256(client.token.encode()).hexdigest(), "store_id": store_id}


def alias_name(api, value):
    if not matches(NAME, value) or value.casefold() in api.TOP_LEVEL_COMMANDS:
        fail(api, "Choose a non-reserved player alias of up to 32 letters, digits, underscores or hyphens")
    return value


def alias_key(api, config, name, device=None):
    aliases = config.get("players", {})
    if not isinstance(aliases, dict) or any(not isinstance(k, str) or not matches(ID, v) for k, v in aliases.items()):
        fail(api, "The local player alias mapping is invalid")
    found = [key for key in aliases if key.casefold() == name.casefold()]
    if len(found) > 1 or (found and device is not None and aliases[found[0]] != device):
        fail(api, "The alias is bound to another identity or is ambiguous")
    return found[0] if found else name


def update_alias(api, client, config_path, expected, name, device, remove=False):
    current = private_read(api, config_path, 4 * 1024 * 1024)
    if not isinstance(current, dict):
        fail(api, "The local configuration changed")
    check = api.Client(current, client.timeout)
    if binding(api, check, config_path, expected["store_id"]) != expected:
        fail(api, "The controller or administrator profile changed; no alias was changed")
    key = alias_key(api, current, name, device)
    if remove:
        current.get("players", {}).pop(key, None)
    else:
        current.setdefault("players", {})[key] = device
    save_private(api, Path(config_path).expanduser(), current)
    client.config = current


def valid_request(kind, request):
    base = {"version", "operation_id", "device_id"}
    if (not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1
            or not matches(HEX, request.get("operation_id")) or not matches(ID, request.get("device_id"))):
        return False
    if kind == "approve":
        required = base | {"id", "code"}
        return (required <= set(request) <= required | {"diagnostics_enabled"}
                and matches(HEX, request["id"]) and isinstance(request["code"], str)
                and re.fullmatch(r"[A-Z2-7]{8}", request["code"]) is not None
                and ("diagnostics_enabled" not in request or type(request["diagnostics_enabled"]) is bool))
    if kind == "add":
        required, extra = base | {"diagnostics_enabled"}, {"expected_generation", "convert_to_dynamic"}
        return (set(request) in (required, required | extra) and type(request["diagnostics_enabled"]) is bool
                and (not extra <= set(request) or (request["convert_to_dynamic"] is True and matches(HEX, request["expected_generation"]))))
    if kind not in ("rotate", "revoke", "reconcile"):
        return False
    fields = base | {"expected_generation"} | ({"observed_static_fingerprint"} if kind == "reconcile" else set())
    return (set(request) == fields and matches(HEX, request["expected_generation"])
            and (kind != "reconcile" or matches(HASH, request["observed_static_fingerprint"])))


class Session:
    def __init__(self, api, client, config_path):
        self.api, self.client, self.config_path = api, client, config_path
        self.deadline = time.monotonic() + client.timeout

    def call(self, path, payload=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            fail(self.api, "Registry operation timed out; reconcile its checkpoint before retrying")
        result = self.client.api(path, payload, timeout=remaining)
        if time.monotonic() >= self.deadline:
            fail(self.api, "Registry operation timed out; reconcile its checkpoint before retrying")
        return result

    def read(self):
        code, raw = self.call("/api/device-registry")
        if code != 200:
            fail(self.api, "The registry did not return a complete inventory")
        return inventory(self.api, raw)

    def identity(self, data, name):
        key = alias_key(self.api, self.client.config, name)
        device = self.client.config.get("players", {}).get(key, name)
        found = [row for row in data["devices"] if row["device_id"] == device]
        if len(found) != 1:
            fail(self.api, "No unique registered identity matches this player; use registry list")
        return found[0]

    def confirm(self, state, result):
        current = self.read()
        if binding(self.api, self.client, self.config_path, current["store_id"]) != state["binding"]:
            fail(self.api, "The registry store changed; no local alias was changed")
        rows = [row for row in current["devices"] if row["device_id"] == result["device_id"]]
        if len(rows) != 1 or any(rows[0][key] != result[key] for key in ("generation", "state")):
            fail(self.api, "The operation completed but the device changed afterward; no alias was changed. Inspect registry list")
        if state["alias"]:
            update_alias(self.api, self.client, self.config_path, state["binding"], state["alias"],
                         result["device_id"], state["remove_alias"])
        return result

    def send(self, state, path):
        request, kind = state["request"], state["kind"]
        payload, endpoint = dict(request), "/api/device-registry/" + kind
        if kind == "approve":
            endpoint = "/api/pairings/approve"
            payload.pop("device_id")
            payload.pop("version")
        try:
            code, result = self.call(endpoint, payload)
        except self.api.HTTPError as exc:
            if exc.code < 500:
                remove_checkpoint(self.api, path)
                raise
            fail(self.api, "Registry outcome is unconfirmed. Keep the checkpoint and use registry resume; no Secret fallback was attempted")
        except self.api.Error:
            fail(self.api, "Registry outcome is unconfirmed. Keep the checkpoint and use registry resume; no request was repeated")
        if code != 200:
            fail(self.api, "Registry outcome is unconfirmed; use registry resume")
        if kind == "approve":
            if result != {"status": "approved"}:
                fail(self.api, "Pairing outcome is unconfirmed; use registry resume")
            code, result = self.call("/api/device-registry/operations?" + urllib.parse.urlencode({"id": request["operation_id"]}))
            if code != 200:
                fail(self.api, "Pairing receipt is unconfirmed; use registry resume")
        result = self.confirm(state, receipt(self.api, result, request, kind))
        remove_checkpoint(self.api, path)
        return result


    def resume(self, path, current):
        state = private_read(self.api, path)
        fields = {"version", "binding", "kind", "request", "alias", "remove_alias", "created_at"}
        if (not isinstance(state, dict) or set(state) != fields or type(state["version"]) is not int
                or state["version"] != 1 or state["binding"] != binding(self.api, self.client, self.config_path, current["store_id"])
                or type(state["created_at"]) is not int or not 0 <= time.time() - state["created_at"] < 86400
                or not valid_request(state["kind"], state["request"]) or type(state["remove_alias"]) is not bool
                or (state["remove_alias"] and (state["kind"] != "revoke" or state["alias"] is None))
                or (state["alias"] is not None and state["kind"] not in ("add", "approve") and not state["remove_alias"])
                or (state["alias"] is not None and not matches(NAME, state["alias"]))):
            fail(self.api, "Checkpoint does not match this controller/profile/store or its 24-hour recovery window; no mutation was sent")
        if state["alias"] is not None:
            alias_name(self.api, state["alias"])
        try:
            code, raw = self.call("/api/device-registry/operations?" + urllib.parse.urlencode({"id": state["request"]["operation_id"]}))
        except self.api.HTTPError as exc:
            if exc.code != 404:
                raise
            return self.send(state, path)
        if code != 200:
            fail(self.api, "The original operation could not be reconciled; no mutation was repeated")
        result = self.confirm(state, receipt(self.api, raw, state["request"], state["kind"]))
        remove_checkpoint(self.api, path)
        return result

def print_result(api, result, json_output):
    if json_output:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    elif "devices" in result:
        for row in result["devices"]:
            print(f"{api.clean(row['device_id'])}: {row['state']}; {row['origin']}; diagnostics={'on' if row['diagnostics_enabled'] else 'off'}; generation={row['generation']}")
        if not result["devices"]:
            print("No registered devices.")
    else:
        print(f"{api.clean(result['device_id'])}: {result['state']}; durable generation {result['generation']}")
        if result["state"] == "revoked":
            print("Access revoked. Playback stopping has not been confirmed.")


def option(api, words, name):
    if name not in words:
        return None
    index = words.index(name)
    if words.count(name) != 1 or index + 1 >= len(words):
        fail(api, name + " requires one value")
    value = words[index + 1]
    del words[index:index + 2]
    return value


def pending_pairing(api, status, pending, device, code):
    rows = pending.get("pairings") if isinstance(pending, dict) else None
    if status != 200 or not isinstance(rows, list) or len(rows) > 64 or set(pending) != {"pairings"}:
        fail(api, "Invalid pending pairing list")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"id", "device_id", "code", "address", "expires_in"}
                or not matches(HEX, row["id"]) or row["id"] in seen or not matches(ID, row["device_id"])
                or not isinstance(row["code"], str) or re.fullmatch(r"[A-Z2-7]{8}", row["code"]) is None
                or not isinstance(row["address"], str) or not 1 <= len(row["address"]) <= 2048
                or type(row["expires_in"]) is not int or not 0 <= row["expires_in"] <= 600):
            fail(api, "Invalid pending pairing list")
        seen.add(row["id"])
    found = [row for row in rows if row["device_id"] == device and row["code"] == code]
    if len(found) != 1:
        fail(api, "No unique pending pairing matches this identity and code")
    return found[0]["id"]


def credential(session, data, words, json_output, pair=False):
    api, client = session.api, session.client
    output = option(api, words, "--output")
    show = "--show-secret" in words
    if show:
        words.remove("--show-secret")
    if len(words) != 1 or (not pair and bool(output) == show):
        fail(api, "Use registry credential NAME --output FILE or --show-secret")
    row = session.identity(data, words[0])
    if row["state"] != "active":
        fail(api, "This device is not active; no credential was requested")
    code, value = session.call("/api/device-registry/credential?" + urllib.parse.urlencode({"device_id": row["device_id"], "generation": row["generation"]}))
    if (code != 200 or not isinstance(value, dict) or set(value) != {"version", "device_id", "generation", "token"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["device_id"] != row["device_id"] or value["generation"] != row["generation"]
            or not matches(TOKEN, value["token"])):
        fail(api, "The active credential response is invalid")
    if output:
        save_private(api, Path(output).expanduser(), value, exclusive=True)
        print(json.dumps({"saved": True}) if json_output else "Credential saved to the private output file.")
    elif json_output:
        print(json.dumps(value, indent=2))
    else:
        print("Settings → Remote control → Command server")
        print("Address:", client.config.get("player_server", client.server))
        print("Access code:", value["token"])
        print("Device UUID / queue:", value["device_id"])


def dispatch(api, client, config_path, words, json_output=False):
    verb = words[0].casefold()
    explicit = verb == "registry"
    if verb not in ("registry", "unalias", "remove", "add", "alias", "pair", "approve"):
        return False
    if explicit and words[1:] in (["--help"], ["-h"]):
        print(HELP)
        return True
    if verb == "unalias":
        if len(words) != 2:
            fail(api, "Use ott unalias NAME; this removes only the local name")
        with Guard(api, str(config_path) + ".registry"):
            config = private_read(api, config_path, 4 * 1024 * 1024)
            key = alias_key(api, config, words[1])
            if key not in config.get("players", {}):
                fail(api, "No local alias matches this name")
            del config["players"][key]
            save_private(api, Path(config_path).expanduser(), config)
        print(json.dumps({"unalias": words[1], "revoked": False}) if json_output else "Local alias removed; server access was not revoked.")
        return True
    if not explicit and ((verb in ("add", "alias", "approve") and len(words) != 3)
                         or (verb in ("pair", "remove") and len(words) != 2)):
        return False
    if verb in ("add", "alias"):
        alias_name(api, words[1])
        if not matches(ID, words[2]):
            fail(api, "Invalid device UUID")
    if verb == "approve" and not re.fullmatch(r"[A-Z0-9]{8}", words[2].upper()):
        fail(api, "Enter the eight-character pairing code displayed by the player")
    session = Session(api, client, config_path)
    checkpoint = Path(config_path).expanduser().with_name(Path(config_path).name + ".registry-pending.json")
    tail = list(words[1:]) if explicit else [verb, *words[1:]]
    custom = option(api, tail, "--checkpoint")
    if custom:
        checkpoint = Path(custom).expanduser()
    action = tail.pop(0).casefold() if tail else "list"
    if action == "forget":
        if tail:
            fail(api, "Use registry forget [--checkpoint FILE]")
        with Guard(api, checkpoint):
            remove_checkpoint(api, checkpoint)
        print(json.dumps({"forgotten": True, "undone": False}) if json_output else "Local checkpoint removed; the server operation was not undone.")
        return True
    try:
        data = session.read()
    except api.HTTPError as exc:
        if (not explicit and verb != "remove" and exc.code == 404 and not checkpoint.exists()
                and not client.credentials.get("device_registry")):
            return False
        raise
    expected = binding(api, client, config_path, data["store_id"])
    if action == "list":
        if tail:
            fail(api, "Use registry list")
        print_result(api, data, json_output)
        return True
    if action in ("credential", "pair"):
        credential(session, data, tail, json_output, action == "pair")
        return True
    with Guard(api, str(config_path) + ".registry"), Guard(api, checkpoint):
        if action == "resume":
            if tail:
                fail(api, "Use registry resume [--checkpoint FILE]")
            print_result(api, session.resume(checkpoint, data), json_output)
            return True
        if checkpoint.exists():
            fail(api, "An unfinished registry operation exists; use registry resume or inspect and forget its checkpoint")
        action, request, alias, remove_alias = prepare(session, data, action, tail, explicit)
        if action == "alias":
            update_alias(api, client, config_path, expected, alias, request["device_id"])
            print(json.dumps({"alias": alias, "device_id": request["device_id"]}) if json_output else f"{alias} → {request['device_id']}")
            return True
        if not valid_request(action, request):
            fail(api, "Invalid registry operation; no request was sent")
        state = {"version": 1, "binding": expected, "kind": action, "request": request,
                 "alias": alias, "remove_alias": remove_alias, "created_at": int(time.time())}
        save_private(api, checkpoint, state)
        print_result(api, session.send(state, checkpoint), json_output)
        return True


def prepare(session, data, action, tail, explicit):
    api, client = session.api, session.client
    alias, remove_alias = None, action == "remove"
    diagnostics = option(api, tail, "--diagnostics")
    if diagnostics is not None and diagnostics not in ("on", "off"):
        fail(api, "--diagnostics requires on or off")
    convert = "--convert-to-dynamic" in tail
    if convert:
        tail.remove("--convert-to-dynamic")
    request = {"version": 1, "operation_id": secrets.token_hex(16)}
    if action in ("add", "alias"):
        if len(tail) != 2 or convert or (action == "alias" and diagnostics is not None):
            fail(api, "Use registry add NAME UUID [--diagnostics on|off]")
        alias, device = alias_name(api, tail[0]), tail[1]
        if not matches(ID, device):
            fail(api, "Invalid device UUID")
        alias_key(api, client.config, alias, device)
        if action == "alias" and session.identity(data, device)["state"] != "active":
            fail(api, "Alias requires an active registered identity")
        request.update(device_id=device, diagnostics_enabled=diagnostics == "on")
    elif action == "approve":
        if convert or len(tail) != (3 if explicit else 2):
            fail(api, "Use registry approve NAME UUID CODE [--diagnostics on|off]")
        alias = alias_name(api, tail[0])
        device = tail[1] if explicit else session.identity(data, tail[0])["device_id"]
        code = tail[-1].upper()
        if not matches(ID, device) or not re.fullmatch(r"[A-Z2-7]{8}", code):
            fail(api, "Enter the device UUID and eight-character pairing code")
        alias_key(api, client.config, alias, device)
        status, pending = session.call("/api/pairings")
        request.update(device_id=device, id=pending_pairing(api, status, pending, device, code), code=code)
        if diagnostics is not None:
            request["diagnostics_enabled"] = diagnostics == "on"
    elif action in ("remove", "revoke", "rotate", "reconcile"):
        if len(tail) != 1 or diagnostics is not None or (convert and action != "reconcile"):
            fail(api, "Use registry revoke|rotate|reconcile NAME_OR_UUID")
        row = session.identity(data, tail[0])
        request.update(device_id=row["device_id"], expected_generation=row["generation"])
        if action == "remove":
            alias, action = alias_name(api, tail[0]), "revoke"
            if alias_key(api, client.config, alias) not in client.config.get("players", {}):
                fail(api, "Remove requires a local alias; use registry revoke for a UUID")
        if action == "reconcile":
            if convert:
                if row["state"] != "conflict" or "static_fingerprint" in row:
                    fail(api, "Dynamic conversion requires a conflicted identity whose static entry was removed")
                action = "add"
                request.update(convert_to_dynamic=True, diagnostics_enabled=row["diagnostics_enabled"])
            else:
                if "static_fingerprint" not in row:
                    fail(api, "Static identity is absent; inspect before explicit --convert-to-dynamic")
                request["observed_static_fingerprint"] = row["static_fingerprint"]
    else:
        fail(api, "Unknown registry command; use registry --help")
    return action, request, alias, remove_alias
