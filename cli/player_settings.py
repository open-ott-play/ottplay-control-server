"""Typed, source-bound player preferences; no provider switching or write replay."""
import json
import re
import time
from pathlib import Path

TOKEN = r"[A-Za-z0-9_.:-]{1,128}"
ID = r"[A-Za-z][A-Za-z0-9_]{0,63}"
ALIAS = r"[A-Za-z0-9_.+\-]{1,64}"
REASONS = {
    "invalid_request": "The player rejected the settings request",
    "atomic_patch_required": "Change this active connection using settings patch command_server or settings patch local_http --value-file FILE",
    "unknown_setting": "This setting is not registered by the player",
    "invalid_value": "The value is invalid for this setting",
    "runtime_changed": "The player restarted; read the setting again",
    "source_changed": "The provider or profile changed; read the setting again",
    "revision_conflict": "The setting changed concurrently; read it again",
    "policy_restricted": "The player's existing settings policy prevents this change",
    "local_only": "This setting requires its dedicated local workflow",
    "unsupported": "This player does not support the settings operation",
    "storage_failed": "The player could not verify the saved setting",
    "effect_failed": "The setting was saved but its application failed",
}
ACCESS_REASONS = set(REASONS) | {"device_forced_auto", "lifecycle_required", "local_action_required"}


def keys(value, required, optional=()):
    return isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional)


def pattern(value, regex):
    return isinstance(value, str) and re.fullmatch(regex, value) is not None


def provider_id(value):
    # Registered IDs are labels, including named providers and family/name IDs.
    return (pattern(value, r"[a-z0-9][a-z0-9_./-]{0,31}")
            and ".." not in value and "//" not in value and not value.endswith("/"))


def text(value, maximum):
    try:
        return (isinstance(value, str) and len(value.encode("utf-8")) <= maximum
                and all(ord(char) >= 32 and ord(char) != 127 for char in value))
    except UnicodeError:
        return False


def typed(value, kind=None):
    if type(value) is int:
        return kind in (None, "integer") and abs(value) <= 9007199254740991
    if isinstance(value, str):
        return kind in (None, "string") and text(value, 4096)
    return (kind in (None, "string_array") and isinstance(value, list) and len(value) <= 64
            and all(text(item, 4096) for item in value))


def _descriptor_valid(row):
    if (not keys(row, ("id", "type", "scope", "sensitive", "access", "constraints", "apply"),
                 ("default_value", "choices", "description", "unit"))
            or not pattern(row["id"], ID) or row["type"] not in ("integer", "string", "string_array")
            or row["scope"] not in ("application", "provider") or type(row["sensitive"]) is not bool
            or row["apply"] not in ("immediate", "next_stream", "manual", "after_ack")):
        return False
    access, constraints = row["access"], row["constraints"]
    if (not keys(access, ("read", "write"), ("reason",))
            or type(access["read"]) is not bool or type(access["write"]) is not bool
            or ("reason" in access and access["reason"] not in ACCESS_REASONS)
            or not keys(constraints, (), ("minimum", "maximum", "values", "max_utf8_bytes", "max_items"))):
        return False
    for key in ("minimum", "maximum"):
        if key in constraints and (row["type"] != "integer" or not typed(constraints[key], "integer")):
            return False
    if ("minimum" in constraints and "maximum" in constraints
            and constraints["minimum"] > constraints["maximum"]):
        return False
    for key, maximum in (("max_utf8_bytes", 4096), ("max_items", 64)):
        if key in constraints and (type(constraints[key]) is not int or not 0 <= constraints[key] <= maximum):
            return False
    if "values" in constraints:
        values = constraints["values"]
        if (not isinstance(values, list) or len(values) > 256
                or any(not typed(value, row["type"]) or isinstance(value, list) for value in values)):
            return False
    if "default_value" in row and (row["sensitive"] or not typed(row["default_value"], row["type"])):
        return False
    if "description" in row and not text(row["description"], 512):
        return False
    if "unit" in row and row["unit"] not in ("seconds", "percent"):
        return False
    if "choices" in row:
        if not isinstance(row["choices"], list) or len(row["choices"]) > 256:
            return False
        aliases = set()
        for choice in row["choices"]:
            if (not keys(choice, ("value", "label", "aliases"))
                    or not typed(choice["value"], row["type"]) or not text(choice["label"], 160)
                    or not isinstance(choice["aliases"], list) or len(choice["aliases"]) > 16):
                return False
            for alias in choice["aliases"]:
                if not pattern(alias, ALIAS) or alias.casefold() in aliases:
                    return False
                aliases.add(alias.casefold())
    return True


