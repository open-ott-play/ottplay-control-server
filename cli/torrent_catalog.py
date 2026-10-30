"""Typed Torrent catalog CLI. No source fetches or automatic mutation replay."""
import base64
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid


def _document_module():
    path = Path(__file__).resolve().with_name("torrent_document.py")
    spec = importlib.util.spec_from_file_location("ott_torrent_document", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


doc = _document_module()
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
CHUNK_BYTES = 6144
RPC_INTERVAL = 1.5
REASONS = frozenset("invalid_request unsupported policy_restricted runtime_changed source_changed revision_conflict "
    "snapshot_not_found operation_not_found operation_conflict busy expired cancelled metadata_failed metadata_timeout "
    "no_files invalid_document document_too_large hash_mismatch offset_mismatch incomplete catalog_mismatch "
    "entry_conflict entry_not_found file_not_found storage_failed effect_failed".split())
USAGE = ("Use torrent list, files ENTRY, inspect SOURCE, add SOURCE --name TITLE [--files 0,3], "
         "add --metadata UUID --name TITLE [--files 0,3], refresh ENTRY, rename ENTRY TITLE, remove ENTRY, "
         "play ENTRY INDEX, status UUID, cancel UUID, export FILE [--with-favorites], or "
         "import FILE [merge|replace] [--dry-run] [--with-favorites]")


class CatalogError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Torrent catalog: " + code.replace("_", " "))


def require(condition, code="invalid_response"):
    if not condition:
        raise CatalogError(code)


def _pattern(value, pattern):
    require(type(value) is str and pattern.fullmatch(value) is not None)
    return value


def _shape(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def _integer(value, low=0, high=doc.MAX_SAFE):
    require(type(value) is int and low <= value <= high)
    return value


def _uuid(value):
    return _pattern(value, UUID)


def snapshot(value, kind=None):
    _shape(value, ("id", "kind", "revision", "sha256", "size_bytes"))
    _uuid(value["id"])
    require(value["kind"] in ("catalog", "metadata") and (kind is None or value["kind"] == kind))
    if value["kind"] == "catalog":
        _pattern(value["revision"], TOKEN)
    else:
        require(value["revision"] is None)
    _pattern(value["sha256"], SHA)
    _integer(value["size_bytes"], 1, doc.MAX_BYTES)
    return copy.deepcopy(value)


def operation(value, identifier, kind=None):
    require(type(value) is dict)
    actual_kind = value.get("kind")
    _shape(value, {"id", "kind", "state", "next_offset", "snapshot", "reason"}
           | ({"target"} if actual_kind == "play" else set()))
    require(_uuid(value["id"]) == identifier and actual_kind in ("metadata", "write", "play")
            and (kind is None or actual_kind == kind))
    states = {"metadata": ("pending", "ready", "failed", "cancelled"),
              "write": ("uploading", "committed", "failed", "cancelled"),
              "play": ("pending", "accepted", "failed", "cancelled")}
    state = value["state"]
    require(state in states[actual_kind])
    if state == "uploading":
        _integer(value["next_offset"], 0, doc.MAX_BYTES)
    else:
        require(value["next_offset"] is None)
    if state in ("ready", "committed"):
        snapshot(value["snapshot"], "metadata" if state == "ready" else "catalog")
    else:
        require(value["snapshot"] is None)
    require(value["reason"] in REASONS if state == "failed" else value["reason"] is None)
    if actual_kind == "play":
        _shape(value["target"], ("catalog_id", "entry_id", "file_index"))
        doc.identifier(value["target"]["catalog_id"])
        doc.identifier(value["target"]["entry_id"])
        _integer(value["target"]["file_index"], 0, 999999999)
    return copy.deepcopy(value)


def decode_chunk(value):
    require(type(value) is str and 0 < len(value) <= 8192)
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise CatalogError("invalid_response") from None
    require(0 < len(raw) <= CHUNK_BYTES and base64.b64encode(raw).decode("ascii") == value)
    return raw


def validate_response(value, request):
    require(type(value) is dict and len(doc.canonical(value)) <= 65536)
    require(type(value.get("version")) is int and value["version"] == 1
            and value.get("runtime") == request["runtime"] and value.get("op") == request["op"])
    common = {"version", "op", "runtime"}
    if "error" in value:
        _shape(value, common | {"error", "reason"})
        require(value["error"] == "Torrent catalog request failed." and value["reason"] in REASONS)
        raise CatalogError(value["reason"])
    op = request["op"]
    if op == "read":
        _shape(value, common | {"snapshot"})
        snapshot(value["snapshot"], "catalog")
    elif op == "read_chunk":
        _shape(value, common | {"snapshot_id", "offset", "data_b64", "next_offset"})
        require(value["snapshot_id"] == request["snapshot_id"])
        require(_integer(value["offset"], 0, doc.MAX_BYTES - 1) == request["offset"])
        chunk = decode_chunk(value["data_b64"])
        require(len(chunk) <= request["max_bytes"] and request["offset"] + len(chunk) <= doc.MAX_BYTES)
        if value["next_offset"] is not None:
            require(_integer(value["next_offset"], 1, doc.MAX_BYTES - 1) == request["offset"] + len(chunk))
    elif op == "play":
        _shape(value, common | {"operation_id", "accepted", "catalog_id", "entry_id", "file_index"})
        require(value["accepted"] is True and value["operation_id"] == request["operation_id"]
                and value["entry_id"] == request["entry_id"]
                and _integer(value["file_index"], 0, 999999999) == request["file_index"])
        doc.identifier(value["catalog_id"])
    else:
        _shape(value, common | {"operation"})
        kind = "metadata" if op == "metadata" else "write" if op.startswith("write_") else None
        operation(value["operation"], request["operation_id"], kind)
        if value["operation"]["state"] == "uploading":
            progress = value["operation"]["next_offset"]
            if op == "write_begin":
                require(progress <= request["size_bytes"])
            elif op == "write_chunk":
                require(progress >= request["offset"] + len(decode_chunk(request["data_b64"])))
    return copy.deepcopy(value)


def parse(words):
    words = list(words)
    if words and words[0].casefold() == "torrent":
        words.pop(0)
    if not words:
        words = ["list"]
    op, tail = words[0].casefold(), words[1:]
    result = {"op": op}
    if op == "list" and not tail:
        return result
    if op in ("files", "refresh", "remove") and len(tail) == 1:
        doc.label(tail[0])
        return {**result, "entry": tail[0]}
    if op in ("status", "cancel") and len(tail) == 1:
        return {**result, "operation_id": _uuid(tail[0])}
    if op == "rename" and len(tail) == 2:
        return {**result, "entry": doc.label(tail[0]), "name": doc.label(tail[1])}
    if op == "play" and len(tail) == 2 and re.fullmatch(r"[0-9]{1,9}", tail[1]):
        return {**result, "entry": doc.label(tail[0]), "index": int(tail[1])}
    if op == "inspect" and len(tail) == 1:
        return {**result, "source": doc.source(tail[0])}
    if op == "add":
        if len(tail) >= 2 and tail[0] == "--metadata":
            result["metadata"] = _uuid(tail[1])
            tail = tail[2:]
        elif tail and not tail[0].startswith("--"):
            result["source"] = doc.source(tail.pop(0))
        else:
            raise CatalogError("invalid_arguments")
        while tail:
            flag = tail.pop(0)
            require(flag in ("--name", "--files") and tail, "invalid_arguments")
            key, value = flag[2:], tail.pop(0)
            require(key not in result, "invalid_arguments")
            if key == "name":
                result[key] = doc.label(value)
            else:
                require(re.fullmatch(r"[0-9]{1,9}(?:,[0-9]{1,9}){0,4095}", value) is not None,
                        "invalid_arguments")
                indices = [int(part) for part in value.split(",")]
                require(len(indices) == len(set(indices)), "invalid_arguments")
                result[key] = indices
        require("name" in result, "invalid_arguments")
        return result
    if op in ("export", "import") and tail:
        path = tail.pop(0)
        require(path and not path.startswith("-") and not path.endswith(("/", "\\"))
                and not any(ord(c) < 32 or ord(c) == 127 for c in path), "invalid_arguments")
        result.update(path=path, with_favorites=False)
        if op == "import":
            result.update(mode="merge", dry_run=False)
            if tail and tail[0] in ("merge", "replace"):
                result["mode"] = tail.pop(0)
        seen = set()
        for flag in tail:
            require(flag not in seen and (flag == "--with-favorites" or op == "import" and flag == "--dry-run"),
                    "invalid_arguments")
            seen.add(flag)
            result[flag[2:].replace("-", "_")] = True
        return result
    raise CatalogError("invalid_arguments")


def _private_write(path, value):
    # A maximum-size export must remain importable under the same byte bound.
    raw = doc.canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        directory = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _read_file(path, maximum=doc.MAX_BYTES):
    require(not path.is_symlink(), "invalid_document")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, "invalid_document")
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "file_changed")
    return doc.decode(raw, maximum)


