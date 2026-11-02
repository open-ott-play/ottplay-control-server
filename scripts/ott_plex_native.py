#!/usr/bin/env python3
"""Bounded, fixed-target Plex Companion operations for the native LG shortcut."""
import json
import logging
import math
import os
import re
import stat
import sys
import time
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree

COLLECTION = "MultCocomelonAll"
MAX_ITEMS = 500
MAX_RESPONSE = 8 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")


class AdapterError(Exception):
    pass


class PlayerRejected(AdapterError):
    def __init__(self, code):
        self.code = code
        super().__init__("player_rejected")


def positive_id(value):
    if type(value) is int and 0 < value < 2 ** 53:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,15}", value) and int(value) < 2 ** 53:
        return int(value)
    return None


def config_value(value):
    expected = {"plex_url", "plex_token_file", "plex_client_id", "plex_server_id"}
    if not isinstance(value, dict) or set(value) != expected or any(not isinstance(v, str) for v in value.values()):
        raise AdapterError("invalid_configuration")
    url = urllib.parse.urlsplit(value["plex_url"])
    # PlexAPI's playMedia parses the origin into host/port. Restrict this
    # adapter to explicit HTTP(S) origins rather than ambiguous URL prefixes.
    if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment or url.path not in ("", "/")
            or url.port is None or not 1 <= url.port <= 65535 or ":" in url.hostname
            or re.search(r"[\s\\]", value["plex_url"])):
        raise AdapterError("invalid_configuration")
    if not all(IDENTIFIER.fullmatch(value[key]) for key in ("plex_client_id", "plex_server_id")):
        raise AdapterError("invalid_configuration")
    token = Path(value["plex_token_file"]).expanduser()
    if not token.is_absolute():
        raise AdapterError("invalid_configuration")
    return dict(value, plex_url=value["plex_url"].rstrip("/"), plex_token_file=str(token))


def read_token(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 4096):
            raise AdapterError("credential_invalid")
        value = stream.read(4097).strip()
    if not re.fullmatch(r"[A-Za-z0-9._~-]{1,4096}", value):
        raise AdapterError("credential_invalid")
    return value


def request_value(payload):
    fields = {"config", "operation", "timeout", "controller_id", "command_id", "queue_id"}
    if not isinstance(payload, dict) or set(payload) - fields or not fields - {"queue_id"} <= payload.keys():
        raise AdapterError("invalid_request")
    operation = payload["operation"]
    if operation not in ("discover", "prepare", "play", "status"):
        raise AdapterError("invalid_request")
    timeout = payload["timeout"]
    if type(timeout) not in (int, float) or not 1 <= timeout <= 45 or not math.isfinite(timeout):
        raise AdapterError("invalid_request")
    if (not isinstance(payload["controller_id"], str) or not IDENTIFIER.fullmatch(payload["controller_id"])
            or type(payload["command_id"]) is not int or positive_id(payload["command_id"]) is None):
        raise AdapterError("invalid_request")
    if operation in ("play", "status"):
        if type(payload.get("queue_id")) is not int or positive_id(payload["queue_id"]) is None:
            raise AdapterError("invalid_request")
    elif "queue_id" in payload:
        raise AdapterError("invalid_request")
    return dict(payload, config=config_value(payload["config"]))


def dependencies():
    # Import lazily so configuration checks and fixture tests need no runtime
    # dependency. The installed shortcut venv uses the pinned requirements.
    import requests
    from plexapi.playqueue import PlayQueue
    from plexapi.server import PlexServer
    logging.getLogger("plexapi").disabled = True
    return requests, PlexServer, PlayQueue


def bounded_session(requests, config, payload, deadline, clock):
    origin = config["plex_url"]

    class Session(requests.Session):
        def request(self, method, url, **kwargs):
            if not isinstance(url, str) or not url.startswith(origin + "/"):
                raise AdapterError("untrusted_endpoint")
            remaining = deadline - clock()
            if remaining <= 0:
                raise AdapterError("deadline")
            kwargs["timeout"] = remaining
            kwargs["allow_redirects"] = False
            kwargs["stream"] = True
            headers = dict(kwargs.get("headers") or {})
            headers["X-Plex-Client-Identifier"] = payload["controller_id"]
            if "/player/" in urllib.parse.urlsplit(url).path:
                headers["X-Plex-Target-Client-Identifier"] = config["plex_client_id"]
            kwargs["headers"] = headers
            response = super().request(method, url, **kwargs)
            try:
                if 300 <= response.status_code < 400:
                    raise AdapterError("redirect_rejected")
                body = bytearray()
                for chunk in response.iter_content(65536):
                    if clock() >= deadline:
                        raise AdapterError("deadline")
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE:
                        raise AdapterError("response_too_large")
                response._content = bytes(body)
                response._content_consumed = True
            finally:
                response.close()
            if urllib.parse.urlsplit(url).path.startswith("/player/") and response.content:
                try:
                    result = ElementTree.fromstring(response.content)
                except ElementTree.ParseError:
                    # PlexAPI handles empty or non-XML success replies from
                    # specific clients. Do not turn those into an implicit ack.
                    result = None
                if result is not None and result.tag == "Response":
                    code = result.attrib.get("code", "")
                    if not re.fullmatch(r"[1-5][0-9]{2}", code):
                        raise AdapterError("invalid_player_response")
                    if not 200 <= int(code) < 300:
                        # PMS can wrap a rejected Companion command in HTTP
                        # 200. playMedia otherwise discards this XML response.
                        raise PlayerRejected(int(code))
            return response

    session = Session()
    session.trust_env = False
    return session


