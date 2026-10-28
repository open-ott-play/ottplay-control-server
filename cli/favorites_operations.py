"""Private intent evidence and bounded retention of verified completions.

Saving never proves player application. Only an exact same-runtime receipt,
revalidated against saved begin/preview, permits completion and later reclamation.
Cross-runtime, pending, partial and unknown records remain protected. Completed
records stay until capacity requires removing the oldest: recent records are
preferred, without a minimum that blocks reuse under byte pressure. Missing or
reclaimed evidence never permits replay. Noncooperating editors must not modify
these private files concurrently with retention operations. Admission counts at
most 32 records and 8 interrupted/in-flight completion files, even if empty.
"""

import copy
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

import favorites_protocol as protocol
import favorites_remote as remote
import preset_favorites as private_files

MAX_RECORD_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_RECORDS = 32
MAX_TEMPORARY_FILES = 8
PREFIX = "favorites-operation-"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
RECORD = re.compile(PREFIX + r"[0-9a-f-]{36}\.json\Z")
TEMPORARY = re.compile(r"\.favorites-completion-[0-9a-f]{32}\.tmp\Z")


class OperationStoreError(ValueError):
    """Fixed text; never include credentials, rejected documents or upstream errors."""

    def __init__(self, code):
        self.code = code
        super().__init__({
            "invalid_context": "Invalid favorites operation context; commit was not authorized.",
            "invalid_completion": "Favorites completion does not match the retained operation evidence.",
            "wrong_target": "Favorites context belongs to another server or player.",
            "conflict": "This favorites operation already has different retained evidence.",
            "capacity": "Favorites evidence storage is full; reconcile retained operations before another change.",
            "unavailable": "Private favorites evidence could not be saved and verified; do not send commit.",
            "retention_uncertain": "Favorites evidence retention is uncertain; inspect retained records. Never replay a player operation.",
            "missing": "Favorites evidence is missing; the player outcome is unknown. Do not replay the operation.",
        }[code])


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