def descriptor_valid(row):
    try:
        return _descriptor_valid(row)
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False


def snapshot_valid(row, request, descriptor=None):
    if (not keys(row, ("id", "revision", "storage"),
                 ("value", "persisted_value", "effective_value", "redacted", "configured"))
            or row["id"] != request["id"] or not pattern(row["revision"], TOKEN)
            or row["storage"] not in ("stored", "default", "invalid", "unavailable")
            or ("redacted" in row and row["redacted"] is not True)
            or ("configured" in row and type(row["configured"]) is not bool)):
        return False
    sensitive = descriptor is not None and descriptor["sensitive"]
    redacted = sensitive or row.get("redacted") is True
    if sensitive and row.get("redacted") is not True:
        return False
    if descriptor is not None and not sensitive and row.get("redacted"):
        return False
    if row["storage"] != "stored" and "persisted_value" in row:
        return False
    readable = descriptor is not None and descriptor["access"]["read"] and not sensitive
    if readable and row["storage"] != "unavailable" and "value" not in row:
        return False
    if readable and row["storage"] == "stored" and "persisted_value" not in row:
        return False
    if request["op"] == "set" and row["storage"] != "stored":
        return False
    for key in ("value", "persisted_value", "effective_value"):
        if key in row and (redacted or not typed(row[key], descriptor["type"] if descriptor else None)):
            return False
    return request["op"] != "set" or redacted or row.get("persisted_value") == request["value"]


def _metadata(api, data, request, descriptor=None, rejected=False):
    invalid = "The player returned invalid settings metadata; the request was not repeated"
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
            or data.get("runtime") != request["runtime"] or data.get("op") != request["op"]
            or len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 65536):
        raise api.Error(invalid)
    if rejected:
        if (not keys(data, ("version", "op", "runtime", "error", "reason"))
                or not text(data["error"], 256) or data["reason"] not in REASONS):
            raise api.Error(invalid)
        return data
    context = data.get("context")
    if (not keys(context, ("token", "provider", "profile")) or not pattern(context["token"], TOKEN)
            or (context["provider"] is not None and not provider_id(context["provider"]))
            or (context["profile"] is not None and (type(context["profile"]) is not int or not 1 <= context["profile"] <= 15))
            or ("context" in request and context["token"] != request["context"])):
        raise api.Error(invalid)
    common = ("version", "op", "runtime", "context")
    if request["op"] == "schema":
        if (not keys(data, common + ("settings", "next_cursor")) or not isinstance(data["settings"], list)
                or len(data["settings"]) > request.get("limit", 20)
                or any(not descriptor_valid(row) for row in data["settings"])
                or len({row["id"] for row in data["settings"]}) != len(data["settings"])
                or (data["next_cursor"] is not None and (not pattern(data["next_cursor"], TOKEN) or not data["settings"]))):
            raise api.Error(invalid)
    elif request["op"] == "get":
        if (not keys(data, common + ("descriptor", "setting")) or not descriptor_valid(data["descriptor"])
                or data["descriptor"]["id"] != request["id"]
                or not snapshot_valid(data["setting"], request, data["descriptor"])):
            raise api.Error(invalid)
    else:
        application = data.get("application")
        if (not keys(data, common + ("setting", "changed", "persisted", "application"))
                or type(data["changed"]) is not bool or data["persisted"] is not True
                or not snapshot_valid(data["setting"], request, descriptor)
                or not keys(application, ("state",), ("reason",))
                or application["state"] not in ("completed", "deferred", "failed")
                or not ((application["state"] == "completed" and "reason" not in application)
                        or (application["state"] == "deferred" and application.get("reason") in ("next_stream", "manual", "after_ack", "view_loading"))
                        or (application["state"] == "failed" and application.get("reason") == "effect_failed"))):
            raise api.Error(invalid)
    return data


