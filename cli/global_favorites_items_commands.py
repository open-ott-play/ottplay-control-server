"""Ordinary favorites use global receiver IDs; add/remove reuse core transfer."""
import copy
import math
import time

import favorites_commands as shared
import global_favorites_commands as global_commands
import global_favorites_document as document
import global_favorites_items as items
import global_favorites_remote as core


def parse(api, words):
    if len(words) == 1 and words[0].casefold() in ("list", "current"):
        return {"op": words[0].casefold()}
    if words and words[0].casefold() == "play":
        return api.parse_favorites(words)
    raise api.Error("Use fav global list, fav global current or fav global play ID_OR_NAME")


def _select(api, params, listing):
    rows = listing.data["entries"]
    value = params.get("id", params.get("query", ""))
    if api.FAVORITES_UUID_V4.fullmatch(value):
        matches = [row for row in rows if row["id"] == value]
    elif "id" in params:
        raise shared.CommandError("legacy_favorite_id_requires_fav_legacy")
    else:
        matches = [row for row in rows if row["entry"]["title"] == value]
        if not matches:
            matches = [row for row in rows if row["entry"]["title"].casefold() == value.casefold()]
    if len(matches) != 1:
        raise shared.CommandError("ambiguous_global_favorite" if matches else "global_favorite_not_found")
    return matches[0]


def run(api, client, device, params, controls, config_path, deadline, *, monotonic=time.monotonic,
        sleep=time.sleep):
    """Use already validated fresh metadata and the caller's whole deadline."""
    server, last = client.server, None
    def remaining():
        nonlocal last
        now = monotonic()
        if (type(now) not in (int, float) or not math.isfinite(now) or now < 0
                or client.server != server or last is not None and now < last):
            raise shared.CommandError("controller_or_clock_changed")
        last = now
        value = deadline - now
        if value <= 0:
            raise shared.CommandError("deadline_exceeded")
        return value
    def space():
        wait_until = monotonic() + shared.remote.POST_INTERVAL
        while monotonic() < wait_until:
            budget = remaining()
            before = monotonic()
            sleep(min(wait_until - before, budget))
            if monotonic() <= before:
                raise shared.CommandError("invalid_clock")
        remaining()
    try:
        if "favorites_global_items" not in controls:
            raise shared.CommandError("global_favorites_items_unsupported")
        items.validate_capability(controls["favorites_global_items"])
        runtime = controls["player"]["runtime"]
        space()
        peer = items.ItemsPeer(client, device, runtime=runtime, timeout=remaining(),
                               monotonic=monotonic, sleep=sleep, deadline=deadline)
        operation = params["op"]
        base = {"collection": "global", "op": operation, "runtime": runtime}
        if operation in ("add", "current"):
            captured = peer.current().data
            remaining()
            if operation == "current":
                return {**base, "status": "ready" if captured["entry"] is not None else "unavailable",
                        "entry": captured["entry"], "unavailable_reason": captured["unavailable_reason"]}
            if captured["entry"] is None:
                raise shared.CommandError(captured["unavailable_reason"])
            selected = copy.deepcopy(captured["entry"])
            if "name" in params:
                selected["title"] = params["name"]
            incoming = document.validate_document({"version": 1, "entries": [selected]})
            fence = captured
        elif operation in ("list", "play", "remove"):
            listing = peer.list()
            remaining()
            if operation == "list":
                return {**base, **listing.data, "op": "list", "collection": "global", "status": "ready"}
            selected_row = _select(api, params, listing)
            if operation == "play":
                played = peer.play(selected_row["id"], listing).data
                remaining()
                return {**base, "status": "dispatched", "id": played["id"], "entry": selected_row["entry"],
                        "dispatched": True, "playback_confirmed": False}
            fence = listing.data
        else:
            raise shared.CommandError("invalid_arguments")
        return _change(api, client, device, params, controls, config_path, deadline, monotonic, sleep,
                       remaining, space, base, runtime, fence, incoming if operation == "add" else None,
                       listing if operation == "remove" else None, selected_row if operation == "remove" else None)
    except api.Error:
        raise
    except (shared.CommandError, core.RemoteError, document.DocumentError, items.ProtocolError) as error:
        raise api.Error(str(error)) from None
    except Exception:  # noqa: BLE001 - provider data and private callback errors must not escape
        raise api.Error("Global favorite could not be verified; no fallback or automatic replay was attempted") from None


def _change(api, client, device, params, controls, config_path, deadline, clock, sleep,
            remaining, space, base, runtime, fence, incoming, listing, selected):
    if "favorites_global" not in controls or config_path is None:
        raise shared.CommandError("global_favorites_transfer_required")
    global_commands.protocol.validate_capability(controls["favorites_global"])
    target = global_commands._Target(api, client, config_path, device, remaining(), clock, sleep, None)
    target.deadline = min(target.deadline, deadline)
    target.runtime, target.capabilities = runtime, controls
    space()
    target.peer = core.RemoteGlobalFavorites(client, device, runtime=runtime, save_context=target.durable.save,
        timeout=remaining(), monotonic=clock, sleep=sleep, deadline=deadline)
    snapshot = target.peer.export()
    remaining()
    if any(snapshot.description.data[key] != fence[key] for key in ("revision", "context_revision")):
        raise shared.CommandError("global_favorites_changed")
    mode = "merge"
    if params["op"] == "remove":
        if document.canonical_bytes(listing.document) != document.canonical_bytes(snapshot.document):
            raise shared.CommandError("global_favorites_changed")
        identity = document.identity(selected["entry"])
        entries = [row for row in snapshot.document["entries"] if document.identity(row) != identity]
        if len(entries) != len(snapshot.document["entries"]) - 1:
            raise shared.CommandError("global_favorites_changed")
        incoming, mode = document.validate_document({"version": 1, "entries": entries}), "replace"
    plan = document.plan_documents(snapshot.document, [incoming], mode)
    conflict = global_commands.conflict_result(plan, [incoming])
    if conflict is not None:
        return {**base, **conflict}
    target.require_write_ready()
    remaining()
    result = target.peer.apply(snapshot, incoming, mode)
    return {**base, **global_commands._outcome(target, result)}


def print_result(api, data):
    if data["op"] == "list":
        print(f"Global favorites: {data['total']}.")
        for row in data["entries"]:
            entry = row["entry"]
            origin = entry["origin"]
            source = " / ".join(api.clean(origin[key]) for key in ("provider", "profile", "subprofile", "sourceAlias")
                                if origin[key] is not None)
            print(f"  {row['id']}  {api.clean(entry['title'])} ({source})")
        if data["unresolved_legacy_count"]:
            print(f"Legacy-only favorites: {data['unresolved_legacy_count']}; use fav legacy list to inspect them.")
    elif data["op"] == "current":
        if data["entry"] is None:
            print("Current global favorite unavailable: " + data["unavailable_reason"].replace("_", " ") + ".")
        else:
            print("Current portable item: " + api.clean(data["entry"]["title"]) + ". Nothing was saved.")
    elif data["op"] == "play":
        print("Global favorite playback dispatched: " + api.clean(data["entry"]["title"]) +
              ". Visible playback is not confirmed.")
    else:
        global_commands.print_result(data)