def _entry(catalog, selector):
    require(catalog is not None, "entry_not_found")
    exact = [row for row in catalog["entries"] if row["id"] == selector]
    rows = exact or [row for row in catalog["entries"] if row["title"] == selector]
    if len(rows) > 1:
        error = CatalogError("ambiguous_entry")
        error.candidates = [row["id"] for row in rows]
        raise error
    require(len(rows) == 1, "entry_not_found")
    return rows[0]


def _public_files(files):
    return [{**row, "name": doc.safe_display(row["name"])} for row in files]


def _public_catalog(value):
    if value is None:
        return {"catalog_id": None, "name": None, "entries": []}
    return {"catalog_id": value["id"], "name": doc.safe_display(value["name"]),
            "entries": [{"id": row["id"], "title": doc.safe_display(row["title"]),
                         "category": doc.safe_display(row["category"]), "file_count": len(row["files"])}
                        for row in value["entries"]]}


class Session:
    def __init__(self, api, client, device, timeout, config_path, *, clock=time.monotonic, sleep=time.sleep):
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 1 <= timeout <= 10800, "invalid_arguments")
        self.api, self.client, self.device = api, client, device
        self.clock, self.sleep = clock, sleep
        self.deadline, self.last, self.next_rpc = clock() + timeout, None, 0
        self.server, self.runtime, self.controls = client.server, None, None
        self.config_path, self.operations, self.uncertain = config_path, [], None
        self.catalog_committed = None

    def remaining(self):
        now = self.clock()
        require(math.isfinite(now) and (self.last is None or now >= self.last)
                and self.client.server == self.server, "context_changed")
        self.last = now
        require(now < self.deadline, "deadline_exceeded")
        return self.deadline - now

    def discover(self):
        self.client.timeout = self.remaining()
        raw = self.client.call(self.device, "capabilities", {})
        self.remaining()
        self.controls = self.api.capabilities_metadata(raw)
        marker = raw.get("torrent_catalog")
        require(type(marker) is dict and set(marker) == {"version"}
                and type(marker["version"]) is int and marker["version"] == 1, "unsupported")
        self.runtime = _pattern(self.controls["player"]["runtime"], TOKEN)
        self.next_rpc = self.clock() + RPC_INTERVAL

    def rpc(self, op, **params):
        request = {"op": op, "runtime": self.runtime, **params}
        require(len(doc.canonical({"action": "torrent_catalog", "params": request})) + 128 <= 16384,
                "document_too_large")
        delay = self.next_rpc - self.clock()
        if delay > 0:
            require(self.remaining() > delay, "deadline_exceeded")
            self.sleep(delay)
        self.client.timeout = self.remaining()
        self.next_rpc = self.clock() + RPC_INTERVAL
        try:
            value = self.client.call(self.device, "torrent_catalog", request)
        except (self.api.PlayerRejected, self.api.PlayerUnsupported) as exc:
            validate_response(exc.data, request)
            raise CatalogError("invalid_response") from None
        except Exception:
            raise CatalogError("request_unconfirmed") from None
        self.remaining()
        return validate_response(value, request)

    def intent(self, op, fields):
        identifier = str(uuid.uuid4())
        require(self.config_path is not None, "operation_storage_unavailable")
        target = hashlib.sha256(doc.canonical([self.server, self.device])).hexdigest()[:32]
        parent = Path(self.config_path).expanduser().absolute().parent
        directory = parent / (".torrent-operations-" + target)
        require(not directory.is_symlink(), "operation_storage_unavailable")
        directory.mkdir(mode=0o700, exist_ok=True)
        require(directory.is_dir(), "operation_storage_unavailable")
        require(os.name == "nt" or directory.stat().st_mode & 0o077 == 0, "operation_storage_unavailable")
        _private_write(directory / (identifier + ".json"), {"version": 1, "operation_id": identifier,
            "runtime": self.runtime, "op": op, "request_sha256": hashlib.sha256(doc.canonical(fields)).hexdigest()})
        self.operations.append(identifier)
        self.uncertain = identifier
        print(json.dumps({"torrent_operation": identifier, "op": op}), file=sys.stderr, flush=True)
        return identifier

    def read_snapshot(self, descriptor):
        descriptor = snapshot(descriptor)
        raw, offset = bytearray(), 0
        while offset < descriptor["size_bytes"]:
            response = self.rpc("read_chunk", snapshot_id=descriptor["id"], offset=offset, max_bytes=CHUNK_BYTES)
            part = decode_chunk(response["data_b64"])
            offset += len(part)
            require(offset <= descriptor["size_bytes"] and response["next_offset"] ==
                    (None if offset == descriptor["size_bytes"] else offset))
            raw.extend(part)
        require(hashlib.sha256(raw).hexdigest() == descriptor["sha256"], "hash_mismatch")
        value = doc.decode(bytes(raw))
        return doc.catalog(value) if descriptor["kind"] == "catalog" else doc.metadata(value)

    def read(self):
        descriptor = self.rpc("read")["snapshot"]
        return descriptor, self.read_snapshot(descriptor)

    def metadata(self, source, identifier=None):
        if identifier is None:
            identifier = self.intent("metadata", {"source": source})
            result = self.rpc("metadata", operation_id=identifier, source=source)["operation"]
        else:
            result = self.rpc("status", operation_id=identifier)["operation"]
        require(result["kind"] == "metadata")
        while result["state"] == "pending":
            if self.remaining() <= RPC_INTERVAL + 1:
                return identifier, None
            result = self.rpc("status", operation_id=identifier)["operation"]
            require(result["kind"] == "metadata")
        if result["state"] != "ready":
            self.uncertain = None
            raise CatalogError(result["reason"] or "cancelled")
        value = self.read_snapshot(result["snapshot"])
        require(source is None or value["source"] == source, "source_changed")
        self.uncertain = None
        return identifier, value

    def write(self, before, current, incoming, mode):
        final = doc.merge(current, incoming, mode)
        if final == current:
            return {"status": "unchanged", "catalog_id": final["id"], "revision": before["revision"]}
        raw = doc.canonical(incoming)
        commands = math.ceil(len(raw) / CHUNK_BYTES) + math.ceil(len(doc.canonical(final)) / CHUNK_BYTES) + 3
        require(self.remaining() >= commands * RPC_INTERVAL + 10, "deadline_insufficient")
        fields = {"revision": before["revision"], "mode": mode, "size_bytes": len(raw),
                  "sha256": hashlib.sha256(raw).hexdigest()}
        identifier = self.intent("write_begin", fields)
        result = self.rpc("write_begin", operation_id=identifier, **fields)["operation"]
        require(result["state"] == "uploading" and result["next_offset"] == 0)
        for offset in range(0, len(raw), CHUNK_BYTES):
            chunk = raw[offset:offset + CHUNK_BYTES]
            result = self.rpc("write_chunk", operation_id=identifier, offset=offset,
                              data_b64=base64.b64encode(chunk).decode("ascii"))["operation"]
            require(result["state"] == "uploading" and result["next_offset"] == offset + len(chunk))
        result = self.rpc("write_commit", operation_id=identifier)["operation"]
        if result["state"] in ("failed", "cancelled"):
            self.uncertain = None
            raise CatalogError(result["reason"] or "cancelled")
        require(result["state"] == "committed")
        checked = self.read_snapshot(result["snapshot"])
        require(checked == final, "readback_mismatch")
        self.uncertain = None
        self.catalog_committed = identifier
        return {"status": "committed", "operation_id": identifier, "catalog_id": checked["id"],
                "revision": result["snapshot"]["revision"]}