def metadata(api, data, request, descriptor=None, rejected=False):
    try:
        return _metadata(api, data, request, descriptor, rejected)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise api.Error("The player returned invalid settings metadata; the request was not repeated") from None


def parse_value(api, word, descriptor):
    """Resolve only canonical aliases supplied by this runtime's schema."""
    for choice in descriptor.get("choices", []):
        if word.casefold() in [alias.casefold() for alias in choice["aliases"]]:
            value = choice["value"]
            break
    else:
        if descriptor["type"] == "integer":
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", word):
                raise api.Error("This setting requires an integer or a listed choice alias")
            value = int(word)
        elif descriptor["type"] == "string_array":
            try:
                value = json.loads(word)
            except (ValueError, TypeError):
                raise api.Error("This setting requires a JSON array of strings") from None
        else:
            value = word
    constraints = descriptor["constraints"]
    invalid = not typed(value, descriptor["type"])
    if not invalid and descriptor["type"] == "integer":
        invalid = value < constraints.get("minimum", -9007199254740991) or value > constraints.get("maximum", 9007199254740991)
    if "values" in constraints and value not in constraints["values"]:
        invalid = True
    if not invalid and isinstance(value, str) and len(value.encode("utf-8")) > constraints.get("max_utf8_bytes", 4096):
        invalid = True
    if not invalid and isinstance(value, list) and (len(value) > constraints.get("max_items", 64)
                                  or any(len(item.encode("utf-8")) > constraints.get("max_utf8_bytes", 4096) for item in value)):
        invalid = True
    if invalid:
        raise api.Error("The value does not satisfy this setting's type, range or choices; no change was sent")
    return value


def display_value(value):
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def print_result(data, descriptor=None):
    if data["op"] == "schema":
        print("Available settings (current player and source):")
        for row in data["settings"]:
            constraints = row["constraints"]
            detail = row["type"]
            if "minimum" in constraints and "maximum" in constraints:
                detail += " " + str(constraints["minimum"]) + ".." + str(constraints["maximum"])
            if "unit" in row:
                detail += " " + row["unit"]
            choices = ["/".join(choice["aliases"]) for choice in row.get("choices", []) if choice["aliases"]]
            if choices:
                detail += "; " + ", ".join(choices)
            access = row["access"]
            detail += "; " + row["scope"] + "; " + ("writable" if access["write"] else "read only")
            if access.get("reason"):
                detail += " (" + access["reason"] + ")"
            if row["sensitive"]:
                detail += "; values redacted"
            print("  " + row["id"] + ": " + detail)
        return
    descriptor = descriptor or data["descriptor"]
    row, context = data["setting"], data["context"]
    scope = descriptor["scope"]
    if scope == "provider":
        scope += " " + (context["provider"] or "none") + (" / profile " + str(context["profile"]) if context["profile"] is not None else "")
    if row.get("redacted"):
        value = "[redacted]" + (" (configured)" if row.get("configured") else "")
    else:
        raw_value = row.get("persisted_value", row.get("value", "unavailable"))
        value = display_value(raw_value)
        choice = next((choice for choice in descriptor.get("choices", []) if choice["value"] == raw_value), None)
        if choice is not None:
            value += " (" + choice["label"] + ")"
    unit = " " + descriptor["unit"] if "unit" in descriptor else ""
    print(row["id"] + ": " + ("saved " if data["op"] == "set" else "") + value + unit + " (" + scope + "; " + row["storage"] + ")")
    if "effective_value" in row and row["effective_value"] != row.get("persisted_value", row.get("value")):
        effective = display_value(row["effective_value"])
        choice = next((choice for choice in descriptor.get("choices", []) if choice["value"] == row["effective_value"]), None)
        print("Effective value: " + effective + (" (" + choice["label"] + ")" if choice else ""))
    if data["op"] == "set":
        application = data["application"]
        note = {"completed": "applied", "deferred": "deferred", "failed": "failed after saving"}[application["state"]]
        reason = application.get("reason", "")
        detail = "interface is loading" if reason == "view_loading" else reason.replace("_", " ")
        print("Application: " + note + (" (" + detail + ")" if detail else "") + ".")
        if reason == "view_loading":
            print("Read this setting again to check its effective value.")
    elif descriptor["apply"] != "immediate":
        print("Changes apply: " + descriptor["apply"].replace("_", " ") + ".")
    if descriptor.get("description"):
        print(descriptor["description"])


