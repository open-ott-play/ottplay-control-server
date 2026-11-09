"""Negotiated, conditional credential writes to explicit native provider slots."""
import copy
import json
import re
import time


OPERATORS = frozenset((
    "antifriz", "kb-team", "itv", "ottclub", "shura", "1ott", "bestlist/stalker", "only4",
    "shara-tv", "tvteam", "all4you", "bestlist", "cbilling", "d/maxtv", "diamondtv", "dosug",
    "dragon", "drvao", "fabryka", "fox", "fxml", "great", "ipstream", "iptv-ott.ru", "korona",
    "moidom", "newlook", "ottg", "ottprime", "polmedia", "prost", "raduga", "rd", "russkoetv",
    "shara.club", "sharavoz", "shocktv", "tabox", "top", "topiptv", "tvclub", "ultifl1x", "vidok",
))
PROVIDERS = OPERATORS | {"edem"}
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESULT_BYTES = 2048
USAGE = "Use provider-config FILE.json, provider-config --check FILE.json or provider-config --schema [PROVIDER]"
REASONS = {
    "invalid_request": "The player rejected the provider configuration request",
    "unsupported": "The player does not support this provider configuration operation",
    "policy_restricted": "The player's settings policy prevents this operation",
    "runtime_changed": "The player restarted; read the profile again",
    "source_changed": "The provider or profile changed; read it again",
    "revision_conflict": "The profile changed concurrently; read it again",
    "invalid_value": "The provider configuration values are invalid",
    "storage_failed": "The player could not verify the saved provider configuration",
    "busy": "Another player operation is in progress",
    "expired": "The provider configuration request expired",
}


def require(value):
    if not value:
        raise ValueError("Invalid native provider configuration")


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def text(value, maximum):
    try:
        return (isinstance(value, str) and len(value.encode("utf-8")) <= maximum
                and all(ord(char) >= 32 and not 127 <= ord(char) <= 159 for char in value))
    except UnicodeError:
        return False


def token(value):
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def capability(value):
    exact(value, ("version", "operations", "providers"))
    require(integer(value["version"], 1, 1))
    operations = value["operations"]
    require(type(operations) is list and len(operations) == 2
            and all(isinstance(operation, str) for operation in operations)
            and set(operations) == {"compare", "set"})
    providers = value["providers"]
    require(type(providers) is list and 1 <= len(providers) <= len(PROVIDERS))
    require(all(isinstance(provider, str) and provider in PROVIDERS for provider in providers))
    require(len(set(providers)) == len(providers))
    return {"version": 1, "operations": ["compare", "set"], "providers": list(providers)}


def settings(provider, value):
    require(isinstance(provider, str) and provider in PROVIDERS)
    allowed = {"key", "list", "portal"} if provider == "edem" else {
        "server", "username", "password", "playlist", "mac", "list", "mode"}
    require(type(value) is dict and value and set(value) <= allowed)
    for key, item in value.items():
        if key in ("list", "mode"):
            require(integer(item, 0, 3 if key == "list" else 2))
        elif key == "key":
            require(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,512}", item) is not None)
        else:
            require(text(item, 8192))
    return copy.deepcopy(value)


def document(value, check=False):
    exact(value, ("provider", "number", "settings"))
    require(integer(value["number"], 1, 15))
    patch = settings(value["provider"], value["settings"])
    return {"operation": "compare" if check else "set", "provider": value["provider"],
            "number": value["number"], "settings": patch}


def schema_request(words):
    require(len(words) <= 1 and (not words or isinstance(words[0], str) and words[0] in PROVIDERS))
    return {"operation": "schema", "provider": words[0] if words else None}


def field_schema(provider):
    if provider == "edem":
        return {
            "key": {"type": "string", "max_utf8_bytes": 512, "pattern": "[A-Za-z0-9_-]{1,512}"},
            "list": {"type": "integer", "minimum": 0, "maximum": 3},
            "portal": {"type": "string", "max_utf8_bytes": 8192, "validation": "empty or player-validated VPortal link"},
        }
    return {**{key: {"type": "string", "max_utf8_bytes": 8192} for key in
               ("server", "username", "password", "playlist", "mac")},
            "list": {"type": "integer", "minimum": 0, "maximum": 3},
            "mode": {"type": "integer", "minimum": 0, "maximum": 2}}


