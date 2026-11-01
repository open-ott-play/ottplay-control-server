"""Configuration policy for an optional local native EPG fallback.

This module does not start processes, download guides, or dispatch playback.
The caller retries the entire read-only query with fresh catalogue metadata.
"""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


class ConfigurationError(ValueError):
    pass


def canonical_archive_origin(value, *, origin_only=False):
    """Canonical HTTP origin, without DNS lookups or host-name heuristics."""
    message = "Invalid stateful archive origin; use an HTTP(S) origin without credentials, path, query or fragment"
    try:
        if (not isinstance(value, str) or not value or (origin_only and len(value) > 2048)
                or "\\" in value or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value)):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "#" in value or parsed.netloc.endswith(":")
                or (origin_only and (parsed.path not in ("", "/") or "?" in value))):
            raise ValueError()
        host = parsed.hostname
        if "%" in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
            host = "[" + str(address) + "]" if address.version == 6 else str(address)
        except ValueError:
            host = (host[:-1] if host.endswith(".") else host).encode("idna").decode("ascii").lower()
            if (not host or len(host) > 253 or re.fullmatch(r"[0-9.]+", host)
                    or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                               for label in host.split("."))):
                raise ValueError()
        port = parsed.port
        default = 443 if parsed.scheme == "https" else 80
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        return parsed.scheme + "://" + host + (":" + str(port) if port not in (None, default) else "")
    except (ValueError, TypeError, UnicodeError):
        raise ConfigurationError(message) from None


def stateful_archive_origins(value):
    """Validate an explicit, bounded allowlist; absence is handled by callers."""
    if not isinstance(value, list) or len(value) > 32:
        raise ConfigurationError("stateful_archive_origins must be a list of at most 32 HTTP(S) origins")
    return list(dict.fromkeys(canonical_archive_origin(item, origin_only=True) for item in value))


def _url(value, *, local=False):
    message = ("EPG fallback_url must be an HTTP(S) loopback IP address ending in /epg/v1"
               if local else "EPG url must be an HTTP(S) address without credentials, query or fragment")
    try:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "?" in value or "#" in value or "\\" in value
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or parsed.port == 0):
            raise ValueError()
        if local:
            # Literal addresses avoid DNS/proxy ambiguity and hostname rebinding.
            address = ipaddress.ip_address(parsed.hostname)
            if not address.is_loopback or "%" in parsed.hostname or parsed.path.rstrip("/") != "/epg/v1":
                raise ValueError()
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    except (ValueError, TypeError):
        raise ConfigurationError(message) from None


def split_settings(settings):
    """Return independent primary/fallback settings without mutating config."""
    if (not isinstance(settings, dict)
            or not {"url", "source"}.issubset(settings)
            or set(settings) - {"url", "source", "fallback_url", "stateful_archive_origins"}
            or settings["source"] != "epg-one"):
        raise ConfigurationError('EPG configuration requires url and source "epg-one", with optional fallback_url and stateful_archive_origins')
    primary = {"url": _url(settings["url"]), "source": "epg-one"}
    fallback = None
    if "fallback_url" in settings:
        fallback = {"url": _url(settings["fallback_url"], local=True), "source": "epg-one"}
        def endpoint(value):
            parsed = urlsplit(value)
            host = parsed.hostname
            try:
                host = str(ipaddress.ip_address(host))
            except ValueError:
                pass
            return parsed.scheme, host, parsed.port or (443 if parsed.scheme == "https" else 80), parsed.path
        if endpoint(primary["url"]) == endpoint(fallback["url"]):
            raise ConfigurationError("EPG fallback_url must differ from the primary url")
    if "stateful_archive_origins" in settings:
        origins = stateful_archive_origins(settings["stateful_archive_origins"])
        primary["stateful_archive_origins"] = list(origins)
        if fallback is not None:
            fallback["stateful_archive_origins"] = list(origins)
    return primary, fallback


def is_local_url(value):
    """Identify a permitted sidecar URL so transports can disable proxies."""
    try:
        _url(value, local=True)
        return True
    except ConfigurationError:
        return False


def eligible_failure(*, status=None, transport=False):
    """Permit only explicitly classified guide availability failures.

    The caller must supply an EPG-specific status or transport classification,
    never a player/controller error or a string-matched exception message.
    """
    return (type(status) is int and status in (429, 500, 502, 503, 504)) or (
        status is None and transport is True)