def _favorite_target(session, commands):
    require("favorites_global" in session.controls and session.config_path is not None,
            "favorites_unsupported")
    commands.protocol.validate_capability(session.controls["favorites_global"])
    delay = max(0, session.next_rpc - session.clock())
    require(session.remaining() > delay, "deadline_exceeded")
    session.sleep(delay)
    budget = min(300, session.remaining())
    target = commands._Target(session.api, session.client, session.config_path, session.device,
                              budget, session.clock, session.sleep, None)
    target.deadline = min(target.deadline, session.deadline)
    target.runtime, target.capabilities = session.runtime, session.controls
    target.peer = commands.remote.RemoteGlobalFavorites(session.client, session.device,
        runtime=session.runtime, save_context=target.durable.save, timeout=budget,
        monotonic=session.clock, sleep=session.sleep, deadline=target.deadline)
    return target


def _favorite_plan(session, commands, incoming, *, writing):
    target = _favorite_target(session, commands)
    exported = target.peer.export()
    session.remaining()
    plan = commands.document.plan_documents(exported.document, [incoming], "merge")
    require(plan["can_commit"], "favorites_title_conflict")
    require(commands.document.supports(plan["document"], exported.description.data["locator_kinds"]),
            "favorites_unsupported")
    if writing:
        require(exported.description.data["write_ready"], "favorites_not_ready")
        target.require_write_ready()
    session.next_rpc = session.clock() + RPC_INTERVAL
    return target, exported, plan


