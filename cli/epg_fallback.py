"""Configuration policy for an optional local native EPG fallback.

This module does not start processes, download guides, or dispatch playback.
The caller retries the entire read-only query with fresh catalogue metadata.
"""
import ipaddress
from urllib.parse import urlsplit, urlunsplit


class ConfigurationError(ValueError):
    pass


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
            or set(settings) - {"url", "source", "fallback_url"}
            or settings["source"] != "epg-one"):
        raise ConfigurationError('EPG configuration requires url and source "epg-one", with optional fallback_url')
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