class ContextStore:
    """Use an existing private directory and the same file guards as preset CAS.

    The supplied validator must validate the full begin/preview receipt context
    and return its unchanged JSON representation. It confers no replay authority.
    The target is separately bound to the controller endpoint and exact device ID.
    """

    def __init__(self, directory, *, server, device, validate_context):
        if (type(server) is not str or not server or len(server) > 2048
                or type(device) is not str or not device or len(device) > 256
                or not callable(validate_context)):
            raise OperationStoreError("invalid_context")
        self.directory = Path(directory).expanduser().absolute()
        self.target = hashlib.sha256(_encoded({"server": server.rstrip("/"), "device": device})).hexdigest()
        self.validate_context = validate_context

    def _context(self, value):
        try:
            if type(value) is not dict or UUID.fullmatch(value.get("operation_id", "")) is None:
                raise ValueError
            raw = _encoded(value)
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError
            validated = self.validate_context(copy.deepcopy(value))
            if type(validated) is not dict or validated != value:
                raise ValueError
            return copy.deepcopy(validated)
        except Exception:  # noqa: BLE001 - callback errors may contain private material
            raise OperationStoreError("invalid_context") from None

    def _name(self, operation_id):
        if type(operation_id) is not str or UUID.fullmatch(operation_id) is None:
            raise OperationStoreError("invalid_context")
        return PREFIX + operation_id + ".json"

    def _bytes(self, parent, name):
        descriptor = parent.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not private_files._private_regular(metadata):
                raise OperationStoreError("unavailable")
            if metadata.st_size > MAX_RECORD_BYTES:
                raise OperationStoreError("invalid_context")
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            raise OperationStoreError("invalid_context")
        return raw, (metadata.st_dev, metadata.st_ino)

    def _completion(self, context, completion):
        """Revalidate stored proof whenever it could authorize reclamation."""
        try:
            if (type(completion) is not dict or set(completion) != {"version", "recorded_ns", "request", "response"}
                    or type(completion["version"]) is not int or completion["version"] != 1
                    or type(completion["recorded_ns"]) is not int or not 0 <= completion["recorded_ns"] < 2**63):
                raise ValueError
            retained = remote.restore_context(context)
            result = protocol.validate_response(completion["request"], completion["response"],
                                                request_id=completion["response"]["id"], context=retained)
            if (not result.verified or result.request["op"] not in ("commit", "status")
                    or result.data["operation_id"] != context["operation_id"]):
                raise ValueError
            return result.data["receipt"]
        except Exception:  # noqa: BLE001 - protocol/callback text may contain private data
            raise OperationStoreError("invalid_completion") from None

    def _read(self, parent, name):
        raw, identity = self._bytes(parent, name)
        value = private_files._decode(raw)
        if (not {"version", "target_sha256", "context"} <= set(value)
                or set(value) - {"version", "target_sha256", "context", "completion"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise OperationStoreError("invalid_context")
        if value["target_sha256"] != self.target:
            raise OperationStoreError("wrong_target")
        context = self._context(value["context"])
        if name != self._name(context["operation_id"]):
            raise OperationStoreError("invalid_context")
        if "completion" in value:
            self._completion(context, value["completion"])
        return raw, context, value, identity

    def _inventory(self, parent, additional, delta, protected=(), reserve_temporary=0):
        """Plan the smallest oldest-completed eviction set, without writing."""
        names, temporary_bytes, temporary_count = [], 0, reserve_temporary
        if temporary_count > MAX_TEMPORARY_FILES:
            raise OperationStoreError("capacity")
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if RECORD.fullmatch(entry.name):
                    names.append(entry.name)
                    if len(names) > MAX_RECORDS:
                        raise OperationStoreError("capacity")
                elif TEMPORARY.fullmatch(entry.name):
                    temporary_count += 1
                    if temporary_count > MAX_TEMPORARY_FILES:
                        raise OperationStoreError("capacity")
                    metadata = entry.stat(follow_symlinks=False)
                    if not private_files._private_regular(metadata):
                        raise OperationStoreError("unavailable")
                    # Interrupted private staging files are counted but never
                    # assumed discardable or used as completion authority.
                    temporary_bytes += metadata.st_size
                    if temporary_bytes > MAX_TOTAL_BYTES:
                        raise OperationStoreError("capacity")
        total, candidates = temporary_bytes, []
        for name in names:
            metadata = (self.directory / name).lstat()
            if not private_files._private_regular(metadata):
                raise OperationStoreError("unavailable")
            total += metadata.st_size
            if name in protected:
                continue
            try:
                raw, _, value, identity = self._read(parent, name)
            except (OperationStoreError, private_files.StorageError):
                # Foreign/damaged/uncertain evidence occupies space, but never
                # supplies permission to delete it.
                continue
            if "completion" in value:
                candidates.append((value["completion"]["recorded_ns"], name, raw, identity))
        victims, count = [], len(names) + additional
        total += delta
        for candidate in sorted(candidates, key=lambda item: (item[0], item[1])):
            if count <= MAX_RECORDS and total <= MAX_TOTAL_BYTES:
                break
            victims.append(candidate)
            count -= 1
            total -= len(candidate[2])
        if count > MAX_RECORDS or total > MAX_TOTAL_BYTES:
            raise OperationStoreError("capacity")
        return victims

    def _reclaim(self, parent, victims):
        changed = False
        try:
            for _, name, expected, identity in victims:
                parent.assert_current()
                raw, _, _, current_identity = self._read(parent, name)
                if raw != expected or identity != current_identity:
                    raise OperationStoreError("conflict")
                # Noncooperating writers can race the final check; the same
                # documented private-lock requirement applies to deletion.
                changed = True
                parent.unlink(name)
            if victims:
                parent.sync()
                parent.assert_current()
        except Exception:
            if changed:
                raise OperationStoreError("retention_uncertain") from None
            raise

    def save(self, context):
        """Write once, fsync and read back before the caller can submit commit."""
        context = self._context(context)
        name = self._name(context["operation_id"])
        raw = _encoded({"version": 1, "target_sha256": self.target, "context": context})
        if len(raw) > MAX_RECORD_BYTES:
            raise OperationStoreError("capacity")
        reclaimed = False
        try:
            with (private_files._Parent(self.directory) as parent,
                  private_files._Lock(parent, ".favorites-operations.lock")):
                parent.assert_current()
                try:
                    current, stored, _, _ = self._read(parent, name)
                except FileNotFoundError:
                    current = None
                if current is not None:
                    if stored != context:
                        raise OperationStoreError("conflict")
                    raw = current  # An identical save must preserve completion evidence.
                else:
                    victims = self._inventory(parent, 1, len(raw))
                    self._reclaim(parent, victims)
                    reclaimed = bool(victims)
                    parent.assert_current()
                    parent.write_new(name, raw)
                # An earlier save may have written the file before sync failed.
                parent.sync()
                confirmed, _, _, _ = self._read(parent, name)
                parent.assert_current()
                if confirmed != raw:
                    raise OperationStoreError("conflict")
                return {"operation_id": context["operation_id"], "path": str(self.directory / name),
                        "sha256": hashlib.sha256(raw).hexdigest(), "player_outcome": "unknown"}
        except (OSError, private_files.StorageError, OperationStoreError) as error:
            if reclaimed:
                raise OperationStoreError("retention_uncertain") from None
            if isinstance(error, OperationStoreError):
                raise
            raise OperationStoreError("unavailable") from None

    def mark_completed(self, operation_id, result):
        """Retain verified same-runtime evidence; never authorize player replay.

        The result's verified flag is not trusted: request/data are revalidated
        against saved begin/preview. Cross-runtime evidence remains protected.
        """
        name = self._name(operation_id)
        try:
            if (type(result) is not protocol.ValidatedResult or type(result._request) is not bytes
                    or type(result._data) is not bytes or len(result._request) > 16384
                    or len(result._data) > protocol.MAX_METADATA_BYTES):
                raise ValueError
            completion = {"version": 1, "recorded_ns": time.time_ns(), "request": result.request,
                          "response": {"id": result.request_id, "status": result.status, "data": result.data}}
        except Exception:  # noqa: BLE001 - immutable result parsing must not leak input
            raise OperationStoreError("invalid_completion") from None
        attempted, reclaimed = False, False
        temporary = None
        temporary_parent = None
        try:
            with (private_files._Parent(self.directory) as parent,
                  private_files._Lock(parent, ".favorites-operations.lock")):
                parent.assert_current()
                before, context, value, identity = self._read(parent, name)
                receipt = self._completion(context, completion)
                if "completion" in value:
                    if self._completion(context, value["completion"]) != receipt:
                        raise OperationStoreError("conflict")
                    after = before
                else:
                    value["completion"] = completion
                    after = _encoded(value)
                    if len(after) > MAX_RECORD_BYTES:
                        raise OperationStoreError("capacity")
                    # Include the original record plus the fsynced staging
                    # copy in the byte ceiling, not only final retained size.
                    victims = self._inventory(parent, 0, len(after), protected=(name,), reserve_temporary=1)
                    self._reclaim(parent, victims)
                    reclaimed = bool(victims)
                    temporary = ".favorites-completion-" + secrets.token_hex(16) + ".tmp"
                    temporary_parent = parent.identity
                    parent.write_new(temporary, after)
                    current, _, _, current_identity = self._read(parent, name)
                    parent.assert_current()
                    if current != before or current_identity != identity:
                        raise OperationStoreError("conflict")
                    attempted = True
                    parent.replace(temporary, name)
                    temporary = None
                parent.sync()
                confirmed, _, _, _ = self._read(parent, name)
                parent.assert_current()
                if confirmed != after:
                    raise OperationStoreError("conflict")
                return {"operation_id": operation_id, "path": str(self.directory / name),
                        "sha256": hashlib.sha256(after).hexdigest(), "retention": "completed", "replay_allowed": False}
        except (OSError, private_files.StorageError, OperationStoreError) as error:
            if attempted or reclaimed:
                raise OperationStoreError("retention_uncertain") from None
            if isinstance(error, OperationStoreError):
                raise
            if isinstance(error, FileNotFoundError):
                raise OperationStoreError("missing") from None
            raise OperationStoreError("unavailable") from None
        finally:
            if temporary is not None:
                try:
                    with private_files._Parent(self.directory) as parent:
                        if parent.identity == temporary_parent:
                            parent.unlink(temporary)
                except (OSError, private_files.StorageError):
                    pass

    def load(self, operation_id):
        """Return retained context, never an assumption that writes did not occur."""
        name = self._name(operation_id)
        try:
            with private_files._Parent(self.directory) as parent:
                _, context, _, _ = self._read(parent, name)
                parent.assert_current()
                return context
        except FileNotFoundError:
            raise OperationStoreError("missing") from None
        except (OSError, private_files.StorageError):
            raise OperationStoreError("unavailable") from None
