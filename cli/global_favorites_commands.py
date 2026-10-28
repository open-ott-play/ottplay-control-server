"""Explicit global Favorites grammar and authenticated host orchestration.

These commands never activate providers or wake players. Registration lives in
the CLI entry point and remains separate from the player capability gate.
"""

import math
from pathlib import Path
import queue
import re
import threading
import time
import uuid

import favorites_commands as shared
import favorites_operations as operations
import global_favorites_document as document
import global_favorites_operations as audit
import global_favorites_protocol as protocol
import global_favorites_recovery as recovery
import global_favorites_remote as remote
import preset_global_favorites as storage

USAGE = ("Use fav global export FILE, import FILE [merge|replace] [--dry-run], "
         "collect PRESET [merge|replace] [--dry-run], load PRESET [merge|replace] [--dry-run], "
         "diff PRESET, clear [--dry-run], describe, status UUIDv4, recovery [list|inspect UUIDv4] or "
         "ack UUIDv4 --dry-run|--confirm PLAN_SHA256")


def parse(api, words):
    if not words:
        return {"op": "describe"}
    operation, tail = words[0].casefold(), list(words[1:])
    if operation in ("recovery", "recover", "ack", "acknowledge"):
        return api.parse_favorites_profile(words)
    result = {"op": operation}
    if operation == "describe" and not tail:
        return result
    if operation == "status" and len(tail) == 1 and protocol.UUID.fullmatch(tail[0]):
        return {**result, "operation_id": tail[0]}
    if operation == "clear" and tail in ([], ["--dry-run"]):
        return {**result, "mode": "replace", "dry_run": bool(tail)}
    if operation not in ("export", "import", "collect", "load", "diff") or not tail:
        raise api.Error(USAGE)
    value = tail.pop(0)
    if operation in ("export", "import"):
        if (not value.strip() or value.startswith("-") or value.endswith(("/", "\\"))
                or Path(value).name in ("", ".", "..") or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise api.Error("Favorites require a regular file path; prefix a filename starting with '-' with './'")
        result["path"] = value
    else:
        if not api.preset_name(value):
            raise api.Error(USAGE)
        result["preset"] = value
    if operation in ("export", "diff"):
        if tail:
            raise api.Error(USAGE)
        return result
    dry_run = bool(tail and tail[-1] == "--dry-run")
    if dry_run:
        tail.pop()
    if len(tail) > 1 or tail and tail[0].casefold() not in ("merge", "replace"):
        raise api.Error(USAGE)
    return {**result, "mode": tail[0].casefold() if tail else "merge", "dry_run": dry_run}


class _Durable(shared._DurableContext):
    def __init__(self, config_path, client, device):
        self.parent = Path(config_path).expanduser().absolute().parent
        prototype = remote.ContextStore(self.parent, server=client.server, device=device)
        self.name = ".favorites-global-operations-" + prototype.target
        self.store = remote.ContextStore(self.parent / self.name, server=client.server, device=device)
        self.admin = audit.AcknowledgementStore(self.parent / self.name, server=client.server, device=device)


class _Target(shared._Target):
    def __init__(self, api, client, config_path, device, timeout, clock, sleep, uuid_factory):
        super().__init__(api, client, config_path, device, timeout, clock, sleep, uuid_factory)
        self.durable = _Durable(config_path, client, device)

    def discover(self, *, recovery_only=False, status_only=False):
        _, data = self.read_capabilities()
        self.capabilities = data
        if status_only:
            # Saved, target-bound evidence permits one strict read even when
            # the player withholds permission for new global mutations.
            self.wait(self.now() + shared.remote.POST_INTERVAL)
            self.peer = remote.StatusReader(self.client, self.device, runtime=self.runtime,
                timeout=self.remaining(), monotonic=self.clock, sleep=self.sleep, deadline=self.deadline)
            return
        if recovery_only:
            self.discover_recovery()
            return
        if "favorites_global" not in data:
            raise shared.CommandError("global_favorites_unsupported")
        protocol.validate_capability(data["favorites_global"])
        self.wait(self.now() + shared.remote.POST_INTERVAL)
        self.peer = remote.RemoteGlobalFavorites(self.client, self.device, runtime=self.runtime,
            save_context=self.durable.save, timeout=self.remaining(), monotonic=self.clock,
            sleep=self.sleep, deadline=self.deadline, uuid_factory=self.uuid_factory)

    def discover_recovery(self):
        if "favorites_global_recovery" not in self.capabilities:
            raise shared.CommandError("global_favorites_recovery_unsupported")
        recovery.validate_recovery_capability(self.capabilities["favorites_global_recovery"])
        self.wait(self.now() + shared.remote.POST_INTERVAL)
        self.recovery = recovery.RecoveryPeer(self.client, self.device, runtime=self.runtime,
            timeout=self.remaining(), monotonic=self.clock, sleep=self.sleep, deadline=self.deadline)

    def require_write_ready(self):
        self.discover_recovery()
        result = self.recovery.list()
        if any(row["acknowledgement"] is None for row in result["records"]):
            raise shared.CommandError("global_favorites_reconciliation_required")
        self.wait(self.now() + shared.remote.POST_INTERVAL)


def _store(api, config_path):
    return storage.Store(config_path, preset_names=api.preset_names, resolve_preset=api.resolve_preset_name)


def _outcome(target, result):
    if isinstance(result, remote.Preview):
        return {"status": "planned" if result.ready else "blocked", "operation_id": result.operation_id,
                "ready": result.ready, "cleanup": result.cleanup, "summary": result.data["counts"],
                "resolution": result.data["resolution_counts"], "replay_allowed": False}
    return {"status": result.outcome, **shared._completion(target, result)}


def _retain_audit(target, inspection, request, evidence):
    completed = queue.Queue(maxsize=1)
    def retain():
        try:
            target.durable.admin.save_evidence(inspection, request, evidence)
            completed.put(True)
        except Exception:  # noqa: BLE001 - local evidence cannot change a player result
            completed.put(False)
    remaining = target.deadline - target.now()
    if remaining <= 0:
        return False
    wall = time.perf_counter() + remaining
    threading.Thread(target=retain, name="ott-global-ack-evidence", daemon=True).start()
    try:
        return completed.get(timeout=max(0, min(remaining, wall - time.perf_counter())))
    except queue.Empty:
        return False


def _recover(target, params):
    op = params["op"]
    if op == "recovery" and params.get("mode", "list") == "list":
        return {"op": op, "status": "recovery_list", "recovery": target.recovery.list(), "replay_allowed": False}
    inspected = target.recovery.inspect(params["operation_id"])
    base = {"op": op, "operation_id": params["operation_id"], "replay_allowed": False}
    if op == "recovery":
        return {**base, "status": "recovery_inspected", "recovery": inspected}
    if params.get("dry_run"):
        return {**base, "status": "planned" if inspected["acknowledge_allowed"] else "blocked", "recovery": inspected}
    if not inspected["acknowledge_allowed"]:
        record = inspected["record"]
        acknowledged = record.get("acknowledgement") if record else None
        try:
            if acknowledged is None:
                raise ValueError
            original, request = target.durable.admin.load_intent(
                acknowledged["acknowledgement_id"], params["operation_id"], params.get("confirm"))
            if record != {**original["record"], "acknowledgement": recovery.recovery_audit(request)}:
                raise ValueError
        except Exception:  # noqa: BLE001 - absent or mismatched intent cannot authorize acknowledgement
            return {**base, "status": "blocked", "reason": "acknowledgement_unavailable", "recovery": inspected}
        result = {**base, "status": "acknowledgement_observed", "acknowledgement_id": request["acknowledgement_id"],
                  "plan_sha256": request["plan_sha256"], "original_outcome": record["original_outcome"], "evidence": inspected}
        inspected = original
    else:
        if params.get("confirm") != inspected["plan_sha256"]:
            return {**base, "status": "blocked", "reason": "confirmation_changed"}
        request = recovery.recovery_ack_request(inspected)
        result = target.recovery.acknowledge(inspected, params["confirm"], target.durable.save_acknowledgement)
    warnings = []
    if result.get("evidence") is not None and not _retain_audit(target, inspected, request, result["evidence"]):
        warnings.append("local_acknowledgement_evidence_unconfirmed")
    return {**base, **result, "warnings": warnings}


def run(api, client, config, config_path, targets, params, timeout, *, monotonic=time.monotonic,
        sleep=time.sleep, uuid_factory=uuid.uuid4):
    """Resolve all targets before dispatch; collection makes one local CAS."""
    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
            raise shared.CommandError("invalid_arguments")
        unique, seen = [], set()
        for label, device in targets:
            if type(device) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", device):
                raise shared.CommandError("invalid_targets")
            if device not in seen:
                unique.append((label, device))
                seen.add(device)
        if not 1 <= len(unique) <= 64 or len(unique) != 1 and params["op"] != "collect":
            raise shared.CommandError("invalid_targets")
        return _run(api, client, config_path, unique, params, timeout, monotonic, sleep, uuid_factory)
    except api.Error:
        raise
    except (shared.CommandError, remote.RemoteError, operations.OperationStoreError,
            storage.StorageError, document.DocumentError, protocol.ProtocolError) as error:
        raise api.Error(str(error)) from None
    except Exception:  # noqa: BLE001 - dependency exceptions may contain private payloads
        raise api.Error("Global Favorites could not be verified; inspect status before another change. No request was replayed") from None


def _run(api, client, config_path, targets, params, timeout, clock, sleep, uuid_factory):
    op = params["op"]
    store = _store(api, config_path) if op in ("collect", "load", "diff") else None
    saved = store.snapshot(params["preset"]) if store else None
    incoming = None
    if op in ("load", "diff"):
        if saved["document"] is None:
            raise shared.CommandError("global_favorites_missing")
        incoming = saved["document"]
    elif op == "import":
        incoming = document.validate_document(api._read_favorites_document(params["path"]))
    elif op == "clear":
        incoming = {"version": 1, "entries": []}
    if op == "export":
        destination = Path(params["path"]).expanduser()
        if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
            raise shared.CommandError("output_unavailable")
    collected = []
    for _, device in targets:
        target = _Target(api, client, config_path, device, timeout, clock, sleep, uuid_factory)
        # Missing evidence fails locally before creating a capability RPC.
        record = target.durable.store.load(params["operation_id"]) if op == "status" else None
        target.discover(recovery_only=op in ("recovery", "acknowledge"), status_only=op == "status")
        if op in ("recovery", "acknowledge"):
            return _recover(target, params)
        if op == "describe":
            return {"op": op, "status": "available", "description": target.peer.describe().data}
        if op == "status":
            return {"op": op, **_outcome(target, target.peer.reconcile(record))}
        snapshot = target.peer.export()
        if op == "collect":
            collected.append(snapshot.document)
            continue
        if op == "export":
            api.write_private(destination, snapshot.document, exclusive=True, compact=True)
            return {"op": op, "status": "exported", "entries": len(snapshot.document["entries"]),
                    "document_sha256": snapshot.description.data["export"]["document_sha256"]}
        if op == "diff":
            plan = document.plan_documents(snapshot.document, [incoming], "replace")
            same = document.canonical_bytes(snapshot.document) == document.canonical_bytes(plan["document"])
            return {"op": op, "status": "matched" if same else "different", "summary": plan["summary"]}
        if params.get("dry_run"):
            result = target.peer.preview(snapshot, incoming, params["mode"])
        else:
            target.require_write_ready()
            result = target.peer.apply(snapshot, incoming, params["mode"])
        return {"op": op, **_outcome(target, result)}
    plan = document.plan_documents(saved["document"], collected, params["mode"])
    if not plan["can_commit"] or params.get("dry_run"):
        return {"op": op, "status": "planned" if plan["can_commit"] else "blocked",
                "summary": plan["summary"], "players": len(targets)}
    update = store.plan(saved["preset"], plan["document"], expected_revision=saved["config_revision"])
    applied = store.apply(update)
    return {"op": op, "status": "collected" if applied["changed"] else "unchanged",
            "players": len(targets), "summary": plan["summary"], **applied}


def offline(api, config_path, params):
    """Manage only the selected global companion; preserve all other settings."""
    try:
        store = _store(api, config_path)
        saved = store.snapshot(params["preset"])
        op = params["op"]
        result = {"op": op, "preset": saved["preset"]}
        if op in ("validate", "export"):
            if saved["document"] is None:
                raise shared.CommandError("global_favorites_missing")
            value = document.validate_document(saved["document"])
            result["entries"] = len(value["entries"])
            if op == "validate":
                return {**result, "status": "valid"}
            try:
                api.write_private(params["path"], value, exclusive=True, compact=True)
            except FileExistsError:
                raise api.Error("Export file already exists; choose a new path. The existing file was not changed") from None
            except OSError:
                raise api.Error("Could not create the global favorites export file") from None
            return {**result, "status": "exported", "path": str(Path(params["path"]).expanduser())}
        if op not in ("import", "diff", "restore"):
            raise shared.CommandError("invalid_arguments")
        if op == "restore":
            backup = store.backup_document(saved["preset"], params["path"])
            incoming = backup["document"]
            result["backup_revision"] = backup["backup_revision"]
        else:
            incoming = api._read_favorites_document(params["path"])
        mode = "replace" if op == "restore" else params.get("mode", "replace")
        plan = document.plan_documents(saved["document"], [incoming], mode)
        result["summary"] = plan["summary"]
        if not plan["can_commit"]:
            return {**result, "status": "blocked"}
        update = store.plan(saved["preset"], plan["document"], expected_revision=saved["config_revision"])
        if op == "diff":
            return {**result, "status": "different" if update.changed else "matched"}
        if params.get("dry_run"):
            return {**result, "status": "planned"}
        applied = store.apply(update)
        status = ("restored" if op == "restore" else "imported") if applied["changed"] else "unchanged"
        return {**result, **applied, "status": status}
    except api.Error:
        raise
    except storage.StorageError as error:
        if error.replacement_occurred:
            raise api.Error("Global favorites configuration replacement is uncertain; inspect the retained backup before another change") from None
        raise api.Error(str(error)) from None
    except (shared.CommandError, document.DocumentError) as error:
        raise api.Error(str(error)) from None
    except Exception:  # noqa: BLE001 - never reveal document payloads or filesystem causes
        raise api.Error("Could not manage the saved global favorites document") from None


def print_result(result):
    print("Global Favorites: " + result["status"].replace("_", " ") + ".")
    if "operation_id" in result and result["operation_id"] is not None:
        print("Operation: " + result["operation_id"])
    if "summary" in result:
        print("Changes: " + ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in result["summary"].items()))
    if "entries" in result:
        print("Entries: " + str(result["entries"]))
    if "description" in result:
        value = result["description"]
        print("Writes: " + ("ready" if value["write_ready"] else "blocked: " + value["write_blocker"].replace("_", " ")) + ".")
        print("Locator kinds: " + ", ".join(value["locator_kinds"]))
        if value.get("export") is not None:
            print("Export entries: " + str(value["export"]["entries"]))
    if "recovery" in result:
        value = result["recovery"]
        rows = value["records"] if "records" in value else ([value["record"]] if value["record"] is not None else [])
        print("Uncertainty records: " + str(len(rows)))
        for row in rows:
            acknowledgement = "acknowledged" if row["acknowledgement"] is not None else "not acknowledged"
            print("  " + row["operation_id"] + " — " + row["original_outcome"] + "; " + acknowledgement)
        if value.get("acknowledge_allowed"):
            print("Confirmation plan: " + value["plan_sha256"])
            print("Use fav global ack " + value["operation_id"] + " --confirm " + value["plan_sha256"])
    if result.get("status") in ("acknowledgement_confirmed", "acknowledgement_observed"):
        print("Administrative acknowledgement only; the original transfer remains uncertain and was not replayed.")
    if "resolution" in result:
        print("Resolution: " + ", ".join(f"{key} {value}" for key, value in result["resolution"].items()))
    if "cleanup" in result:
        print("Preview cleanup: " + result["cleanup"].replace("_", " ") + ".")
    for warning in result.get("warnings", []):
        print("Warning: " + warning.replace("_", " ") + ".")
    if "path" in result:
        print("File: " + result["path"])
    if "reason" in result:
        print("Result: " + result["reason"].replace("_", " ") + ".")
    if result.get("status") in ("unknown", "not_applied", "blocked"):
        print("No automatic replay was attempted.")
