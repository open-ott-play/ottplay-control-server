"""Local named player groups; expansion reuses the existing group dispatcher."""
import json
import re


HELP = """ott groups                         list saved player groups
  ott group show NAME                show one group's members
  ott group add NAME PLAYER[,PLAYER...] [PLAYER...]
                                      create a group from saved player aliases
  ott group set NAME PLAYER[,PLAYER...] [PLAYER...]
                                      create or replace a group
  ott group remove NAME              remove only the group
  ott NAME COMMAND...                run a player command on the group
  ott NAME,PLAYER COMMAND...         combine groups and individual players

Examples:
  ott group set tt t1,t2,t3,t4
  ott group set oo o1,o2,o3,o4
  ott tt R
  ott oo provider m3u
  ott tt,oo +10s

Groups are saved in the selected CLI configuration. Management is local and
does not contact players. Names are case-insensitive. Members must be saved
player aliases or their configured device IDs; nested groups are not supported.
Up to 64 members may be expanded per command. Each distinct device receives
the command once, in first-occurrence order. Per-player restrictions still apply.
"""
NAME = re.compile(r"[a-zA-Z0-9а-яА-ЯёЁ_-]{1,32}\Z")


def group_map(api, config):
    if not isinstance(config, dict):
        raise api.Error("The CLI configuration must be a JSON object")
    groups = config.get("groups", {})
    if (not isinstance(groups, dict) or len(groups) > 128
            or any(not isinstance(name, str) or not NAME.fullmatch(name)
                   or name.casefold() in api.TOP_LEVEL_COMMANDS for name in groups)
            or len({name.casefold() for name in groups}) != len(groups)):
        raise api.Error("Invalid player groups: use unique, non-reserved names of up to 32 letters, digits, underscores or hyphens")
    return groups


def group_name(api, config, name):
    return next((key for key in group_map(api, config) if key.casefold() == name.casefold()), None)


def members(api, value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 64
            or any(not isinstance(item, str) or not item or item != item.strip()
                   or len(item) > 512 or "," in item or not item.isprintable() for item in value)):
        raise api.Error("A player group requires 1–64 nonempty player names, without commas inside a member")
    return value


def check_alias(api, config, name):
    if group_name(api, config, name) is not None:
        raise api.Error("This name belongs to a player group; choose a different player alias")


def targets(api, client, selector):
    """Resolve every member before dispatch; preserve legacy literal aliases."""
    group = group_name(api, client.config, selector)
    if "," not in selector and group is None:
        return None
    aliases = client.config.get("players", {})
    devices = client.credentials.get("devices", [])
    if (not isinstance(aliases, dict) or any(not isinstance(name, str) for name in aliases)
            or not isinstance(devices, list) or any(not isinstance(row, dict)
                or not isinstance(row.get("id"), str) for row in devices)):
        raise api.Error("Check the configured player names and device UUIDs")
    identities = [*aliases, *aliases.values(), *(row["id"] for row in devices)]

    def is_identity(name):
        return any(isinstance(value, str) and value.casefold() == name.casefold() for value in identities)

    if is_identity(selector):
        if group is not None:
            raise api.Error("The group name conflicts with a player alias or device ID")
        return None
    names = [name.strip() for name in selector.split(",")]
    if any(not name for name in names):
        raise api.Error("Player lists require a name between every comma; use t1,t2,t3,t4")
    if len(names) > 64:
        raise api.Error("Player lists support at most 64 names")
    expanded = []
    groups = group_map(api, client.config)
    for name in names:
        key = group_name(api, client.config, name)
        if key is not None:
            if is_identity(name):
                raise api.Error("The group name conflicts with a player alias or device ID")
            values = members(api, groups[key])
            if any(group_name(api, client.config, item) is not None for item in values):
                raise api.Error("Nested player groups are not supported; list player aliases instead")
            expanded.extend(values)
        else:
            expanded.append(name)
        if len(expanded) > 64:
            raise api.Error("Player lists and groups support at most 64 expanded names")
    result, seen = [], set()
    for name in expanded:
        device = client.device(name)
        if not isinstance(device, str) or not device:
            raise api.Error("Check the configured player UUIDs")
        if device not in seen:
            seen.add(device)
            result.append((name, device))
    return result