def schema(marker, provider):
    providers = [provider] if provider is not None else marker["providers"]
    require(all(value in marker["providers"] for value in providers))
    families = {}
    for value in providers:
        family = "edem" if value == "edem" else "operator"
        if family not in families:
            families[family] = {"providers": [], "fields": field_schema(value)}
        families[family]["providers"].append(value)
    return {"version": 1, "operation": "schema", "providers": list(providers), "schemas": families}


def metadata(value, params, write=False, rejected=False):
    require(encoded_size(value) <= MAX_RESULT_BYTES)
    identity = {"version", "runtime", "provider", "number"}
    if rejected:
        exact(value, identity | {"reason"})
    elif write:
        exact(value, identity | {"context", "revision", "saved", "fields", "reloaded", "reload_required"})
    else:
        exact(value, identity | {"context", "revision", "matched"})
    require(integer(value["version"], 1, 1) and integer(value["number"], params["number"], params["number"]))
    require(all(value[key] == params[key] for key in ("runtime", "provider")))
    if rejected:
        require(isinstance(value["reason"], str) and value["reason"] in REASONS)
    else:
        require(token(value["context"]) and token(value["revision"]))
        if write:
            require(value["context"] == params["context"] and value["revision"] == params["revision"])
            require(value["saved"] is True and type(value["reloaded"]) is bool
                    and type(value["reload_required"]) is bool and not (value["reloaded"] and value["reload_required"]))
            fields = value["fields"]
            require(type(fields) is list and all(isinstance(field, str) for field in fields)
                    and len(fields) == len(params["settings"]) and set(fields) == set(params["settings"]))
        else:
            require(type(value["matched"]) is bool)
    return copy.deepcopy(value)


def encoded_size(value):
    try:
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Invalid native provider configuration") from None
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                               ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        value = value.replace(character, escaped)
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError("Invalid native provider configuration") from None


def request_size(action, params):
    # Include the controller's 32-hex request ID and conservatively reserve an
    # expiry spelling. Go's JSON encoder also escapes HTML and line separators.
    return encoded_size({"id": "0" * 32, "expires_at": "0" * 32, "action": action, "params": params})


def _unknown(api, message, error=None):
    if isinstance(error, api.RequestError):
        safe = api.request_error_metadata(error)
        result = api.RequestError(safe["reason"], safe["stage"], message, safe["request_id"],
                                  transport_category=safe.get("transport_category"))
        result.action = safe["action"]
        return result
    return api.Error(message)


