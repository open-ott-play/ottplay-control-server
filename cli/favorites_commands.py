"""Current-Plex host orchestration with explicit, revision-bound pairing.

The caller supplies the already loaded config, authenticated Client and resolved
device IDs. This module returns structured outcomes, performs no UI work and has
no fallback to legacy favorites commands or another provider/source.
"""
import copy
import json
import math
import os
import queue
import re
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import favorites_document as document
import favorites_operations as operations
import favorites_protocol as protocol
import favorites_remote as remote
import preset_favorites as storage


class CommandError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Favorites synchronization: " + code.replace("_", " ") + ". No automatic replay is permitted.")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _bundle(scope):
    return {"version": 1, "scopes": [scope]}


def _scope_key(scope):
    return tuple(scope[key] for key in ("provider", "profileBindingId", "sourceBindingId"))


def _admit(value, target, limits):
    """Match actual player JSON accounting: values only, root at depth one."""
    checked = document.validate_scope_document(value)
    if checked["scope"]["provider"] != "plex" or len(_json(checked)) > limits["max_document_bytes"]:
        raise CommandError("unsupported_document")
    pending, nodes = [(checked, 1)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > limits["max_json_nodes"] or depth > limits["max_json_depth"]:
            raise CommandError("document_limits_exceeded")
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
    lists = checked["channelLists"]["lists"]
    if len(lists) > limits["max_channel_lists"] or len(checked["mediaFavorites"]) > limits["max_media_favorites"]:
        raise CommandError("document_limits_exceeded")
    references = list(checked["mediaFavorites"])
    for row in lists:
        for key in ("entries", "shuffleBefore"):
            if len(row.get(key, [])) > limits["max_entries_per_list"]:
                raise CommandError("document_limits_exceeded")
            references.extend(row.get(key, []))
    for reference in references:
        if reference["state"] == "located":
            locator = reference["locator"]
            if locator["kind"] != "plex-media" or locator["serverId"] != target["serverId"]:
                raise CommandError("plex_source_identity_mismatch")
    return checked


class _DurableContext:
    def __init__(self, config_path, client, device):
        self.parent = Path(config_path).expanduser().absolute().parent
        prototype = operations.ContextStore(self.parent, server=client.server, device=device,
                                             validate_context=remote.validate_recovery_record)
        self.name = ".favorites-operations-" + prototype.target
        self.store = operations.ContextStore(self.parent / self.name, server=client.server, device=device,
                                             validate_context=remote.validate_recovery_record)

    def save(self, record):
        # Creation is deferred until RemoteFavorites has a ready preview and
        # is about to authorize commit. Read-only/preview paths never call this.
        with storage._Parent(self.parent) as parent:
            parent.assert_current()
            try:
                if parent.fd is None:
                    os.mkdir(self.parent / self.name, 0o700)
                else:
                    os.mkdir(self.name, 0o700, dir_fd=parent.fd)
            except FileExistsError:
                pass
            parent.sync()
            with storage._Parent(self.parent / self.name) as child:
                child.assert_current()
                child.sync()
            parent.assert_current()
        self.store.save(record)
        return True


class _Target:
    def __init__(self, api, client, config_path, device, timeout, clock, sleep, uuid_factory, deadline=None):
        self.api, self.client, self.device = api, client, device
        self.server = client.server
        self.clock, self.sleep = clock, sleep
        self.last_clock = None
        started = self.now()
        self.deadline = min(started + timeout, deadline) if deadline is not None else started + timeout
        self.durable = _DurableContext(config_path, client, device)
        self.uuid_factory = uuid_factory
        self.peer = self.capability = self.catalog = self.target = None
        self.runtime = None

    def now(self):
        value = self.clock()
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or self.last_clock is not None and value < self.last_clock):
            raise CommandError("invalid_clock")
        self.last_clock = value
        return value

    def remaining(self):
        if self.client.server != self.server:
            raise CommandError("controller_authority_changed")
        value = self.deadline - self.now()
        if value <= 0:
            raise CommandError("deadline_exceeded")
        return value

    def wait(self, until):
        if until >= self.deadline:
            raise CommandError("deadline_exceeded")
        while self.now() < until:
            before = self.now()
            self.sleep(min(5, until - before))
            if self.now() <= before:
                raise CommandError("invalid_clock")

    def discover(self, *, status_recovery=False):
        query = "/api/requests?" + urllib.parse.urlencode({"device_id": self.device})
        self.wait(self.now() + remote.POST_INTERVAL)
        status, queued = self.client.api(query, {"action": "capabilities", "params": {}}, timeout=min(10, self.remaining()))
        self.remaining()
        if (status != 202 or type(queued) is not dict or set(queued) != {"id", "status"}
                or queued.get("status") != "queued" or type(queued.get("id")) is not str
                or protocol.REQUEST_ID.fullmatch(queued["id"]) is None):
            raise CommandError("invalid_capability_receipt")
        request_id, delay = queued["id"], 1.5
        while True:
            # Discovery is also paced; one capabilities POST, bounded GETs.
            self.wait(self.now() + delay)
            try:
                status, raw = self.client.api_raw(query + "&id=" + request_id, timeout=min(10, self.remaining()))
            except Exception as error:  # noqa: BLE001 - only safe HTTP codes are inspected
                if getattr(error, "code", None) in (429, 502, 503, 504):
                    delay = min(8, delay * 2)
                    continue
                raise CommandError("capability_transport_failed") from None
            self.remaining()
            if type(raw) is not bytes or not 0 < len(raw) <= protocol.MAX_RESPONSE_BYTES:
                raise CommandError("invalid_capability_receipt")
            body = self.api.decode_response_json(raw.decode("utf-8"))
            if status == 202 and body == {"status": "pending"}:
                delay = 1.5
                continue
            if (status != 200 or type(body) is not dict or set(body) != {"id", "status", "data"}
                    or body["id"] != request_id or body["status"] != "ok"):
                raise CommandError("capability_unavailable")
            controls = self.api.capabilities_metadata(body["data"])
            self.runtime = controls["player"]["runtime"]
            advertised = body["data"].get("favorites_transfer")
            if advertised is None:
                if status_recovery:
                    return False
                raise CommandError("unsupported_player_or_controller")
            self.capability = protocol.validate_capability(advertised)
            break
        # Keep spacing across the discovery/transfer boundary, without resetting
        # the original per-player deadline or importing another client/config.
        self.wait(self.now() + remote.POST_INTERVAL)
        limits = self.capability["limits"]
        self.peer = remote.RemoteFavorites(self.client, self.device, runtime=controls["player"]["runtime"],
            limits=remote.Limits(**{key: limits[key] for key in remote.Limits.__dataclass_fields__}),
            receipt_retention_seconds=self.capability["receipt_retention_seconds"],
            scope_validator=lambda value: _admit(value, self.target, limits), save_context=self.durable.save,
            timeout=self.remaining(), monotonic=self.clock, sleep=self.sleep, uuid_factory=self.uuid_factory,
            max_metadata_bytes=limits["max_metadata_bytes"], deadline=self.deadline,
            admission_ttl_ms=self.capability["admission"]["admission_ttl_ms"],
            max_budget_ms=self.capability["admission"]["max_budget_ms"])
        try:
            self.catalog = self.peer.catalog()
        except remote.RemoteError:
            if status_recovery:
                return False
            raise
        self.target = self.catalog.data["targets"][0]
        return True

    def observe_status(self, record):
        # Fresh generic metadata and the target-bound local record are required,
        # but no missing mutation capability or catalog is fabricated here.
        self.wait(self.now() + remote.POST_INTERVAL)
        bounds = {} if self.capability is None else {
            "envelope_limit": self.capability["limits"]["max_envelope_bytes"],
            "response_limit": self.capability["limits"]["max_snapshot_response_bytes"],
            "max_metadata_bytes": self.capability["limits"]["max_metadata_bytes"],
        }
        reader = remote.StatusReader(self.client, self.device, runtime=self.runtime,
            timeout=self.remaining(), monotonic=self.clock, sleep=self.sleep, deadline=self.deadline, **bounds)
        return reader.observe(record)

    def select(self, selector="current", target_id=None):
        if target_id is not None and target_id != self.target["target_id"]:
            raise CommandError("target_not_current")
        rows = self.catalog.data["bindings"]
        if selector != "current":
            rows = [row for row in rows if row["scope"] == selector]
        if len(rows) != 1:
            raise CommandError("scope_not_uniquely_bound")
        return copy.deepcopy(rows[0]["scope"])

    def snapshot(self, scope):
        snapshot = self.peer.snapshot(scope)
        if snapshot.data["binding_revision"] != self.catalog.data["binding_revision"]:
            raise CommandError("binding_changed")
        return snapshot