def _favorite_apply(session, commands, incoming):
    # Catalog writes can change the resolver context; obtain a new admission,
    # export and recovery gate after the catalog commit, under the same deadline.
    target, exported, plan = _favorite_plan(session, commands, incoming, writing=True)
    if plan["document"] == exported.document:
        return {"status": "unchanged", "verified": True, "operation_id": None}
    result = target.peer.apply(exported, incoming, "merge")
    # Retain an already returned operation identity even at the deadline;
    # a late receipt is not permission for another request or replay.
    return {"status": result.outcome, "verified": result.verified, "operation_id": result.operation_id}


def _execute(session, args, imported=None, commands=None):
    op = args["op"]
    if op in ("status", "cancel"):
        result = session.rpc(op, operation_id=args["operation_id"])["operation"]
        # Operation descriptors contain no source URL or file-name payload.
        return {"status": result["state"], "operation": result, "replay_allowed": False}
    if op == "inspect":
        identifier, metadata = session.metadata(args["source"])
        return {"status": "pending" if metadata is None else "ready", "operation_id": identifier,
                "files": [] if metadata is None else _public_files(metadata["files"]), "replay_allowed": False}
    before, current = session.read()
    if op == "list":
        return {"status": "ready", **_public_catalog(current)}
    if op == "files":
        selected = _entry(current, args["entry"])
        return {"status": "ready", "entry_id": selected["id"], "files": _public_files(selected["files"])}
    if op == "export":
        value = current
        if args["with_favorites"]:
            require(current is not None, "catalog_missing")
            target = _favorite_target(session, commands)
            favorites = target.peer.export().document
            prefix = "torrent:" + current["id"] + ":"
            favorites["entries"] = [entry for entry in favorites["entries"]
                if entry["origin"]["provider"] == "torrent"
                and entry["locator"].get("kind") == "channel"
                and entry["locator"].get("channelId", "").startswith(prefix)]
            value = doc.bundle({"version": 1, "kind": "ottplay-torrent-bundle",
                                "catalog": current, "favorites": favorites}, commands.document)
        session.remaining()
        _private_write(Path(args["path"]).expanduser(), value)
        return {"status": "exported", "catalog_id": current["id"] if current else None,
                "with_favorites": args["with_favorites"]}
    if op == "play":
        selected = _entry(current, args["entry"])
        require(any(row["index"] == args["index"] for row in selected["files"]), "file_not_found")
        fields = {"revision": before["revision"], "entry_id": selected["id"], "file_index": args["index"]}
        identifier = session.intent("play", fields)
        response = session.rpc("play", operation_id=identifier, **fields)
        require(response["catalog_id"] == current["id"])
        session.uncertain = None
        return {"status": "accepted", "operation_id": identifier, "catalog_id": current["id"],
                "entry_id": selected["id"], "file_index": args["index"], "playback_confirmed": False}
    if op == "import":
        incoming = imported["catalog"] if args["with_favorites"] else imported
        final = doc.merge(current, incoming, args["mode"])
        if args["with_favorites"]:
            _favorite_plan(session, commands, imported["favorites"], writing=not args["dry_run"])
        if args["dry_run"]:
            return {"status": "planned", "changed": final != current, "mode": args["mode"],
                    "entry_count": len(final["entries"]), "with_favorites": args["with_favorites"],
                    "catalog_id": final["id"], "resolution_checked": False}
        result = session.write(before, current, incoming, args["mode"])
        if args["with_favorites"]:
            try:
                favorites = _favorite_apply(session, commands, imported["favorites"])
            except Exception:
                # The first commit remains real even if the second store fails.
                return {"status": "partial", "catalog": result, "favorites": {"status": "unconfirmed"},
                        "replay_allowed": False, "guidance": "Use fav global recovery and saved operation context."}
            return {"status": "committed" if favorites["verified"] else "partial", "catalog": result,
                    "favorites": favorites, "replay_allowed": False}
        return result
    if op in ("add", "refresh"):
        selected = _entry(current, args["entry"]) if op == "refresh" else None
        source = selected["source"] if selected else args.get("source")
        identifier, metadata = session.metadata(source, args.get("metadata"))
        if metadata is None:
            return {"status": "pending", "operation_id": identifier, "replay_allowed": False}
        files = metadata["files"]
        if "files" in args:
            require(set(args["files"]) <= {row["index"] for row in files}, "file_not_found")
            files = [row for row in files if row["index"] in args["files"]]
        require(files, "no_files")
        final = copy.deepcopy(current) if current is not None else {
            "schema": 1, "id": str(uuid.uuid4()), "name": "Torrent", "entries": []}
        if selected:
            for row in final["entries"]:
                if row["id"] == selected["id"]:
                    row["files"] = files
        else:
            final["entries"].append({"id": str(uuid.uuid4()), "title": args["name"],
                "category": args["name"], "source": metadata["source"], "files": files})
        return session.write(before, current, doc.catalog(final, allow_null=False), "replace")
    selected = _entry(current, args["entry"])
    final = copy.deepcopy(current)
    if op == "rename":
        for row in final["entries"]:
            if row["id"] == selected["id"]:
                row["title"] = row["category"] = args["name"]
    elif op == "remove":
        final["entries"] = [row for row in final["entries"] if row["id"] != selected["id"]]
    else:
        raise CatalogError("invalid_arguments")
    return session.write(before, current, final, "replace")