def run(api, client, device, words, timeout, json_output):
    if len(words) >= 2 and words[0].casefold() == "settings" and words[1].casefold() == "patch":
        return run_patch(api, client, device, words[2:], timeout, json_output)
    if len(words) >= 2 and words[0].casefold() == "setting" and words[1] == "plexFolderPaths":
        return run_folders(api, client, device, words[2:], timeout, json_output)
    if words[0].casefold() == "settings":
        if len(words) > 2 or (len(words) == 2 and words[1].casefold() not in ("list", "schema")):
            raise api.Error("Use settings [list|schema], setting KEY, or setting KEY VALUE")
        operation, identifier, word = "schema", None, None
    else:
        if len(words) not in (2, 3, 4) or not pattern(words[1], ID):
            raise api.Error("Use setting KEY [VALUE] or setting KEY --value-file FILE")
        identifier, word = words[1], None
        if len(words) == 4:
            if words[2] != "--value-file":
                raise api.Error("Use setting KEY --value-file FILE")
            try:
                with Path(words[3]).open("rb") as stream:
                    raw = stream.read(16385)
                if len(raw) > 16384:
                    raise ValueError()
                word = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
            except (OSError, UnicodeError, ValueError):
                raise api.Error("Could not read the bounded UTF-8 setting value file") from None
        elif len(words) == 3:
            word = words[2]
        operation = "get" if word is None else "set"
    deadline, previous = time.monotonic() + timeout, client.timeout

    def call(action, params, descriptor=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise api.Error("Settings request exceeded --timeout; no further request was sent")
        client.timeout = remaining
        try:
            result = client.call(device, action, params)
        except (api.PlayerRejected, api.PlayerUnsupported) as exc:
            if action == "capabilities":
                raise api.Error("Settings discovery is unavailable; update the player/controller. No settings change was sent") from None
            checked = metadata(api, exc.data, params, rejected=True)
            raise api.Error(REASONS[checked["reason"]] + "; the request was not repeated") from None
        except api.HTTPError as exc:
            if exc.code in (400, 404, 405):
                raise api.Error("This controller does not support typed settings; update it and the player. The request was not repeated") from None
            raise
        if time.monotonic() >= deadline:
            raise api.Error("The settings response exceeded --timeout; inspect the setting before retrying. The request was not repeated")
        return result if action == "capabilities" else metadata(api, result, params, descriptor)

    descriptor = None
    try:
        caps = api.capabilities_metadata(call("capabilities", {}))
        if "player_settings" not in caps:
            raise api.Error("Typed settings require an updated player and controller; no settings change was sent")
        runtime = caps["player"]["runtime"]
        if operation == "schema":
            params = {"op": "schema", "runtime": runtime, "limit": 20}
            entries, ids, cursors, context = [], set(), set(), None
            while True:
                data = call("player_settings", params)
                if context is not None and context != data["context"]:
                    raise api.Error("Settings source changed during discovery; no change was sent")
                context = data["context"]
                for row in data["settings"]:
                    if row["id"] in ids or len(entries) >= 1024:
                        raise api.Error("Invalid or excessive settings pagination; no change was sent")
                    ids.add(row["id"])
                    entries.append(row)
                cursor = data["next_cursor"]
                if cursor is None:
                    break
                if cursor in cursors:
                    raise api.Error("Repeated settings cursor; no change was sent")
                cursors.add(cursor)
                params["cursor"] = cursor
            data["settings"] = entries
            if "plex_folders" in caps and context["provider"] == "plex":
                data["collections"] = [{"id": "plexFolderPaths", "action": "plex_folders", "capability": caps["plex_folders"]}]
        else:
            params = {"op": "get", "runtime": runtime, "id": identifier}
            data = call("player_settings", params)
            descriptor = data["descriptor"]
            if operation == "set":
                if not descriptor["access"]["write"]:
                    raise api.Error("This setting is not writable: " + descriptor["access"].get("reason", "local_only") + "; no change was sent")
                params = {"op": "set", "runtime": runtime, "id": identifier,
                          "context": data["context"]["token"], "revision": data["setting"]["revision"],
                          "value": parse_value(api, word, descriptor)}
                if len(json.dumps({"action": "player_settings", "params": params}, ensure_ascii=False).encode("utf-8")) > 16384:
                    raise api.Error("The settings request exceeds the 16 KiB limit; no change was sent")
                expected_context = data["context"]
                data = call("player_settings", params, descriptor)
                if data["context"] != expected_context:
                    raise api.Error("The player returned a different settings source; inspect before retrying. The request was not repeated")
        if json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print_result(data, descriptor)
            if data.get("collections"):
                print("  plexFolderPaths: collection; use setting plexFolderPaths [add PATH TITLE|remove PATH|clear]; empty means all libraries")
        return 1 if data.get("application", {}).get("state") == "failed" else 0
    finally:
        client.timeout = previous


FOLDER_PATH = r"/library/(?:sections/[1-9][0-9]{0,19}/(?:all(?:\?type=(?:[134]|10))?|folder(?:\?parent=[0-9]{1,20})?)|metadata/[1-9][0-9]{0,19}/children)"
FOLDER_REASONS = {key: value for key, value in REASONS.items() if key not in ("unknown_setting", "local_only", "effect_failed", "atomic_patch_required")}
FOLDER_REASONS["capacity_exceeded"] = "The Plex selection already contains 1000 folders; remove a folder before adding another"


def folder_title(value):
    return text(value, 4096) and len(value.encode("utf-16-le")) <= 2048


def folder_metadata(api, data, request, rejected=False):
    try:
        return _folder_metadata(api, data, request, rejected)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise api.Error("Invalid Plex folder settings response; the request was not repeated") from None


def _folder_metadata(api, data, request, rejected=False):
    invalid = "Invalid Plex folder settings response; the request was not repeated"
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
            or data.get("runtime") != request["runtime"] or data.get("op") != request["op"]
            or len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 65536):
        raise api.Error(invalid)
    if rejected:
        if (not keys(data, ("version", "op", "runtime", "error", "reason"))
                or not text(data["error"], 256) or data["reason"] not in FOLDER_REASONS
                or (data["reason"] == "capacity_exceeded" and request["op"] != "add")):
            raise api.Error(invalid)
        return data
    context = data.get("context")
    if (not keys(context, ("token", "provider", "profile")) or not pattern(context["token"], TOKEN)
            or context["provider"] != "plex"
            or (context["profile"] is not None and (type(context["profile"]) is not int or not 1 <= context["profile"] <= 15))
            or ("context" in request and context["token"] != request["context"])
            or not pattern(data.get("revision"), TOKEN) or type(data.get("total")) is not int or not 0 <= data["total"] <= 1000):
        raise api.Error(invalid)
    common = ("version", "op", "runtime", "context", "revision", "total")
    if request["op"] == "list":
        items, cursor = data.get("items"), data.get("next_cursor")
        if (not keys(data, common + ("items", "max_items", "next_cursor"))
                or type(data["max_items"]) is not int or data["max_items"] != 1000 or not isinstance(items, list)
                or len(items) > min(request.get("limit", 10), data["total"])
                or (not items and data["total"] > 0)
                or any(not keys(row, ("path", "title")) or not pattern(row["path"], FOLDER_PATH) or not folder_title(row["title"]) for row in items)
                or len({row["path"] for row in items}) != len(items)
                or (cursor is not None and (not pattern(cursor, TOKEN) or not items or len(items) == data["total"] or cursor == request.get("cursor")))
                or ("cursor" not in request and cursor is None and len(items) != data["total"])):
            raise api.Error(invalid)
    else:
        application = data.get("application")
        if (not keys(data, common + ("changed", "persisted", "application"))
                or type(data["changed"]) is not bool or data["persisted"] is not True
                or (request["op"] == "add" and data["total"] < 1)
                or (request["op"] == "clear" and data["total"] != 0)
                or not keys(application, ("state", "reason"))
                or (application["state"], application["reason"]) not in (("deferred", "next_catalogue"), ("failed", "effect_failed"))):
            raise api.Error(invalid)
    return data


