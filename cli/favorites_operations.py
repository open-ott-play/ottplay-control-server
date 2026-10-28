"""Private, immutable intent evidence for favorites receipt reconciliation.

Saving a context never proves that a player applied it. Records have no automatic
expiry or replay path. A caller must save successfully before submitting commit,
then reconcile through the player's scoped status and current store evidence.
"""

import copy
import hashlib
import json
from pathlib import Path
import re

import preset_favorites as private_files

MAX_RECORD_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_RECORDS = 32
PREFIX = "favorites-operation-"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
RECORD = re.compile(PREFIX + r"[0-9a-f-]{36}\.json\Z")


class OperationStoreError(ValueError):
    """Fixed text; never include credentials, rejected documents or upstream errors."""

    def __init__(self, code):
        self.code = code
        super().__init__({
            "invalid_context": "Invalid favorites operation context; commit was not authorized.",
            "wrong_target": "Favorites context belongs to another server or player.",
            "conflict": "This favorites operation already has different retained evidence.",
            "capacity": "Favorites evidence storage is full; reconcile retained operations before another change.",
            "unavailable": "Private favorites evidence could not be saved and verified; do not send commit.",
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

    def _read(self, parent, name):
        raw, _ = parent.read(name)
        if len(raw) > MAX_RECORD_BYTES:
            raise OperationStoreError("invalid_context")
        value = private_files._decode(raw)
        if (set(value) != {"version", "target_sha256", "context"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise OperationStoreError("invalid_context")
        if value["target_sha256"] != self.target:
            raise OperationStoreError("wrong_target")
        context = self._context(value["context"])
        if name != self._name(context["operation_id"]):
            raise OperationStoreError("invalid_context")
        return raw, context

    def save(self, context):
        """Write once, fsync and read back before the caller can submit commit."""
        context = self._context(context)
        name = self._name(context["operation_id"])
        raw = _encoded({"version": 1, "target_sha256": self.target, "context": context})
        if len(raw) > MAX_RECORD_BYTES:
            raise OperationStoreError("capacity")
        try:
            with private_files._Parent(self.directory) as parent:
                with private_files._Lock(parent, ".favorites-operations.lock"):
                    parent.assert_current()
                    try:
                        current, _ = self._read(parent, name)
                    except FileNotFoundError:
                        current = None
                    if current is not None:
                        if current != raw:
                            raise OperationStoreError("conflict")
                    else:
                        # Stat only this store's files; do not read other contexts
                        # or silently evict old/unknown operations to make space.
                        rows = [path for path in self.directory.iterdir() if RECORD.fullmatch(path.name)]
                        sizes = []
                        for path in rows:
                            metadata = path.lstat()
                            if not private_files._private_regular(metadata):
                                raise OperationStoreError("unavailable")
                            sizes.append(metadata.st_size)
                        if len(rows) >= MAX_RECORDS or sum(sizes) + len(raw) > MAX_TOTAL_BYTES:
                            raise OperationStoreError("capacity")
                        parent.assert_current()
                        parent.write_new(name, raw)
                    # An earlier save may have written the file before sync failed.
                    parent.sync()
                    confirmed, _ = self._read(parent, name)
                    parent.assert_current()
                    if confirmed != raw:
                        raise OperationStoreError("conflict")
                    return {"operation_id": context["operation_id"], "path": str(self.directory / name),
                            "sha256": hashlib.sha256(raw).hexdigest(), "player_outcome": "unknown"}
        except (OSError, private_files.StorageError):
            raise OperationStoreError("unavailable") from None

    def load(self, operation_id):
        """Return retained context, never an assumption that writes did not occur."""
        name = self._name(operation_id)
        try:
            with private_files._Parent(self.directory) as parent:
                _, context = self._read(parent, name)
                parent.assert_current()
                return context
        except FileNotFoundError:
            raise OperationStoreError("missing") from None
        except (OSError, private_files.StorageError):
            raise OperationStoreError("unavailable") from None