def print_result(value):
    print("Torrent catalog: " + value["status"].replace("_", " ") + ".")
    if "error" in value:
        print(value["error"])
    for row in value.get("entries", []):
        print(f"{row['id']}  {row['title']}  ({row['file_count']} files)")
    for row in value.get("files", []):
        print(f"{row['index']}  {row['name']}  ({row['length']} bytes)")
    for key in ("catalog_id", "entry_id", "operation_id"):
        if value.get(key):
            print(key.replace("_", " ").capitalize() + ": " + value[key])
    if "operation" in value:
        row = value["operation"]
        print(f"Operation: {row['id']} / {row['kind']} / {row['state']}")
    if value.get("candidate_ids"):
        print("Choose an exact entry ID: " + ", ".join(value["candidate_ids"]))
    for key in ("catalog", "favorites"):
        if key in value:
            row = value[key]
            print(key.capitalize() + ": " + row["status"])
            if row.get("operation_id"):
                print(key.capitalize() + " operation: " + row["operation_id"])
    if value.get("status") in ("pending", "unknown", "partial"):
        print("No automatic replay. Use torrent status UUID; cancel UUID only cancels pending work.")
    if value.get("guidance"):
        print(value["guidance"])
    if value.get("playback_confirmed") is False:
        print("Playback dispatch accepted; decoder playback is not confirmed.")