def _store(api, config_path):
    return storage.Store(config_path, preset_names=api.preset_names, resolve_preset=api.resolve_preset_name,
                         validate_document=document.validate_document)


def _saved_scope(saved, scope):
    selected = [value for value in saved["scopes"] if _scope_key(value["scope"]) == _scope_key(scope)]
    if len(selected) != 1:
        raise CommandError("saved_scope_not_uniquely_bound")
    return selected[0]


def _source_servers(value):
    references = list(value["mediaFavorites"])
    for row in value["channelLists"]["lists"]:
        references.extend(row["entries"])
        references.extend(row.get("shuffleBefore", []))
    return {row["locator"]["serverId"] for row in references
            if row["state"] == "located" and row["locator"]["kind"] == "plex-media"}


def _bind(target, saved, params):
    catalog = target.catalog.data
    target_id = target.target["target_id"]
    if params.get("target_id", target_id) != target_id:
        raise CommandError("target_not_current")
    bindings = catalog["bindings"]
    if len(bindings) > 1 and (params.get("new") or params.get("scope", "current") == "current"):
        raise CommandError("scope_not_uniquely_bound")
    prior = bindings[0]["scope"] if len(bindings) == 1 else None
    scope = None
    if params.get("new"):
        if params.get("replace_current"):
            raise CommandError("invalid_arguments")
        if prior is not None:
            return {"op": "bind", "status": "unchanged", "scope": prior, "target_id": target_id}
        mode = "create"
    else:
        candidates = [value for value in saved["scopes"] if value["scope"]["provider"] == "plex"]
        selector = params.get("scope", "current")
        if selector != "current":
            candidates = [value for value in candidates if value["scope"] == selector]
        elif prior is not None and any(value["scope"] == prior for value in candidates):
            candidates = [value for value in candidates if value["scope"] == prior]
        else:
            candidates = [value for value in candidates if _source_servers(value) == {target.target["serverId"]}]
        if len(candidates) != 1:
            raise CommandError("saved_scope_not_uniquely_bound")
        incoming = _admit(candidates[0], target.target, target.capability["limits"])
        scope = incoming["scope"]
        if any(row["scope"] == scope for row in bindings):
            return {"op": "bind", "status": "unchanged", "scope": scope, "target_id": target_id}
        if len(bindings) > 1:
            raise CommandError("scope_not_uniquely_bound")
        if _source_servers(incoming) != {target.target["serverId"]}:
            raise CommandError("saved_source_identity_unavailable")
        if prior is not None and not params.get("replace_current"):
            raise CommandError("different_pair_requires_replace_current")
        mode = "replace-current" if prior is not None else "import"
    request = {"op": "provision", "runtime": target.peer.runtime, "mode": mode,
               "target_id": target_id, "codecVersion": 1,
               "expected_binding_revision": catalog["binding_revision"]}
    if scope is not None:
        request["scope"] = scope
    if mode == "replace-current":
        request["prior_scope"] = prior
    protocol.validate_request(request)
    if params.get("dry_run", False):
        return {"op": "bind", "status": "planned", "mode": mode, "scope": scope, "target_id": target_id}
    try:
        response = target.peer.provision(target.catalog, mode, target_id, scope=scope,
                                         prior_scope=prior if mode == "replace-current" else None)
        return {"op": "bind", "status": "paired", "scope": response.data["scope"],
                "pairing": response.data, "replay_allowed": False}
    except remote.RemoteError as error:
        # A fresh observation cannot prove which request caused the pairing.
        # Never replay create/import/rebind after a missing acknowledgement.
        result = {"op": "bind", "status": "unknown", "scope": scope,
                  "request_id": error.request_id, "replay_allowed": False}
        try:
            observed = target.peer.catalog().data
            rows = observed["bindings"]
            if (observed["targets"][0] == target.target and len(rows) == 1
                    and (scope is None or rows[0]["scope"] == scope)):
                result["observed_pair"] = {"scope": rows[0]["scope"], "target_id": target_id,
                                           "binding_revision": observed["binding_revision"]}
        except remote.RemoteError:
            pass
        return result


