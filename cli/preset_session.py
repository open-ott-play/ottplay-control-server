"""Resumable preset application: private receipts, exact readback, no blind replay."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
import urllib.parse


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def comparisons(preset):
    yield "plex", {"provider": "plex", "settings": preset["plex"]}
    for row in preset["m3u"]:
        yield "m3u_" + str(row["number"]), {"provider": "m3u", **row}
    for row in preset.get("stalker", []):
        yield "stalker_" + str(row["profile"]), {"provider": "stalker", "number": row["profile"],
            "settings": {k: row[k] for k in ("name", "server", "mac")}}


def compare(api, client, device, params):
    result = client.call(device, "settings_compare", params)
    if (not isinstance(result, dict) or result.get("provider") != params["provider"]
            or result.get("number") != params.get("number") or type(result.get("matched")) is not bool):
        raise api.Error("The player did not return a valid exact settings comparison")
    return result["matched"]


def verify(api, client, device, preset):
    rows = [{"step": name, "matched": compare(api, client, device, params)}
            for name, params in comparisons(preset)]
    metadata = api.profile_metadata(client.call(device, "profiles", {}), "profiles", {}, expected_provider="m3u")
    active = next(row["number"] for row in metadata["profiles"] if row["active"])
    return {"status": "verified" if all(row["matched"] for row in rows) and active == preset["active_profile"] else "different",
            "comparisons": rows, "active_profile": active, "expected_active_profile": preset["active_profile"]}


class Session:
    def __init__(self, api, client, device, name, preset, path, resume):
        self.api, self.client, self.device = api, client, device
        self.path = Path(path).expanduser()
        self.lock = None
        self.state = {"version": 1, "device": device, "preset": name, "revision": digest(preset),
                      "server": digest(client.server), "completed": [], "pending": None}
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt" and stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
                raise api.Error("Checkpoint directory must be private (0700)")
            for candidate in (self.path, Path(str(self.path) + ".lock")):
                if candidate.exists() or candidate.is_symlink():
                    mode = candidate.lstat().st_mode
                    if not stat.S_ISREG(mode) or (os.name != "nt" and stat.S_IMODE(mode) & 0o077):
                        raise api.Error("Checkpoint files must be regular private files (0600)")
            self.lock = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if os.name == "nt":
                import msvcrt
                os.write(self.lock, b"0")
                os.lseek(self.lock, 0, os.SEEK_SET)
                msvcrt.locking(self.lock, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if resume:
                if self.path.stat().st_size > 65536:
                    raise api.Error("Checkpoint is too large")
                saved = api.read_json(self.path)
                if (not isinstance(saved, dict) or set(saved) != set(self.state)
                        or any(saved.get(k) != self.state[k] for k in ("version", "device", "preset", "revision", "server"))
                        or not isinstance(saved["completed"], list) or len(saved["completed"]) > 100
                        or any(not isinstance(x, str) or len(x) > 64 for x in saved["completed"])):
                    raise api.Error("Checkpoint does not match this server, player and preset revision")
                self.state = saved
            elif self.path.exists():
                raise api.Error("Checkpoint exists; use resume or a new checkpoint path")
            query = "?" + urllib.parse.urlencode({"device_id": device})
            self.operations = "/api/operations" + query
            code, caps = client.api(self.operations)
            self.ttl = caps.get("command_ttl_seconds") if isinstance(caps, dict) else None
            if code != 200 or not isinstance(caps, dict) or caps.get("enabled") is not True or type(self.ttl) is not int or not 1 <= self.ttl <= 3600:
                raise api.Error("Resumable apply requires a controller with the command journal enabled")
            self.recover()
            self.save()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def save(self):
        fd, name = tempfile.mkstemp(prefix=".ott-preset-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.state, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            if os.name != "nt":
                fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def recover(self):
        pending = self.state["pending"]
        if pending is None:
            return
        if (not isinstance(pending, dict) or set(pending) != {"label", "key", "safe_after"}
                or not isinstance(pending["label"], str) or len(pending["label"]) > 64
                or not isinstance(pending["key"], str) or not self.api.re.fullmatch(r"[a-f0-9]{32}", pending["key"])
                or type(pending["safe_after"]) not in (int, float) or not self.api.math.isfinite(pending["safe_after"])):
            raise self.api.Error("Invalid pending checkpoint record")
        responded = False
        try:
            code, result = self.client.api(self.operations + "&key=" + pending["key"])
            responded = code == 200 and isinstance(result, dict) and result.get("state") == "responded" and result.get("result_status") in ("ok", "rejected", "unsupported")
        except self.api.HTTPError as exc:
            if exc.code != 404:
                raise
        if not responded and time.time() <= pending["safe_after"]:
            raise self.api.Error("Previous step has an uncertain outcome. Resume after its command deadline; no command was replayed")
        self.state["pending"] = None

    def prepare(self, label, action, params):
        check = None
        if action == "profile_settings":
            check = {"provider": "m3u", **params}
        elif action == "provider_settings":
            if params["provider"] == "stalker":
                row = params["settings"]
                check = {"provider": "stalker", "number": row["profile"],
                         "settings": {k: row[k] for k in ("name", "server", "mac")}}
            else:
                check = params
        if check is not None and compare(self.api, self.client, self.device, check):
            self.complete(label)
            return True
        if action in ("provider", "profile", "profile_settings", "provider_settings"):
            self.retry(label, action, params)
        return False

    def retry(self, label, action, params):
        # An intent is durable before sending. A lost POST response remains bounded
        # by the server TTL plus this request's full timeout and a clock margin.
        self.state["pending"] = {"label": label, "key": secrets.token_hex(16),
                                 "safe_after": time.time() + self.ttl + self.client.timeout + 5}
        self.save()

    def call(self, action, params):
        pending = self.state["pending"]
        if pending is None:
            return self.client.call(self.device, action, params)
        return self.client.call(self.device, action, params, idempotency_key=pending["key"])

    def complete(self, label):
        self.state["pending"] = None
        if label not in self.state["completed"]:
            self.state["completed"].append(label)
        self.save()


def run(api, config, words, timeout, json_output, on_receipt):
    if len(words) not in (4, 6) or words[3] not in ("plan", "apply", "resume", "verify") or (len(words) == 6 and words[4] != "--checkpoint"):
        raise api.Error("Use PLAYER load PRESET plan|apply|resume|verify [--checkpoint FILE]")
    name, preset = api.validate_preset(config, words[2])
    mode = words[3]
    client = api.Client(config, timeout)
    client.on_receipt = on_receipt
    device = client.device(words[0])
    if mode == "plan":
        result = {"preset": name, "device": device, "status": "planned", "revision": digest(preset),
                  "settings": [label for label, _ in comparisons(preset)], "active_profile": preset["active_profile"]}
    elif mode == "verify":
        result = {"preset": name, "device": device, **verify(api, client, device, preset)}
    else:
        path = words[5] if len(words) == 6 else (Path.home() / ".config/ottplay-control/checkpoints" / (digest([client.server, device, name]) + ".json"))
        session = Session(api, client, device, name, preset, path, mode == "resume")
        try:
            # Preflight compares every provider before the first mutation, so an
            # old player cannot receive only half of a resumable application.
            for _, params in comparisons(preset):
                compare(api, client, device, params)
            result = api.load_preset(client, device, name, preset, session=session)
            if result["status"] == "loaded":
                result = {**result, **verify(api, client, device, preset)}
        finally:
            session.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("planned", "verified") else 130 if result["status"] == "interrupted" else 1