def run_folders(api, client, device, words, timeout, json_output):
    operation = words[0].casefold() if words else "list"
    value = {}
    if operation == "add" and len(words) >= 3:
        value = {"path": words[1], "title": " ".join(words[2:])}
    elif operation == "remove" and len(words) == 2:
        value = {"path": words[1]}
    elif operation not in ("list", "clear") or len(words) > 1:
        raise api.Error("Use setting plexFolderPaths [list|add PATH TITLE|remove PATH|clear]; clear selects all libraries")
    if ("path" in value and not pattern(value["path"], FOLDER_PATH)) or ("title" in value and not folder_title(value["title"])):
        raise api.Error("Use a canonical Plex library/folder path and a title of at most 1024 UTF-16 units; no change was sent")
    deadline, previous = time.monotonic() + timeout, client.timeout

    def call(action, params):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise api.Error("Plex folder request exceeded --timeout; no further request was sent")
        client.timeout = remaining
        try:
            data = client.call(device, action, params)
        except (api.PlayerRejected, api.PlayerUnsupported) as exc:
            if action == "capabilities" or not isinstance(exc.data, dict):
                raise api.Error("Plex folder settings require an updated player/controller; the request was not repeated") from None
            error = folder_metadata(api, exc.data, params, rejected=True)
            raise api.Error(FOLDER_REASONS[error["reason"]] + "; the request was not repeated") from None
        except api.HTTPError as exc:
            if exc.code in (400, 404, 405):
                raise api.Error("This controller does not support Plex folder settings; the request was not repeated") from None
            raise
        if time.monotonic() >= deadline:
            raise api.Error("Plex folder response exceeded --timeout; inspect the selection before retrying; the request was not repeated")
        return data if action == "capabilities" else folder_metadata(api, data, params)

    try:
        caps = api.capabilities_metadata(call("capabilities", {}))
        if "plex_folders" not in caps:
            raise api.Error("Plex folder settings require an updated player/controller advertising plex_folders; no change was sent")
        params = {"op": "list", "runtime": caps["player"]["runtime"], "limit": 10 if operation == "list" else 1}
        data = call("plex_folders", params)
        context, revision, total = data["context"], data["revision"], data["total"]
        if operation == "list":
            items, seen, cursors = [], set(), set()
            while True:
                if data["context"] != context or data["revision"] != revision or data["total"] != total:
                    raise api.Error("Plex folder selection changed during pagination; no change was sent")
                for row in data["items"]:
                    if row["path"] in seen or len(items) >= total:
                        raise api.Error("Invalid duplicate/excessive Plex folder pagination; no change was sent")
                    seen.add(row["path"]); items.append(row)
                cursor = data["next_cursor"]
                if cursor is None:
                    if len(items) != total:
                        raise api.Error("Incomplete Plex folder selection; no change was sent")
                    break
                if cursor in cursors or len(items) >= total:
                    raise api.Error("Invalid repeated Plex folder cursor; no change was sent")
                cursors.add(cursor); params["cursor"] = cursor
                data = call("plex_folders", params)
            data["items"] = items
        else:
            params = dict(value, op=operation, runtime=params["runtime"], context=context["token"], revision=revision)
            if len(json.dumps({"action": "plex_folders", "params": params}, ensure_ascii=False).encode("utf-8")) > 16384:
                raise api.Error("Plex folder request exceeds 16 KiB; no change was sent")
            data = call("plex_folders", params)
            if data["context"] != context:
                raise api.Error("Plex folder source changed after writing; inspect before retrying; the request was not repeated")
        if json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print("Plex folder selection: " + ("all libraries" if data["total"] == 0 else str(data["total"]) + " folders") + ".")
            if operation == "list":
                for row in data["items"]:
                    print("  " + row["path"] + "  " + row["title"])
            else:
                print("Saved; applies on the next catalogue load." if data["application"]["state"] == "deferred" else "Saved, but catalogue invalidation failed; inspect before retrying.")
        return 1 if data.get("application", {}).get("state") == "failed" else 0
    finally:
        client.timeout = previous


