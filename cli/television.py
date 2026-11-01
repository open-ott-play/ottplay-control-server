"""Typed, server-owned television controls independent of player presence."""
import json
import math
import re
import shlex
import sys
import urllib.parse

ACTIONS = frozenset(("status", "volume", "mute", "on", "off", "launch", "go", "apps", "pair"))
TRANSPORTS = frozenset(("webos", "home_assistant", "wake_on_lan"))
OUTCOMES = frozenset(("confirmed", "accepted", "uncertain"))
ERRORS = {
    "invalid_command": "The controller rejected the television command parameters",
    "invalid_configuration": "The controller's television binding is invalid",
    "not_configured": "No television is bound to this player on the controller",
    "launch_not_configured": "No OttPlay app or URL is configured for this television",
    "credential_unavailable": "The television pairing credential is unavailable on the controller",
    "credential_invalid": "The television pairing credential is invalid",
    "not_paired": "The television is not paired; use tv pair while its screen is on",
    "pairing_required": "Accept the connection request on the television, then use tv pair",
    "pairing_rejected": "The television rejected pairing",
    "pairing_timeout": "Pairing was not completed before the deadline",
    "direct_unavailable": "The direct television connection is unavailable",
    "unavailable": "The television is unavailable through its configured control routes",
    "connection_failed": "The television connection failed",
    "certificate_required": "Pair the television to save its TLS certificate identity",
    "certificate_mismatch": "The television certificate differs from its saved identity; verify the television before pairing again",
    "protocol_error": "The television returned an invalid protocol response",
    "command_rejected": "The television rejected the requested operation",
    "unsupported": "The television does not support this operation",
    "fallback_unavailable": "The configured Home Assistant fallback is unavailable",
    "fallback_target_invalid": "The Home Assistant fallback is not a valid native television entity",
    "timeout": "The television operation exceeded its deadline",
    "deadline": "The television operation exceeded its deadline",
    "operation_uncertain": "The result of the television operation could not be established",
    "wake_unavailable": "Wake-on-LAN could not be sent from the controller",
    "not_ready": "The television did not become ready before the deadline",
    "busy": "Another television operation is in progress",
    "invalid_response": "The television returned an invalid response",
}


def help_text(name="NAME"):
    prefix = "ott " + shlex.quote(name) + " tv"
    return f"""Television controls (independent of the OttPlay page):
  {prefix} [status]              read television power, volume and active app
  {prefix} volume [N|+N|-N]      read/set system volume (0..100), or change it
  {prefix} mute on|off           set system mute explicitly
  {prefix} on / wake             wake the television
  {prefix} off                   switch off the television
  {prefix} launch                open the configured OttPlay app or URL
  {prefix} go / start            wake, wait for the TV, then open OttPlay
  {prefix} apps                  list applications reported by the television
  {prefix} pair                  pair once; accept the request on the TV

Direct webOS control is preferred; Home Assistant is a configured fallback.
Player standby/go, restart, exit and volume keep their existing meanings.
Use ott -t 90 {shlex.quote(name)} tv pair or tv go for a longer deadline (maximum 120s).
Lists/groups work for television controls; pairing requires one television.
Accepted means submitted, not observed. An uncertain change is never retried.
"""


def parse(api, words):
    words = list(words)
    op = words[0].casefold() if words else "status"
    tail = words[1:]
    op = {"wake": "on", "start": "go", "vol": "volume", "v": "volume"}.get(op, op)
    if op in ("help", "-h", "--help") and not tail:
        return None
    if op in ("status", "on", "off", "launch", "go", "apps", "pair") and not tail:
        return {"action": op}
    if op == "volume":
        if not tail:
            return {"action": "status"}
        if len(tail) == 1 and re.fullmatch(r"[+-]?(?:0|[1-9][0-9]{0,2})", tail[0]):
            value = int(tail[0])
            relative = tail[0][0] in "+-"
            if (relative and -100 <= value <= 100 and value != 0) or (not relative and 0 <= value <= 100):
                return {"action": "volume", "delta" if relative else "volume": value}
    if op == "mute" and len(tail) == 1 and tail[0].casefold() in ("on", "off"):
        return {"action": "mute", "muted": tail[0].casefold() == "on"}
    raise api.Error("Use tv status, volume [0..100|+N|-N], mute on|off, on, off, launch, go, apps or pair")