def client_snapshot(server, config, command_id):
    matches = [client for client in server.clients() if client.machineIdentifier == config["plex_client_id"]]
    if len(matches) > 1:
        raise AdapterError("ambiguous_client")
    if not matches:
        return None
    client = matches[0]
    # PMS may advertise the LG app as 127.0.0.1. Never contact that address:
    # this detached snapshot stays on the authenticated PMS proxy route.
    client.proxyThroughServer(True, server=server)
    client._commandId = command_id - 1
    return client


def queue_metadata(queue, expected=None):
    queue_id = positive_id(queue.playQueueID)
    count = queue.playQueueTotalCount
    if queue_id is None or (expected is not None and queue_id != expected) or type(count) is not int or not 1 <= count <= MAX_ITEMS:
        raise AdapterError("invalid_queue")
    selected = queue.selectedItem
    first_id = positive_id(getattr(selected, "ratingKey", None))
    if first_id is None:
        raise AdapterError("invalid_queue")
    return {"queue_id": queue_id, "count": count, "first_id": str(first_id)}


def operate(payload, server_factory=None, queue_factory=None, clock=time.monotonic):
    payload = request_value(payload)
    config = payload["config"]
    deadline = clock() + payload["timeout"]
    session = None
    try:
        if server_factory is None:
            requests, server_factory, queue_factory = dependencies()
            session = bounded_session(requests, config, payload, deadline, clock)
            token = read_token(config["plex_token_file"])
            server = server_factory(config["plex_url"], token=token, session=session, timeout=payload["timeout"])
        else:
            server = server_factory()
        if server.machineIdentifier != config["plex_server_id"]:
            raise AdapterError("server_mismatch")
        client = client_snapshot(server, config, payload["command_id"])
        capabilities = sorted(set(getattr(client, "protocolCapabilities", []) or [])) if client else []
        ready = client is not None and {"playback", "timeline", "playqueues"} <= set(capabilities)
        operation = payload["operation"]
        if operation == "discover":
            allowed = {"playback", "timeline", "playqueues", "navigation", "provider-playback", "mirror"}
            return {"ready": ready, "capabilities": [v for v in capabilities if v in allowed]}
        if not ready:
            raise AdapterError("client_not_ready")
        if operation == "prepare":
            section = server.library.section(COLLECTION)
            if section.title != COLLECTION or section.type != "movie":
                raise AdapterError("library_mismatch")
            items = section.search(libtype="movie", maxresults=MAX_ITEMS + 1)
            if not items:
                raise AdapterError("empty_library")
            selected = items[:MAX_ITEMS]
            ids = [positive_id(getattr(item, "ratingKey", None)) for item in selected]
            if None in ids or len(set(ids)) != len(ids) or any(item.type != "movie" for item in selected):
                raise AdapterError("invalid_library")
            queue = queue_factory.create(server, selected, shuffle=True, includeRelated=False)
            result = queue_metadata(queue)
            if result["count"] != len(selected):
                raise AdapterError("invalid_queue")
            return dict(result, truncated=len(items) > MAX_ITEMS)
        if operation == "play":
            queue = queue_factory.get(server, payload["queue_id"])
            queue_metadata(queue, payload["queue_id"])
            # The queue is already created. playMedia receives that exact
            # PlayQueue, so it cannot silently create a replacement queue.
            client.playMedia(queue, offset=0)
            return {"accepted": True, "queue_id": payload["queue_id"]}
        timelines = [item for item in client.timelines(wait=0) if item.type == "video"]
        if len(timelines) != 1 or getattr(timelines[0], "machineIdentifier", None) != config["plex_server_id"]:
            return {"state": "unknown", "queue_id": None, "content_id": None, "position": None}
        timeline = timelines[0]
        state = timeline.state if timeline.state in ("playing", "paused", "stopped", "buffering") else "unknown"
        queue_id = positive_id(timeline.playQueueID)
        content_id = positive_id(timeline.ratingKey)
        position = timeline.time
        position = position / 1000 if type(position) in (int, float) and 0 <= position <= 1e15 and math.isfinite(position) else None
        return {"state": state, "queue_id": queue_id,
                "content_id": str(content_id) if content_id is not None else None, "position": position}
    finally:
        if session is not None:
            session.close()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError("invalid_request")
        result[key] = value
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise AdapterError("invalid_request")
        payload = json.loads(raw, object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(AdapterError("invalid_request")))
        print(json.dumps({"data": operate(payload)}, allow_nan=False))
        return 0
    except PlayerRejected as error:
        print(f"Native Plex rejected the Companion request (code {error.code}); "
              "check that the native Plex app is ready and supports this operation. "
              "The request was not repeated.", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - dependency errors must not expose credential-bearing URLs
        # Library exceptions may embed URLs and transient Plex tokens.
        print("Native Plex operation did not complete; no request was repeated.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