def run(api, client, device, params, timeout):
    previous_timeout = client.timeout
    deadline = time.monotonic() + timeout

    def call(action, request, write=False, probe=False):
        cancelled = getattr(client, "group_cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise KeyboardInterrupt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise api.Error("Provider configuration exceeded --timeout; no save was sent")
        try:
            size = request_size(action, request)
        except (ValueError, TypeError, UnicodeError):
            raise api.Error("Invalid native provider configuration; no save was sent") from None
        if size > MAX_REQUEST_BYTES:
            raise api.Error("Provider configuration request exceeds 16 KiB; no save was sent")
        client.timeout = remaining
        try:
            data = client.call(device, action, request)
        except (api.PlayerRejected, api.PlayerUnsupported) as error:
            if probe:
                raise api.Error("Native provider configuration is not supported by this player/controller; no credentials or save were sent") from None
            try:
                rejected = metadata(error.data, request, write=write, rejected=True)
            except (ValueError, TypeError, KeyError, UnicodeError):
                suffix = "Save outcome is unknown; inspect the profile before another write. The request was not repeated." if write else "No save was sent."
                raise api.Error("The player returned an invalid provider configuration rejection. " + suffix) from None
            raise api.Error(REASONS[rejected["reason"]] + ". " +
                            ("The request was not repeated." if write else "No save was sent.")) from None
        except api.HTTPError as error:
            if probe:
                raise api.Error("Native provider capability negotiation failed; no credentials or save were sent") from None
            if error.code < 500:
                raise api.Error("The controller refused the provider configuration request. " +
                                ("The request was not repeated." if write else "No save was sent.")) from None
            message = ("Provider configuration save outcome is unknown; inspect the profile before another write. The request was not repeated."
                       if write else "Provider configuration could not be confirmed; no save was sent.")
            raise _unknown(api, message, error) from None
        except api.Error as error:
            message = ("Provider configuration save outcome is unknown; inspect the profile before another write. The request was not repeated."
                       if write else "Provider configuration could not be confirmed; no save was sent.")
            raise _unknown(api, message, error) from None
        except Exception:
            message = ("Provider configuration save outcome is unknown; inspect the profile before another write. The request was not repeated."
                       if write else "Provider configuration could not be confirmed; no save was sent.")
            raise api.Error(message) from None
        if time.monotonic() >= deadline:
            message = ("Provider configuration save exceeded --timeout; its outcome is unknown. Inspect the profile before another write; the request was not repeated."
                       if write else "Provider configuration exceeded --timeout; no save was sent.")
            raise api.Error(message)
        return data

    try:
        raw = call("capabilities", {"native_provider_settings": True}, probe=True)
        try:
            controls = api.capabilities_metadata(raw)
            marker = capability(controls.get("native_provider_settings"))
        except (api.Error, ValueError, TypeError, KeyError, UnicodeError):
            raise api.Error("The player did not advertise valid native provider configuration support; no credentials or save were sent") from None
        if params["operation"] == "schema":
            try:
                return schema(marker, params["provider"])
            except ValueError:
                raise api.Error("This provider is not advertised for native configuration; no credentials or save were sent") from None
        if params["provider"] not in marker["providers"]:
            raise api.Error("This provider is not advertised for native configuration; no credentials or save were sent")
        request = {"version": 1, "runtime": controls["player"]["runtime"],
                   **{key: copy.deepcopy(params[key]) for key in ("provider", "number", "settings")}}
        compared = call("settings_compare", request)
        try:
            compared = metadata(compared, request)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise api.Error("The player did not confirm the exact provider configuration comparison; no save was sent") from None
        if params["operation"] == "compare" or compared["matched"]:
            return compared
        request.update({key: compared[key] for key in ("context", "revision")})
        saved = call("provider_settings", request, write=True)
        try:
            return metadata(saved, request, write=True)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise api.Error("The player did not confirm the exact provider configuration save. Its outcome is unknown; inspect the profile before another write. The request was not repeated.") from None
    finally:
        client.timeout = previous_timeout


def print_result(data):
    if data.get("operation") == "schema":
        print("Native provider configuration: explicit profile number 1–15; omitted fields are preserved.")
        for family in data["schemas"].values():
            print("Providers: " + ", ".join(family["providers"]))
            for name, field in family["fields"].items():
                bound = (str(field["minimum"]) + "–" + str(field["maximum"]) if field["type"] == "integer"
                         else "up to " + str(field["max_utf8_bytes"]) + " UTF-8 bytes; no control characters")
                print("  " + name + ": " + field["type"] + "; " + bound)
                if "pattern" in field:
                    print("    Required pattern: " + field["pattern"])
                if "validation" in field:
                    print("    " + field["validation"])
        return
    label = data["provider"] + " profile " + str(data["number"])
    if "matched" in data:
        print(label + (": supplied settings match; no save was sent." if data["matched"]
                       else ": supplied settings differ; no save was sent."))
    else:
        print(label + ": settings saved (" + ", ".join(data["fields"]) + ").")
        if data["reloaded"]:
            print("The player reports that the active source was reloaded; playback is not confirmed.")
        elif data["reload_required"]:
            print("The player reports that reloading the source is still required.")