def _completion(target, outcome):
    warnings = []
    if outcome.verified:
        try:
            remaining = target.deadline - target.now()
            if remaining <= 0:
                raise CommandError("deadline_exceeded")
            completed = queue.Queue(maxsize=1)
            def retain():
                try:
                    target.durable.store.mark_completed(outcome.operation_id, outcome.result)
                    completed.put(True)
                except Exception:  # noqa: BLE001 - late worker only changes local proof
                    completed.put(False)
            wall_deadline = time.perf_counter() + remaining
            threading.Thread(target=retain, name="ott-favorites-retention", daemon=True).start()
            while True:
                remaining = min(target.deadline - target.now(), wall_deadline - time.perf_counter())
                if remaining <= 0:
                    raise CommandError("deadline_exceeded")
                try:
                    confirmed = completed.get(timeout=min(5, remaining))
                    break
                except queue.Empty:
                    continue
            if not confirmed or target.now() >= target.deadline or time.perf_counter() >= wall_deadline:
                raise CommandError("retention_unconfirmed")
        except Exception:  # noqa: BLE001 - a local bookkeeping failure cannot undo player proof
            # The player proof remains verified even if local retention cannot
            # be updated (including deliberately protected restart evidence).
            warnings.append("local_retention_unconfirmed")
    return {**outcome.summary(), "warnings": warnings, "replay_allowed": False}


