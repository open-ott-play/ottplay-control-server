"""Explicit, verified-release Android OTA orchestration; no background scheduler."""
import json
import math
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "open-ott-play/ottplay-foss"
API = "https://api.github.com/repos/" + REPOSITORY
HASH = re.compile(r"[a-f0-9]{64}")
COMMIT = re.compile(r"[a-f0-9]{40}")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|rc|nightly)\.[A-Za-z0-9.-]+)?")
VARIANTS = {"api22": "ottplay-foss-android-api22.apk", "full": "ottplay-foss-android-full.apk"}


def high_level(words):
    return bool(words and words[0].casefold() == "update" and
                (len(words) == 1 or words[1].casefold() not in ("status", "prepare", "install")))


def options(words, error):
    result = {"version": None, "variant": None, "prepare_only": False, "wait": 300.0}
    tail = list(words[1:])
    if tail and tail[0] == "beta":
        tail.pop(0)
    while tail:
        name = tail.pop(0)
        if name == "--prepare-only" and not result["prepare_only"]:
            result["prepare_only"] = True
        elif name in ("--version", "--variant", "--wait") and tail:
            key = name[2:]
            value = tail.pop(0)
            if key == "wait":
                try:
                    value = float(value)
                except ValueError:
                    raise error("--wait must be 1–900 seconds") from None
                if not math.isfinite(value) or not 1 <= value <= 900:
                    raise error("--wait must be 1–900 seconds")
            elif result[key] is not None:
                raise error("Duplicate update option: " + name)
            result[key] = value
        else:
            raise error("Use update [beta] --variant api22|full [--version vX.Y.Z[-beta.N]] [--prepare-only] [--wait SECONDS]")
    if result["variant"] not in VARIANTS:
        raise error("Select --variant api22 for Android 5.1/6 (including k8a/Fire OS 5), or --variant full for Android 7+. The player does not report a reliable Android API level; no APK was selected.")
    if result["version"]:
        tag = result["version"]
        if not tag.startswith("v"):
            tag = "v" + tag
        if not TAG.fullmatch(tag):
            raise error("--version must name a published official version, for example v1.1.58-beta.3 or v1.1.58")
        result["version"] = tag
    return result


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                not (parsed.hostname in ("github.com", "api.github.com") or
                     (parsed.hostname or "").endswith(".githubusercontent.com"))):
            raise ValueError("Untrusted release redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def request_json(url, error, limit=1024 * 1024, deadline=None):
    """Public GitHub requests never receive controller credentials."""
    budget = min(15, (deadline - time.monotonic()) if deadline is not None else 15)
    if budget <= 0:
        raise error("Update deadline expired while reading official release metadata")
    finished = queue.Queue(maxsize=1)
    cancelled = threading.Event()
    def read():
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ott-cli-android-update"})
            with urllib.request.build_opener(SafeRedirect()).open(request, timeout=budget) as response:
                raw = bytearray()
                while not cancelled.is_set():
                    chunk = response.read1(min(65536, limit + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > limit:
                        raise ValueError("Oversized release metadata")
                if cancelled.is_set():
                    return
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("Duplicate metadata key")
                    value[key] = item
                return value
            finished.put((True, json.loads(raw, object_pairs_hook=unique)))
        except Exception:
            finished.put((False, None))
    threading.Thread(target=read, daemon=True).start()
    try:
        valid, result = finished.get(timeout=budget)
    except queue.Empty:
        cancelled.set()
        raise error("Official release metadata timed out; no update was requested") from None
    if not valid:
        raise error("Cannot read verified official release metadata; no update was requested") from None
    return result


def release_target(opts, error, deadline=None):
    def fetch(url, limit=1024 * 1024):
        return request_json(url, error, limit, deadline)
    tag = opts["version"]
    if tag:
        release = fetch(API + "/releases/tags/" + tag)
    else:
        releases = fetch(API + "/releases?per_page=100", 16 * 1024 * 1024)
        if not isinstance(releases, list):
            raise error("Invalid official release listing")
        candidates = []
        for item in releases:
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)-beta\.(\d+)", str(item.get("tag_name", ""))) if isinstance(item, dict) else None
            if match and item.get("draft") is False and item.get("prerelease") is True:
                candidates.append((tuple(map(int, match.groups())), item))
        if not candidates:
            raise error("No published beta found in the latest 100 official releases; use --version")
        release = max(candidates, key=lambda row: row[0])[1]
        tag = release["tag_name"]
    if (not isinstance(release, dict) or release.get("draft") is not False or
            release.get("tag_name") != tag or not release.get("published_at") or
            not isinstance(release.get("assets"), list)):
        raise error("The selected official release is not published")
    prefix = "https://github.com/" + REPOSITORY + "/releases/download/" + tag + "/"
    def asset(name):
        found = [a for a in release["assets"] if isinstance(a, dict) and a.get("name") == name]
        if (len(found) != 1 or found[0].get("browser_download_url") != prefix + name or
                type(found[0].get("size")) is not int or found[0]["size"] <= 0):
            raise error("Official release has a missing or ambiguous " + name)
        return found[0]
    manifest_asset = asset("release-manifest.json")
    if manifest_asset["size"] > 1024 * 1024:
        raise error("Official release manifest is too large")
    manifest = fetch(manifest_asset["browser_download_url"])
    ref = fetch(API + "/git/ref/tags/" + tag)
    obj = ref.get("object", {}) if isinstance(ref, dict) else {}
    for _ in range(3):
        if obj.get("type") != "tag":
            break
        sha = obj.get("sha")
        if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
            raise error("Invalid official release tag")
        annotated = fetch(API + "/git/tags/" + sha)
        obj = annotated.get("object", {}) if isinstance(annotated, dict) else {}
    source = obj.get("sha")
    if obj.get("type") != "commit" or not isinstance(source, str) or not COMMIT.fullmatch(source):
        raise error("Official release tag does not identify a commit")
    if not isinstance(manifest, dict):
        raise error("Invalid official release manifest")
    plan = manifest.get("version_plan", {})
    expected_channel = next((channel for channel in ("beta", "rc", "nightly") if "-" + channel + "." in tag), "stable")
    if (type(manifest.get("schema")) is not int or manifest["schema"] != 1 or manifest.get("repository") != REPOSITORY or
            manifest.get("tag") != tag or manifest.get("source_sha") != source or
            manifest.get("channel") != expected_channel or
            not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1 or
            plan.get("channel") != expected_channel or
            plan.get("tag") != tag or plan.get("source_sha") != source or
            plan.get("version") != tag[1:] or
            type(plan.get("build_number")) is not int or not 0 < plan["build_number"] <= 2100000000):
        raise error("Official release tag and version plan do not match")
    name = VARIANTS[opts["variant"]]
    apk = asset(name)
    entries = manifest.get("assets", [])
    found = [a for a in entries if isinstance(a, dict) and a.get("name") == name] if isinstance(entries, list) else []
    if (len(found) != 1 or not isinstance(found[0].get("sha256"), str) or
            not HASH.fullmatch(found[0]["sha256"]) or found[0].get("size") != apk["size"] or apk["size"] > 128 * 1024 * 1024):
        raise error("Official APK size/hash does not match the release manifest")
    versions = manifest.get("package_versions", [])
    def value(field, expected):
        rows = [row for row in versions if isinstance(row, dict) and row.get("path") == name and row.get("field") == field] if isinstance(versions, list) else []
        return len(rows) == 1 and rows[0].get("version") == expected
    if not value("versionCode", str(plan["build_number"])) or not value("version", tag[1:]):
        raise error("Official APK version metadata does not match its version plan")
    return {"tag": tag, "variant": opts["variant"], "version_code": plan["build_number"],
            "source_sha": source, "sha256": found[0]["sha256"], "url": apk["browser_download_url"]}


def update(client, device, opts, runtime):
    error = runtime.Error
    deadline = time.monotonic() + opts["wait"]
    original_timeout = client.timeout
    target = None
    def call(operation, **values):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise error("Update wait expired; inspect update status before retrying. No install is replayed automatically.")
        client.timeout = min(original_timeout, remaining)
        params = {"operation": operation, **values}
        try:
            value = client.call(device, "app_update", params)
        except runtime.PlayerRejected:
            raise error("Player rejected the update. Inspect 'ott DEVICE kiosk', explicitly disable kiosk with 'ott DEVICE kiosk off' and unlock protected settings, then retry. No kiosk or PIN policy was changed.") from None
        except runtime.PlayerUnsupported:
            raise error("This player has no compatible AppUpdate plugin. Its first compatible APK must be installed locally; updating the CLI alone cannot add it.") from None
        return runtime.app_update_metadata(value, params)
    def result(state, message):
        return {"release": {k: target[k] for k in ("tag", "variant", "version_code", "source_sha", "sha256")},
                "phase": state["phase"], "installed_code": state["installed_code"],
                "installed_version": state["installed_version"], "message": message,
                "user_confirmation_required": True}
    try:
        state = call("status")
        target = release_target(opts, error, deadline)
        if state["installed_code"] > target["version_code"]:
            if opts["version"]:
                raise error("Selected release is older than the installed Android versionCode. In-place OTA downgrades are not supported; no uninstall or update was requested.")
            return result(state, "Installed versionCode is newer than the latest beta; no update requested.")
        if state["installed_code"] == target["version_code"]:
            return result(state, "The selected versionCode is already installed; no update requested (this does not verify installed file hashes).")
        busy = ("downloading", "ready", "awaiting_permission", "installing", "awaiting_confirmation")
        if state["phase"] in busy and state["sha256"] != target["sha256"]:
            raise error("A different APK update is pending; inspect update status before selecting another release")
        if state["phase"] not in busy:
            state = call("prepare", url=target["url"], sha256=target["sha256"])
        while state["phase"] == "downloading":
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            state = call("status")
        if state["sha256"] != target["sha256"]:
            raise error("The player update changed during this operation; inspect status before retrying")
        if state["phase"] in ("ready", "awaiting_permission", "installing", "awaiting_confirmation") and (
                state["target_code"] != target["version_code"] or state["target_version"] != target["tag"][1:]):
            raise error("The staged APK version does not match the selected release; installation was not requested")
        if state["phase"] == "failed":
            raise error("APK preparation/installation failed: " + (state["error"] or "unknown"))
        if state["phase"] in ("awaiting_confirmation", "installing"):
            return result(state, "An installation is already pending. Confirm it on Android if prompted, then run update status; install was not replayed.")
        if state["phase"] not in ("ready", "awaiting_permission"):
            raise error("Unexpected update state; inspect update status before retrying")
        if opts["prepare_only"]:
            return result(state, "APK verified and ready. Run update again without --prepare-only to request installation.")
        if state["phase"] == "awaiting_permission" and not state["can_request_installs"]:
            return result(state, "Allow installation from OTT in Android settings, then run the same update command again.")
        call("install", sha256=target["sha256"])
        while True:
            try:
                state = call("status")
            except error:
                # A successful package replacement closes the old player queue.
                pending = result(state, "Installer accepted; completion is not yet verified. Confirm on Android if prompted, then run update status. Do not resend install blindly.")
                pending["last_reported_phase"] = pending["phase"]
                pending["phase"] = "unconfirmed"
                return pending
            if state["installed_code"] > target["version_code"]:
                return result(state, "A newer versionCode is now installed; the selected update was superseded. Its installation is not verified.")
            if state["installed_code"] == target["version_code"]:
                if (state["sha256"] != target["sha256"] or state["target_code"] != target["version_code"] or
                        state["target_version"] != target["tag"][1:]):
                    raise error("Installed versionCode matches, but the selected update receipt changed; inspect status")
                return result(state, "Installed versionCode verified after update.")
            if state["sha256"] != target["sha256"]:
                raise error("The player update changed after installation was requested; inspect status")
            if state["phase"] in ("awaiting_permission", "awaiting_confirmation"):
                return result(state, "Android requires local permission/confirmation. Complete the displayed step, then run update status (or repeat update after granting installation permission).")
            if state["phase"] == "failed":
                raise error("Android installation failed: " + (state["error"] or "unknown"))
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    finally:
        client.timeout = original_timeout


def run(runtime, client, device, words, json_output):
    result = update(client, device, options(words, runtime.Error), runtime)
    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print(result["release"]["tag"] + " (" + result["release"]["variant"] + "): " + result["message"])
        print("Update phase: " + result["phase"] + "; installed versionCode: " + str(result["installed_code"]))
    return 0