def saved_members(api, config, words):
    names = members(api, [name.strip() for word in words for name in word.split(",")])
    aliases = config.get("players", {})
    if (not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                           or not value for key, value in aliases.items())):
        raise api.Error("Check the configured player names and device UUIDs")
    result, seen = [], set()
    for name in names:
        if group_name(api, config, name) is not None:
            raise api.Error("Nested player groups are not supported; list player aliases instead")
        keys = [key for key in aliases if key.casefold() == name.casefold()]
        ids = list(dict.fromkeys(value for value in aliases.values() if value.casefold() == name.casefold()))
        if len(keys) == 1:
            label, device = keys[0], aliases[keys[0]]
        elif not keys and len(ids) == 1:
            # Store an alias so normal Client.device resolution works even when
            # an operator's credential file does not include the device list.
            device = ids[0]
            label = next(key for key, value in aliases.items() if value == device)
        else:
            raise api.Error(f"Unknown or ambiguous group member {name!r}; use a saved player alias")
        members(api, [label])
        if device not in seen:
            seen.add(device)
            result.append(label)
    return result


def management(api, config_path, words, json_output=False):
    tail = list(words[1:])
    if tail in (["--help"], ["-h"], ["help"]):
        print(HELP)
        return 0
    if tail and tail[-1] in ("-j", "--json"):
        json_output, tail = True, tail[:-1]
    op = tail[0].casefold() if tail else "list"
    if ((op == "list" and len(tail) > 1)
            or (op in ("show", "remove") and len(tail) != 2)
            or (op in ("add", "set") and len(tail) < 3)
            or op not in ("list", "show", "remove", "add", "set")):
        raise api.Error("Use ott groups or ott group add|set|show|remove NAME; see ott group --help")
    config = api.read_json(config_path)
    if not isinstance(config, dict):
        raise api.Error("The CLI configuration must be a JSON object")
    groups = dict(group_map(api, config))
    if op == "list":
        data = {"groups": {key: members(api, value) for key, value in groups.items()}}
    else:
        name = tail[1]
        key = group_name(api, config, name)
        if op in ("show", "remove") and key is None:
            raise api.Error(f"Unknown player group {name!r}; run ott groups")
        if op in ("add", "set"):
            if not NAME.fullmatch(name) or name.casefold() in api.TOP_LEVEL_COMMANDS:
                raise api.Error("Choose a non-reserved group name of up to 32 letters, digits, underscores or hyphens")
            aliases = config.get("players", {})
            if not isinstance(aliases, dict):
                raise api.Error("Check the configured player names")
            if any(isinstance(value, str) and value.casefold() == name.casefold()
                   for value in [*aliases, *aliases.values()]):
                raise api.Error("The group name conflicts with a player alias or device ID")
            if op == "add" and key is not None:
                raise api.Error("This group already exists; use group set to replace its members")
            key = key or name
            groups[key] = saved_members(api, config, tail[2:])
            group_map(api, {"groups": groups})
        data = {"group": key, "players": groups[key]}
        if op == "remove":
            del groups[key]
            data = {"removed": key}
        elif op == "show":
            members(api, data["players"])
    if op in ("add", "set", "remove"):
        if api.read_json(config_path) != config:
            raise api.Error("The CLI configuration changed; no group was saved")
        api.write_private(config_path, {**config, "groups": groups})
    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif op == "list":
        print("\n".join(key + ": " + ", ".join(value) for key, value in data["groups"].items())
              or "No player groups. Use ott group add NAME PLAYER[,PLAYER...]")
    elif op == "remove":
        print("Player group removed: " + data["removed"])
    else:
        print(data["group"] + ": " + ", ".join(data["players"]))
    return 0