def run(api, client, config, config_path, targets, params, timeout, *, monotonic=time.monotonic,
        sleep=time.sleep, uuid_factory=uuid.uuid4):
    """Return structured results using one supplied authority.

    The config snapshot supplied by main remains authoritative for Client and
    target resolution. The favorites Store independently rereads its bytes for
    CAS; it never reloads credentials or changes the supplied Client.
    """
    try:
        return _run(api, client, config, config_path, targets, params, timeout, monotonic, sleep, uuid_factory)
    except CommandError as error:
        raise api.Error(str(error)) from None
    except Exception:  # noqa: BLE001 - host dependencies may carry private configuration in errors
        raise api.Error("Favorites synchronization could not be verified; no request was automatically repeated") from None


def _run(api, client, config, config_path, targets, params, timeout, clock, sleep, uuid_factory):
    if (type(config) is not dict or type(params) is not dict or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 1 <= timeout <= 300):
        raise CommandError("invalid_arguments")
    started = clock()
    if type(started) not in (int, float) or not math.isfinite(started) or started < 0:
        raise CommandError("invalid_clock")
    unique = list(dict.fromkeys(tuple(row) for row in targets))
    devices, resolved = set(), []
    for label, device in unique:
        if (type(label) is not str or type(device) is not str
                or re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", device) is None):
            raise CommandError("invalid_targets")
        if device not in devices:
            devices.add(device)
            resolved.append((label, device))
    if not 1 <= len(resolved) <= 64:
        raise CommandError("invalid_targets")
    op = params.get("op")
    if op not in ("bindings", "bind", "status", "collect", "load", "diff", "clear"):
        raise CommandError("unsupported_operation")
    if op == "status":
        protocol.validate_request({"op": "status", "runtime": "validation", "operation_id": params.get("operation_id")})
    if params.get("scope", "current") != "current":
        protocol.validate_request({"op": "snapshot", "runtime": "validation", "scope": params["scope"]})
    saved = None
    if op in ("load", "diff") or op == "bind" and not params.get("new"):
        local = _store(api, config_path).snapshot(params["preset"])
        saved = local["document"]
        if saved is None or not saved["scopes"]:
            raise CommandError("saved_favorites_missing")
    sources = []
    def make(device, budget, deadline=None):
        return _Target(api, client, config_path, device, budget, clock, sleep, uuid_factory, deadline)
    if op == "collect":
        def export(device, budget):
            target = make(device, budget)
            target.discover()
            scope = target.select(params.get("scope", "current"))
            snapshot = target.snapshot(scope)
            sources.append({"scope": scope, "coverage": "current-source"})
            return {"complete": True, "document": _bundle(json.loads(snapshot.document_bytes))}
        result = api.collect_favorites_profile(config_path, params["preset"], resolved, timeout,
            mode=params.get("mode", "merge"), dry_run=params.get("dry_run", False),
            validate_document=document.validate_document, planner=document.plan_documents,
            store_factory=storage.Store, export_snapshot=export, storage_errors=(storage.StorageError,))
        return {**result, "sources": sources}
    if len(resolved) != 1:
        raise CommandError("one_target_required")
    elapsed = clock() - started
    if elapsed < 0 or elapsed >= timeout:
        raise CommandError("deadline_exceeded")
    target = make(resolved[0][1], timeout - elapsed, started + timeout)
    record = None
    if op == "status":
        try:
            record = target.durable.store.load(params["operation_id"])
        except operations.OperationStoreError:
            return {"op": op, "status": "unknown", "operation_id": params["operation_id"],
                    "verified": False, "reason": "local_context_unavailable", "replay_allowed": False}
    qualified = target.discover(status_recovery=op == "status")
    if op == "status" and not qualified:
        outcome = target.observe_status(record)
        return {"op": op, "status": "unknown", **outcome.summary(), "warnings": [], "replay_allowed": False}
    if op == "bindings":
        return {"op": op, "status": "available", "catalog": target.catalog.data}
    if op == "bind":
        return _bind(target, saved, params)
    if op == "status":
        try:
            target.select(record["begin_request"]["scope"])
        except CommandError:
            outcome = target.observe_status(record)
            return {"op": op, "status": "unknown", **outcome.summary(), "warnings": [], "replay_allowed": False}
        outcome = target.peer.reconcile(record)
        return {"op": op, "status": outcome.outcome, **_completion(target, outcome)}
    scope = target.select(params.get("scope", "current"), params.get("target_id"))
    if saved is not None:
        incoming = _admit(_saved_scope(saved, scope), target.target, target.capability["limits"])
    snapshot = target.snapshot(scope)
    current = json.loads(snapshot.document_bytes)
    if op == "clear":
        incoming = document.clear_scope(current)
    mode = params.get("mode", "merge") if op == "load" else "replace"
    planned = document.plan_documents(_bundle(current), [_bundle(incoming)], mode)
    summary = planned["summary"]
    if summary["conflicts"]["unresolved"]:
        return {"op": op, "status": "blocked", "scope": scope, "summary": summary}
    desired = _admit(planned["document"]["scopes"][0], target.target, target.capability["limits"])
    if op == "diff":
        return {"op": op, "status": "matched" if current == desired else "different", "scope": scope, "summary": summary}
    if params.get("dry_run", False):
        preview = target.peer.preview(snapshot, _json(desired))
        status = "planned" if preview.ready is True else "blocked" if preview.ready is False else "failed"
        return {"op": op, "status": status, "scope": scope, "summary": summary,
                **preview.summary(), "preview": preview.data}
    if current == desired:
        return {"op": op, "status": "unchanged", "scope": scope, "summary": summary}
    outcome = target.peer.replace(snapshot, _json(desired))
    return {"op": op, "status": outcome.outcome, "scope": scope, "summary": summary, **_completion(target, outcome)}