def run(api, client, device, words, timeout, json_output, config_path):
    """Single target entry point; the existing dispatcher owns group expansion."""
    previous = client.timeout
    session = None
    try:
        args = parse(words)
        commands, imported = None, None
        if args.get("with_favorites"):
            commands = api.favorites_module("global_favorites_commands")
        if args["op"] == "import":
            imported = _read_file(Path(args["path"]).expanduser(),
                                  doc.MAX_BUNDLE_BYTES if args["with_favorites"] else doc.MAX_BYTES)
            imported = (doc.bundle(imported, commands.document) if args["with_favorites"]
                        else doc.catalog(imported, allow_null=False))
        if args["op"] == "export":
            path = Path(args["path"]).expanduser()
            require(not os.path.lexists(path) and path.parent.is_dir(), "export_exists_or_invalid_path")
        session = Session(api, client, device, timeout, config_path)
        session.discover()
        value = _execute(session, args, imported, commands)
        code = 2 if value["status"] in ("pending", "unknown", "partial", "failed", "cancelled") else 0
    except KeyboardInterrupt:
        value, code = {"status": "unknown", "error": "Interrupted; no automatic replay."}, 130
    except Exception as error:
        allowed = REASONS | {"invalid_response", "invalid_arguments", "context_changed", "deadline_exceeded",
            "deadline_insufficient", "operation_storage_unavailable", "request_unconfirmed", "readback_mismatch",
            "file_changed", "ambiguous_entry", "favorites_unsupported", "favorites_title_conflict",
            "favorites_not_ready", "catalog_missing", "export_exists_or_invalid_path"}
        reason = error.code if isinstance(error, (CatalogError, doc.DocumentError)) and error.code in allowed else "request_failed"
        unknown = session is not None and session.uncertain is not None
        value = {"status": "unknown" if unknown else "failed", "reason": reason,
                 "error": "Torrent catalog: " + reason.replace("_", " ") + ". No automatic replay.",
                 "replay_allowed": False}
        if isinstance(error, CatalogError) and hasattr(error, "candidates"):
            value["candidate_ids"] = error.candidates
        code = 2 if unknown else 1
    finally:
        client.timeout = previous
    if session is not None and session.operations:
        value["operation_ids"] = list(session.operations)
        if session.uncertain is not None:
            value["operation_id"] = session.uncertain
    if json_output:
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    else:
        print_result(value)
    return code
