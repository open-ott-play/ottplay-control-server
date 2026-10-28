"""Private favorites documents attached to existing named CLI presets.

The caller supplies existing preset lookup functions and a portable-document
validator returning a JSON object. This module has no transport or entry schema.
Missing documents mean "do not sync". Backups contain the complete private config.

Cooperating writers serialize through a config-specific lock. Byte fingerprints
are checked immediately before replacement, but this is optimistic CAS, not a
kernel CAS. An old CLI/editor ignoring the lock can race that final check or
overwrite the file afterward; do not run such writers concurrently.
"""

import copy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


MAX_CONFIG_BYTES = 4 * 1024 * 1024
KEY = "preset_favorites"


class StorageError(Exception):
    """Fixed, non-secret text and explicit persistence outcome metadata."""

    def __init__(self, code, message, *, replacement_occurred=False, backup_path=None):
        super().__init__(message)
        self.code = code
        self.replacement_occurred = replacement_occurred
        self.backup_path = backup_path


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _document_digest(value):
    return None if value is None else _digest(_json_bytes(value))


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def finite_only(_):
        raise ValueError("non-finite number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=finite_only)
        if not isinstance(value, dict):
            raise ValueError("configuration must be an object")
        _json_bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise StorageError("invalid_config", "The private configuration is not valid bounded JSON.") from None


def _private_regular(metadata):
    return (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
            and (os.name == "nt" or (metadata.st_mode & 0o077 == 0
                 and metadata.st_uid == os.getuid())))


class _Parent:
    """Anchor POSIX operations to the verified parent without following leaves."""

    def __init__(self, path):
        self.path, self.fd = path, None

    def __enter__(self):
        try:
            metadata = self.path.lstat()
            if (not stat.S_ISDIR(metadata.st_mode)
                    or (os.name != "nt" and (metadata.st_mode & 0o077 or metadata.st_uid != os.getuid()))):
                raise StorageError("unsafe_path", "The configuration directory must be private and must not be a symlink.")
            self.identity = (metadata.st_dev, metadata.st_ino)
            if os.name != "nt":
                self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
                opened = os.fstat(self.fd)
                if ((opened.st_dev, opened.st_ino) != self.identity
                        or opened.st_mode & 0o077 or opened.st_uid != os.getuid()):
                    raise StorageError("config_changed", "The configuration directory changed; prepare a new plan.")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def assert_current(self):
        metadata = self.path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != self.identity
                or (os.name != "nt" and (metadata.st_mode & 0o077 or metadata.st_uid != os.getuid()))):
            raise StorageError("config_changed", "The configuration directory changed; prepare a new plan.")

    def open(self, name, flags, mode=0o600):
        if self.fd is not None:
            return os.open(name, flags, mode, dir_fd=self.fd)
        self.assert_current()
        target = self.path / name
        if target.is_symlink():
            raise StorageError("unsafe_path", "Configuration, backup and lock paths must not be symlinks.")
        return os.open(target, flags, mode)

    def unlink(self, name):
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            os.unlink(self.path / name)

    def replace(self, source, destination):
        if self.fd is not None:
            os.replace(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            self.assert_current()
            os.replace(self.path / source, self.path / destination)

    def sync(self):
        if self.fd is not None:
            os.fsync(self.fd)

    def read(self, name):
        descriptor = self.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not _private_regular(metadata):
                raise StorageError("unsafe_path", "The configuration must be a private regular file with one link.")
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise StorageError("too_large", "The private configuration exceeds the 4 MiB limit.")
        return raw, (metadata.st_dev, metadata.st_ino)

    def write_new(self, name, raw):
        descriptor = self.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                if hasattr(os, "fchmod"):
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                self.unlink(name)
            except OSError:
                pass
            raise


class _Lock:
    def __init__(self, parent, name):
        self.parent, self.name, self.fd = parent, name, None

    def __enter__(self):
        try:
            self.fd = self.parent.open(self.name, os.O_RDWR | os.O_CREAT
                                       | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            if not _private_regular(os.fstat(self.fd)):
                raise StorageError("unsafe_lock", "The favorites lock must be a private regular file with one link.")
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"0")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BaseException as error:
            self.__exit__(None, None, None)
            if isinstance(error, StorageError) or not isinstance(error, Exception):
                raise
            raise StorageError("lock_unavailable", "Another favorites update is active or its private lock is unavailable.") from None

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@dataclass(frozen=True)
class UpdatePlan:
    path: str
    preset: str
    source_revision: str
    before_revision: str | None
    after_revision: str
    changed: bool
    _document: dict = field(repr=False)

    def summary(self):
        return {key: getattr(self, key) for key in
                ("preset", "source_revision", "before_revision", "after_revision", "changed")}


class Store:
    def __init__(self, path, *, preset_names, resolve_preset, validate_document):
        self.path = Path(path).expanduser().absolute()
        self.preset_names = preset_names
        self.resolve_preset = resolve_preset
        self.validate_document = validate_document

    def _names(self, config):
        try:
            return self.preset_names(config)
        except Exception:
            raise StorageError("invalid_presets", "The named presets are invalid or ambiguous.") from None

    def _resolve(self, config, requested):
        try:
            name = self.resolve_preset(config, requested)
        except Exception:
            raise StorageError("invalid_preset", "The requested existing preset is missing, invalid or ambiguous.") from None
        if name not in self._names(config):
            raise StorageError("invalid_preset", "The preset resolver did not return an existing canonical name.")
        return name

    def _document(self, document):
        try:
            value = self.validate_document(copy.deepcopy(document))
            if not isinstance(value, dict) or len(_json_bytes(value)) > MAX_CONFIG_BYTES:
                raise ValueError("invalid or oversized document")
            return copy.deepcopy(value)
        except Exception:
            raise StorageError("invalid_document", "The portable favorites document is invalid or unsupported.") from None

    def inspect(self, config):
        """Validate all stored documents; report and retain orphan entries."""
        names = self._names(config)
        if KEY not in config:
            return {"present": False, "presets": {}, "orphans": []}
        extension = config[KEY]
        if (not isinstance(extension, dict) or set(extension) != {"version", "presets"}
                or type(extension["version"]) is not int or extension["version"] != 1
                or not isinstance(extension["presets"], dict)):
            raise StorageError("invalid_extension", "Use the version 1 preset_favorites extension with a presets mapping.")
        keys = self._names({"presets": extension["presets"]})
        canonical = {name.casefold(): name for name in names}
        if any(key.casefold() in canonical and key != canonical[key.casefold()] for key in keys):
            raise StorageError("noncanonical_name", "Favorites keys must use the exact existing preset names.")
        documents = {key: self._document(extension["presets"][key]) for key in keys}
        return {"present": True, "presets": documents, "orphans": [key for key in keys if key not in names]}

    def get(self, config, requested):
        name = self._resolve(config, requested)
        return self.inspect(config)["presets"].get(name)

    def _read(self):
        try:
            with _Parent(self.path.parent) as parent:
                raw, _ = parent.read(self.path.name)
            return raw, _decode(raw)
        except OSError:
            raise StorageError("read_failed", "The private configuration could not be read safely.") from None

    def snapshot(self, requested):
        raw, config = self._read()
        name = self._resolve(config, requested)
        inspected = self.inspect(config)
        return {"preset": name, "document": inspected["presets"].get(name),
                "config_revision": _digest(raw), "orphans": inspected["orphans"]}

    def backup_document(self, requested, backup_path):
        """Read one document from this config's own private backup family.

        This is not full-config rollback. Callers retain their current snapshot
        revision for plan/apply CAS. Moved/renamed backups are deliberately
        refused: the filename digest detects accidental selection or corruption,
        not authenticity against another writer with access to this directory.
        Other historical preset settings and documents are not interpreted.
        """
        _, current = self._read()
        name = self._resolve(current, requested)
        try:
            backup = Path(backup_path).expanduser().absolute()
            match = re.fullmatch(re.escape(self.path.name)
                                 + r"\.favorites-backup-([0-9a-f]{12})-[0-9a-f]{16}\.json",
                                 backup.name)
            if backup.parent != self.path.parent or match is None:
                raise StorageError("invalid_backup", "Choose an unchanged favorites backup from this configuration's private directory.")
            with _Parent(self.path.parent) as parent:
                raw, _ = parent.read(backup.name)
                parent.assert_current()
        except (OSError, TypeError, ValueError):
            raise StorageError("backup_read_failed", "The favorites backup could not be read safely.") from None
        revision = _digest(raw)
        if revision[:12] != match.group(1):
            raise StorageError("invalid_backup", "The favorites backup does not match its recorded byte digest.")
        config = _decode(raw)
        if KEY not in config:
            raise StorageError("backup_document_missing", "The backup has no favorites document for the selected preset.")
        extension = config[KEY]
        if (not isinstance(extension, dict) or set(extension) != {"version", "presets"}
                or type(extension["version"]) is not int or extension["version"] != 1
                or not isinstance(extension["presets"], dict)):
            raise StorageError("invalid_extension", "Use the version 1 preset_favorites extension with a presets mapping.")
        self._names({"presets": extension["presets"]})
        if name not in extension["presets"]:
            raise StorageError("backup_document_missing", "The backup has no favorites document for the selected preset.")
        return {"preset": name, "document": self._document(extension["presets"][name]),
                "backup_revision": revision}

    def plan(self, requested, document, *, expected_revision=None):
        raw, config = self._read()
        if expected_revision is not None and expected_revision != _digest(raw):
            raise StorageError("config_changed", "The configuration changed; prepare a new favorites plan.")
        name = self._resolve(config, requested)
        before, after = self.get(config, name), self._document(document)
        before_revision, after_revision = _document_digest(before), _document_digest(after)
        return UpdatePlan(str(self.path), name, _digest(raw), before_revision, after_revision,
                          before_revision != after_revision, after)

    def _check_plan(self, plan, raw):
        if not isinstance(plan, UpdatePlan) or plan.path != str(self.path):
            raise StorageError("invalid_plan", "The favorites plan belongs to another configuration.")
        if _digest(raw) != plan.source_revision:
            raise StorageError("config_changed", "The configuration changed; prepare a new favorites plan.")
        config = _decode(raw)
        name = self._resolve(config, plan.preset)
        before, after = self.get(config, name), self._document(plan._document)
        if (name != plan.preset or _document_digest(before) != plan.before_revision
                or _document_digest(after) != plan.after_revision
                or type(plan.changed) is not bool or plan.changed != (plan.before_revision != plan.after_revision)):
            raise StorageError("invalid_plan", "The favorites plan changed; prepare a new plan.")
        return config, after

    def apply(self, plan):
        """Replace one document after durable backup; do not replay uncertainty."""
        try:
            with _Parent(self.path.parent) as parent:
                raw, _ = parent.read(self.path.name)
                self._check_plan(plan, raw)
                if not plan.changed:
                    return {"preset": plan.preset, "changed": False, "config_revision": _digest(raw), "backup_path": None}
                with _Lock(parent, self.path.name + ".preset-favorites.lock"):
                    return self._apply_locked(parent, plan)
        except OSError:
            raise StorageError("read_failed", "The private configuration or its lock could not be accessed safely.") from None

    def _apply_locked(self, parent, plan):
        raw, identity = parent.read(self.path.name)
        config, document = self._check_plan(plan, raw)
        updated = copy.deepcopy(config)
        updated.setdefault(KEY, {"version": 1, "presets": {}})["presets"][plan.preset] = document
        after = (json.dumps(updated, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        if len(after) > MAX_CONFIG_BYTES:
            raise StorageError("too_large", "The updated private configuration exceeds the 4 MiB limit.")
        parent.assert_current()
        backup = self.path.name + ".favorites-backup-" + plan.source_revision[:12] + "-" + secrets.token_hex(8) + ".json"
        try:
            parent.write_new(backup, raw)
            parent.sync()
        except OSError:
            raise StorageError("backup_failed", "The original configuration could not be backed up durably; it was not changed.") from None
        temporary = ".ott-favorites-" + secrets.token_hex(16)
        replaced, temporary_owned = False, False
        try:
            parent.write_new(temporary, after)
            temporary_owned = True
            parent.assert_current()
            current, current_identity = parent.read(self.path.name)
            if current_identity != identity or _digest(current) != plan.source_revision:
                raise StorageError("config_changed", "The configuration changed; prepare a new favorites plan.")
            parent.replace(temporary, self.path.name)
            replaced = True
            temporary = None
            parent.sync()
        except StorageError as error:
            error.backup_path = str(self.path.parent / backup)
            raise
        except OSError:
            if replaced:
                raise StorageError("commit_uncertain", "The file was replaced but directory sync failed. Inspect it and the backup before another change.",
                                   replacement_occurred=True, backup_path=str(self.path.parent / backup)) from None
            raise StorageError("write_failed", "The favorites update was not installed; the original configuration and backup remain.",
                               backup_path=str(self.path.parent / backup)) from None
        finally:
            if temporary is not None and temporary_owned:
                try:
                    parent.unlink(temporary)
                except OSError:
                    # Retain the original failure and its backup information.
                    # Any leftover owned temporary still has private permissions.
                    pass
        return {"preset": plan.preset, "changed": True, "config_revision": _digest(after),
                "backup_path": str(self.path.parent / backup)}
