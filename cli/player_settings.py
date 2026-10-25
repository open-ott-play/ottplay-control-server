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
ACCESS_REASONS = set(REASONS) | {"device_forced_auto", "lifecycle_required"}


def keys(value, required, optional=()):
    return isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional)


def pattern(value, regex):
    return isinstance(value, str) and re.fullmatch(regex, value) is not None


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
            or row["apply"] not in ("immediate", "next_stream", "manual")):
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
            or (context["provider"] is not None and not pattern(context["provider"], r"[a-z0-9_-]{1,32}"))
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
                        or (application["state"] == "deferred" and application.get("reason") in ("next_stream", "manual"))
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
        print("Application: " + note + (" (" + application["reason"].replace("_", " ") + ")" if application.get("reason") else "") + ".")
    elif descriptor["apply"] != "immediate":
        print("Changes apply: " + descriptor["apply"].replace("_", " ") + ".")
    if descriptor.get("description"):
        print(descriptor["description"])


def run(api, client, device, words, timeout, json_output):
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
        return 1 if data.get("application", {}).get("state") == "failed" else 0
    finally:
        client.timeout = previous