PATCH_GROUPS = {
    "command_server": {"commandServerAddress": 4096, "commandServerToken": 256, "commandServerEnabled": 0},
    "local_http": {"localCmdUrl": 4096, "localHttpDeviceCode": 256, "localHttpEnabled": 0},
}
PATCH_REASONS = {key: value for key, value in REASONS.items()
                 if key not in ("unknown_setting", "local_only", "atomic_patch_required")}
TRANSITION_REASONS = ("handshake_timeout", "authorization_failed", "network_error", "storage_failed", "ack_timeout", "effect_failed")


def _patch_metadata(api, data, request, rejected=False):
    invalid = "Invalid grouped settings response; inspect the connection before retrying; the request was not repeated"
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
            or data.get("op") != request["op"] or data.get("runtime") != request["runtime"]
            or data.get("group") != request["group"]
            or len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 65536):
        raise api.Error(invalid)
    common = ("version", "op", "runtime", "group")
    if rejected:
        if (not keys(data, common + ("error", "reason")) or not text(data["error"], 256)
                or data["reason"] not in PATCH_REASONS):
            raise api.Error(invalid)
        return data
    required = common + ("context", "revision", "settings", "transition")
    if request["op"] == "set":
        required += ("persisted", "changed", "application")
    context, transition, rows = data.get("context"), data.get("transition"), data.get("settings")
    if (not keys(data, required) or not pattern(data["revision"], TOKEN)
            or not keys(context, ("token", "provider", "profile")) or not pattern(context["token"], TOKEN)
            or (context["provider"] is not None and not provider_id(context["provider"]))
            or (context["profile"] is not None and (type(context["profile"]) is not int or not 1 <= context["profile"] <= 15))
            or ("context" in request and context["token"] != request["context"])
            or not keys(transition, ("state",), ("reason",))
            or not isinstance(rows, list) or len(rows) != 3):
        raise api.Error(invalid)
    state = transition["state"]
    if state in ("idle", "pending", "applied"):
        if "reason" in transition:
            raise api.Error(invalid)
    elif state in ("rolled_back", "failed"):
        if transition.get("reason") not in TRANSITION_REASONS + (("superseded",) if state == "failed" else ()):
            raise api.Error(invalid)
    else:
        raise api.Error(invalid)
    seen, fields = set(), PATCH_GROUPS[request["group"]]
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row["id"] not in fields or row["id"] in seen
                or row.get("storage") not in ("stored", "default", "invalid")
                or (request["op"] == "set" and row["storage"] != "stored")):
            raise api.Error(invalid)
        seen.add(row["id"])
        if fields[row["id"]]:
            if (not keys(row, ("id", "storage", "redacted", "configured"))
                    or row["redacted"] is not True or type(row["configured"]) is not bool):
                raise api.Error(invalid)
        else:
            if (not keys(row, ("id", "storage", "value"), ("persisted_value",))
                    or type(row["value"]) is not int or row["value"] not in (0, 1)):
                raise api.Error(invalid)
            if row["storage"] == "stored":
                if type(row.get("persisted_value")) is not int or row["persisted_value"] not in (0, 1):
                    raise api.Error(invalid)
            elif "persisted_value" in row:
                raise api.Error(invalid)
            if request["op"] == "set" and (row["value"] != row["persisted_value"]
                    or request["values"].get(row["id"], row["value"]) != row["value"]):
                raise api.Error(invalid)
    if request["op"] == "set":
        if data["persisted"] is not True or type(data["changed"]) is not bool:
            raise api.Error(invalid)
        completed = data["application"] == {"state": "completed"} and not data["changed"] and state == "applied"
        pending = data["application"] == {"state": "deferred", "reason": "after_ack"} and state == "pending"
        if not (completed or pending):
            raise api.Error(invalid)
    return data