def require(api, condition, mutating=False):
    if not condition:
        suffix = ". The request may have executed; do not repeat the change blindly" if mutating else ""
        raise api.Error("The controller returned invalid television metadata" + suffix)


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value) is not None


def label(value, limit=160):
    return (isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and "://" not in value and not re.search(r"\b(?:token|password|authorization)=", value, re.IGNORECASE))


def binding_metadata(api, raw, device):
    require(api, isinstance(raw, dict) and raw.get("version") == 1 and type(raw.get("version")) is int
            and raw.get("device_id") == device and type(raw.get("configured")) is bool)
    result = {"version": 1, "device_id": device, "configured": raw["configured"]}
    caps = raw.get("capabilities", [])
    require(api, isinstance(caps, list) and len(caps) <= len(ACTIONS)
            and all(isinstance(x, str) and x in ACTIONS for x in caps) and len(set(caps)) == len(caps))
    result["capabilities"] = list(caps)
    for key in ("paired", "fallback_configured"):
        if key in raw:
            require(api, type(raw[key]) is bool)
            result[key] = raw[key]
    return result


def result_metadata(api, raw, device, action, params=None):
    mutating = action not in ("status", "apps")
    require(api, isinstance(raw, dict) and type(raw.get("version")) is int and raw["version"] == 1
            and raw.get("device_id") == device and raw.get("action") == action, mutating)
    if "code" in raw:
        require(api, isinstance(raw["code"], str) and type(raw.get("uncertain")) is bool, mutating)
        message = ERRORS.get(raw["code"], "The television operation failed")
        if raw["uncertain"]:
            message += ". The request may have executed; inspect the television before another change"
        raise api.Error(message + ". The request was not repeated")
    require(api, isinstance(raw.get("transport"), str) and raw["transport"] in TRANSPORTS
            and isinstance(raw.get("outcome"), str) and raw["outcome"] in OUTCOMES, mutating)
    result = {"version": 1, "device_id": device, "action": action,
              "transport": raw["transport"], "outcome": raw["outcome"]}
    if "state" in raw:
        state = raw["state"]
        require(api, isinstance(state, dict), mutating)
        projected = {}
        if "power" in state:
            require(api, state["power"] in ("on", "off", "standby", "unknown"), mutating)
            projected["power"] = state["power"]
        if "volume" in state:
            value = state["volume"]
            require(api, type(value) in (int, float) and 0 <= value <= 100 and math.isfinite(value), mutating)
            projected["volume"] = value
        if "muted" in state:
            require(api, type(state["muted"]) is bool, mutating)
            projected["muted"] = state["muted"]
        if "app_id" in state:
            require(api, identifier(state["app_id"]), mutating)
            projected["app_id"] = state["app_id"]
        result["state"] = projected
    if "pairing" in raw:
        require(api, action == "pair" and isinstance(raw["pairing"], dict)
                and type(raw["pairing"].get("paired")) is bool, mutating)
        result["pairing"] = {"paired": raw["pairing"]["paired"]}
    if raw.get("apps") is not None:
        apps = raw["apps"]
        require(api, action == "apps" and isinstance(apps, list) and len(apps) <= 512, mutating)
        result["apps"] = []
        seen = set()
        for app in apps:
            require(api, isinstance(app, dict) and identifier(app.get("id")) and label(app.get("title"))
                    and app["id"] not in seen, mutating)
            seen.add(app["id"])
            result["apps"].append({"id": app["id"], "title": app["title"]})
    require(api, action != "apps" or "apps" in result, mutating)
    require(api, action != "status" or bool(result.get("state")), mutating)
    require(api, action != "pair" or "pairing" in result, mutating)
    if result["outcome"] == "confirmed":
        state, params = result.get("state", {}), params or {}
        if action == "volume":
            require(api, "volume" in state and ("volume" not in params or state["volume"] == params["volume"]), True)
        elif action == "mute":
            require(api, "muted" in state and ("muted" not in params or state["muted"] == params["muted"]), True)
        elif action in ("on", "off"):
            require(api, state.get("power") == action, True)
        elif action in ("launch", "go"):
            require(api, "app_id" in state, True)
        elif action == "pair":
            require(api, result["pairing"]["paired"], True)
    return result


def read_binding(api, client, device, timeout=2):
    try:
        status, raw = client.api("/api/televisions?" + urllib.parse.urlencode({"device_id": device}), timeout=timeout)
    except api.HTTPError as exc:
        if exc.code in (404, 405):
            return None
        raise
    require(api, status == 200)
    return binding_metadata(api, raw, device)


def overview_help(api, client, device, name):
    """Optional bounded discovery must not break an older player's overview."""
    try:
        binding = read_binding(api, client, device, min(2, client.timeout))
    except (api.Error, TypeError, ValueError, AttributeError):
        return
    if binding and binding["configured"]:
        prefix = "ott " + shlex.quote(name) + " tv"
        print("Television controls (available independently of the player):")
        print(f"  {prefix} status / volume [N|+N|-N] / mute on|off")
        print(f"  {prefix} on / off / launch / go / apps / pair")
        print(f"  {prefix} --help  — television power and system volume; player controls stay separate")
        print()


def print_result(result):
    transport = {"webos": "direct webOS", "home_assistant": "Home Assistant fallback", "wake_on_lan": "Wake-on-LAN"}[result["transport"]]
    print(f"Television {result['action']}: {result['outcome']} ({transport})")
    state = result.get("state", {})
    for key, title in (("power", "Power"), ("volume", "System volume"), ("muted", "Muted"), ("app_id", "Active app")):
        if key in state:
            value = state[key]
            if key == "volume":
                value = f"{value:g}%"
            elif key == "muted":
                value = "yes" if value else "no"
            print(f"  {title}: {value}")
    if "pairing" in result:
        print("  Pairing: " + ("paired" if result["pairing"]["paired"] else "not completed"))
    for app in result.get("apps", []):
        print(f"  {app['id']}: {app['title']}")
    if result["outcome"] == "accepted":
        print("The command was submitted; the final television state has not been confirmed.")


def run(api, client, device, name, words, timeout, json_output=False):
    params = parse(api, words)
    if params is None:
        print(help_text(name))
        return 0
    if type(timeout) not in (int, float) or not 1 <= timeout <= 120 or not math.isfinite(timeout):
        raise api.Error("Television --timeout must be between 1 and 120 seconds")
    action = params["action"]
    if action == "pair":
        print("Pairing television: accept the connection request on its screen.", file=sys.stderr, flush=True)
    # Leave room for the controller's deadline/receipt to reach this caller.
    operation_timeout = timeout - min(1, timeout * 0.1)
    payload = {"device_id": device, **params, "timeout_ms": max(1, int(operation_timeout * 1000))}
    try:
        status, raw = client.api("/api/televisions", payload, timeout=timeout, timeout_cap=120)
    except api.HTTPError as exc:
        if exc.code in (404, 405):
            raise api.Error("This controller does not support television controls; update the controller") from None
        suffix = ". The request may have executed; inspect the television before another change" if action not in ("status", "apps") else ""
        raise api.Error("Television control request failed (HTTP " + str(exc.code) + ")" + suffix + ". The request was not repeated") from None
    except api.Error:
        suffix = ". The request may have executed; inspect the television before another change" if action not in ("status", "apps") else ""
        raise api.Error("Could not complete the television request" + suffix + ". The request was not repeated") from None
    require(api, status == 200, action not in ("status", "apps"))
    result = result_metadata(api, raw, device, action, params)
    if json_output:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    else:
        print_result(result)
    if result["outcome"] == "uncertain":
        print("The final state is uncertain; inspect the television before another change. The request was not repeated.", file=sys.stderr)
        return 1
    return 0