def patch_metadata(api, data, request, rejected=False):
    try:
        return _patch_metadata(api, data, request, rejected)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise api.Error("Invalid grouped settings response; inspect the connection before retrying; the request was not repeated") from None


def read_patch_values(api, group, path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        values = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        fields = PATCH_GROUPS[group]
        if not isinstance(values, dict) or not 1 <= len(values) <= 3 or not set(values) <= set(fields):
            raise ValueError()
        for key, value in values.items():
            limit = fields[key]
            if (limit and not text(value, limit)) or (not limit and (type(value) is not int or value not in (0, 1))):
                raise ValueError()
        return values
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise api.Error("Could not read grouped settings: use a UTF-8 JSON object containing only this group's fields, within 16 KiB; no change was sent") from None


def run_patch(api, client, device, words, timeout, json_output):
    if (len(words) not in (1, 3) or words[0] not in PATCH_GROUPS
            or (len(words) == 3 and words[1] != "--value-file")):
        raise api.Error("Use settings patch command_server|local_http [--value-file FILE]")
    group = words[0]
    values = read_patch_values(api, group, words[2]) if len(words) == 3 else None
    deadline, previous = time.monotonic() + timeout, client.timeout

    def call(action, params):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise api.Error("Grouped settings request exceeded --timeout; no further request was sent")
        client.timeout = remaining
        try:
            data = client.call(device, action, params)
        except (api.PlayerRejected, api.PlayerUnsupported) as exc:
            if action == "capabilities":
                raise api.Error("Grouped settings require an updated player/controller; no change was sent") from None
            error = patch_metadata(api, exc.data, params, rejected=True)
            raise api.Error(PATCH_REASONS[error["reason"]] + "; the request was not repeated") from None
        except api.HTTPError as exc:
            if exc.code in (400, 404, 405):
                raise api.Error("This controller does not support grouped settings; the request was not repeated") from None
            raise
        if time.monotonic() >= deadline:
            raise api.Error("Grouped settings response exceeded --timeout; inspect the connection before retrying; the request was not repeated")
        return data if action == "capabilities" else patch_metadata(api, data, params)

    try:
        caps = api.capabilities_metadata(call("capabilities", {}))
        if "player_settings_patch" not in caps:
            raise api.Error("Grouped settings require an updated player/controller; no change was sent")
        params = {"op": "get", "runtime": caps["player"]["runtime"], "group": group}
        data = call("player_settings_patch", params)
        if values is not None:
            context = data["context"]
            params = dict(params, op="set", context=context["token"], revision=data["revision"], values=values)
            if len(json.dumps({"action": "player_settings_patch", "params": params}, ensure_ascii=False).encode("utf-8")) > 16384:
                raise api.Error("Grouped settings request exceeds 16 KiB; no change was sent")
            data = call("player_settings_patch", params)
            if data["context"] != context:
                raise api.Error("Grouped settings source changed after writing; inspect before retrying; the request was not repeated")
        if json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print("Connection group: " + group)
            for row in data["settings"]:
                value = ("[redacted; " + ("configured" if row["configured"] else "empty") + "]"
                         if row.get("redacted") else str(row["value"]))
                print("  " + row["id"] + ": " + value + " (" + row["storage"] + ")")
            transition = data["transition"]
            print("Transition: " + transition["state"].replace("_", " ")
                  + (" (" + transition["reason"].replace("_", " ") + ")" if "reason" in transition else "") + ".")
            if data["op"] == "set":
                print("Saved; connection switch starts after acknowledgement. New connectivity is not confirmed."
                      if data["application"]["state"] == "deferred" else "Unchanged; applied without reconnecting.")
        return 1 if data["transition"]["state"] in ("failed", "rolled_back") else 0
    finally:
        client.timeout = previous
