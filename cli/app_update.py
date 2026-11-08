"""Explicit, verified-release Android OTA orchestration; no background scheduler."""
import hashlib
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
    if tail and tail[0].casefold() == "beta":
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


def request_json(url, error, limit=1024 * 1024, deadline=None, *, group_cancelled=None, expected_sha256=None):
    """Public GitHub requests never receive controller credentials."""
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not HASH.fullmatch(expected_sha256)):
        raise error("Invalid official release metadata digest")
    if group_cancelled is not None and group_cancelled.is_set():
        raise KeyboardInterrupt
    budget = min(15, (deadline - time.monotonic()) if deadline is not None else 15)
    if budget <= 0:
        raise error("Update deadline expired while reading official release metadata")
    finished = queue.Queue(maxsize=1)
    cancelled = threading.Event()
    def read():
        try:
            if cancelled.is_set() or group_cancelled is not None and group_cancelled.is_set():
                return
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
            if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise ValueError("Release metadata digest mismatch")
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
        cutoff = time.monotonic() + budget
        while True:
            if group_cancelled is not None and group_cancelled.is_set():
                raise KeyboardInterrupt
            try:
                valid, result = finished.get(timeout=budget if group_cancelled is None else
                                            max(0, min(0.1, cutoff - time.monotonic())))
                break
            except queue.Empty:
                if group_cancelled is None or time.monotonic() >= cutoff:
                    raise
        if group_cancelled is not None and group_cancelled.is_set():
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        cancelled.set()
        raise
    except queue.Empty:
        cancelled.set()
        raise error("Official release metadata timed out; no update was requested") from None
    if not valid:
        raise error("Cannot read verified official release metadata; no update was requested") from None
    return result


def payload_asset_index(entries, error, *, closed=False):
    """Validate a bounded manifest's full asset index without downloading payloads."""
    invalid = "Invalid official release payload index"
    if not isinstance(entries, list) or not entries:
        raise error(invalid)
    result = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not {"name", "size", "sha256"} <= set(entry)
                or (closed and set(entry) != {"name", "size", "sha256"})):
            raise error(invalid)
        name = entry["name"]
        if (not isinstance(name, str) or not name or name in (".", "..") or name in result
                or "/" in name or "\\" in name or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)
                or type(entry["size"]) is not int or entry["size"] <= 0
                or not isinstance(entry["sha256"], str) or not HASH.fullmatch(entry["sha256"])):
            raise error(invalid)
        try:
            if len(name.encode("utf-8")) > 255:
                raise error(invalid)
        except UnicodeEncodeError:
            raise error(invalid) from None
        result[name] = {key: entry[key] for key in ("name", "size", "sha256")}
    return result


def compact_payloads(manifest, error):
    """Project authenticated RC evidence; this does not attest CI or fuzz success."""
    invalid = "Official RC has invalid declared validation or build receipts"
    assets = payload_asset_index(manifest.get("assets"), error)
    build = manifest.get("build_receipts")
    validation = manifest.get("validation_receipts", [])
    if not isinstance(build, list) or not build or not isinstance(validation, list):
        raise error(invalid)
    omitted = set()
    for receipt in build + validation:
        if not isinstance(receipt, dict):
            raise error(invalid)
        name = receipt.get("name")
        if (not isinstance(name, str) or not re.fullmatch(r"release-inputs-[A-Za-z0-9._-]+\.json", name)
                or name in omitted or name not in assets or receipt.get("sha256") != assets[name]["sha256"]):
            raise error(invalid)
        omitted.add(name)
    declared_names = {name for name in assets if name.startswith("release-inputs-") and name.endswith(".json")}
    if declared_names != omitted:
        raise error(invalid)
    has_fuzz = "fuzz-report.json" in assets
    if has_fuzz != any(receipt["name"] == "release-inputs-fuzz.json" for receipt in validation):
        raise error(invalid)
    if has_fuzz:
        if not manifest.get("fuzzing_profile"):
            raise error(invalid)
        omitted.add("fuzz-report.json")
    shipped = [assets[name] for name in sorted(assets) if name not in omitted]
    if not shipped:
        raise error("Official RC has no stable payloads")
    return shipped


def release_target(opts, error, deadline=None, *, group_cancelled=None):
    def fetch(url, limit=1024 * 1024, expected_sha256=None):
        kwargs = {} if group_cancelled is None else {"group_cancelled": group_cancelled}
        if expected_sha256 is not None:
            kwargs["expected_sha256"] = expected_sha256
        return request_json(url, error, limit, deadline, **kwargs)
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
    def asset(name, selected_release=release, selected_tag=tag):
        prefix = "https://github.com/" + REPOSITORY + "/releases/download/" + selected_tag + "/"
        found = [a for a in selected_release["assets"] if isinstance(a, dict) and a.get("name") == name]
        if (len(found) != 1 or found[0].get("browser_download_url") != prefix + name or
                type(found[0].get("size")) is not int or found[0]["size"] <= 0):
            raise error("Official release has a missing or ambiguous " + name)
        return found[0]
    manifest_asset = asset("release-manifest.json")
    if manifest_asset["size"] > 1024 * 1024:
        raise error("Official release manifest is too large")
    manifest = fetch(manifest_asset["browser_download_url"])
    def tag_source(selected_tag):
        ref = fetch(API + "/git/ref/tags/" + selected_tag)
        obj = ref.get("object", {}) if isinstance(ref, dict) else {}
        for _ in range(3):
            if not isinstance(obj, dict):
                break
            if obj.get("type") != "tag":
                break
            sha = obj.get("sha")
            if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
                raise error("Invalid official release tag")
            annotated = fetch(API + "/git/tags/" + sha)
            obj = annotated.get("object", {}) if isinstance(annotated, dict) else {}
        source = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(obj, dict) or obj.get("type") != "commit" or not isinstance(source, str) or not COMMIT.fullmatch(source):
            raise error("Official release tag does not identify a commit")
        return source
    source = tag_source(tag)
    if not isinstance(manifest, dict):
        raise error("Invalid official release manifest")
    expected_channel = next((channel for channel in ("beta", "rc", "nightly") if "-" + channel + "." in tag), "stable")
    compact_assets = None
    candidate = None
    if type(manifest.get("schema")) is int and manifest["schema"] == 2:
        expected_keys = {"schema", "kind", "repository", "version", "channel", "tag", "source_sha", "assets", "rc_evidence"}
        evidence = manifest.get("rc_evidence")
        evidence_keys = {"tag", "manifest_sha256", "source_sha", "run_id", "run_attempt"}
        if (set(manifest) != expected_keys or manifest.get("kind") != "rc-payloads-v1"
                or expected_channel != "stable" or release.get("prerelease") is not False
                or manifest.get("repository") != REPOSITORY or manifest.get("version") != tag[1:]
                or manifest.get("channel") != "stable" or manifest.get("tag") != tag
                or manifest.get("source_sha") != source
                or not isinstance(evidence, dict) or set(evidence) != evidence_keys
                or not isinstance(evidence.get("tag"), str)
                or not re.fullmatch(re.escape(tag) + r"-rc\.([1-9][0-9]*)", evidence["tag"])
                or evidence.get("source_sha") != source
                or not isinstance(evidence.get("manifest_sha256"), str) or not HASH.fullmatch(evidence["manifest_sha256"])
                or any(type(evidence.get(key)) is not int or evidence[key] <= 0 for key in ("run_id", "run_attempt"))):
            raise error("Official stable release has invalid compact RC evidence")
        compact_assets = payload_asset_index(manifest["assets"], error, closed=True)
        candidate_tag = evidence["tag"]
        candidate = fetch(API + "/releases/tags/" + candidate_tag)
        if (not isinstance(candidate, dict) or candidate.get("draft") is not False
                or candidate.get("prerelease") is not True or candidate.get("tag_name") != candidate_tag
                or not candidate.get("published_at") or not isinstance(candidate.get("assets"), list)):
            raise error("Official stable release has no published RC evidence")
        candidate_manifest = asset("release-manifest.json", candidate, candidate_tag)
        if (candidate_manifest["size"] > 1024 * 1024
                or candidate_manifest.get("digest") != "sha256:" + evidence["manifest_sha256"]):
            raise error("Official stable release has invalid RC manifest evidence")
        # Follow exactly one same-repository evidence link, retaining the outer
        # stable identity. Promoted RC packages already have the final version.
        manifest = fetch(candidate_manifest["browser_download_url"], expected_sha256=evidence["manifest_sha256"])
        if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int or manifest["schema"] != 1
                or manifest.get("channel") != "rc" or manifest.get("tag") != candidate_tag
                or manifest.get("source_sha") != source
                or any(type(manifest.get(key)) is not int or manifest[key] != evidence[key] for key in ("run_id", "run_attempt"))):
            raise error("Official stable release has mismatched RC provenance")
    plan = manifest.get("version_plan", {})
    evidence_tag = tag
    promoted = expected_channel == "stable" and manifest.get("channel") == "rc"
    if promoted:
        evidence_tag = manifest.get("tag")
        match = re.fullmatch(re.escape(tag) + r"-rc\.([1-9][0-9]*)", evidence_tag) if isinstance(evidence_tag, str) else None
        plan_keys = {"schema_version", "base_version", "version", "channel", "sequence", "tag",
                     "source_sha", "build_number", "policy_sha256", "promotion"}
        policy = manifest.get("source_policy", {})
        policy = policy.get("data") if isinstance(policy, dict) else None
        def digest(value):
            try:
                raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
            except (TypeError, ValueError):
                return None
            return hashlib.sha256(raw).hexdigest()
        if (not match or release.get("prerelease") is not False or
                not isinstance(plan, dict) or set(plan) != plan_keys or
                plan.get("promotion") != "promote-bytes" or
                type(plan.get("sequence")) is not int or str(plan["sequence"]) != match[1] or
                plan.get("base_version") != tag[1:] or manifest.get("version") != tag[1:] or
                not isinstance(manifest.get("plan_sha256"), str) or not HASH.fullmatch(manifest["plan_sha256"]) or
                not isinstance(plan.get("policy_sha256"), str) or not HASH.fullmatch(plan["policy_sha256"]) or
                manifest.get("plan_sha256") != digest(plan) or
                not isinstance(policy, dict) or plan.get("policy_sha256") != digest(policy) or
                not isinstance(policy.get("versioning"), dict) or policy["versioning"].get("promotion") != "promote-bytes"):
            raise error("Official stable release has invalid RC promotion evidence")
        expected_channel = "rc"
    if (type(manifest.get("schema")) is not int or manifest["schema"] != 1 or manifest.get("repository") != REPOSITORY or
            manifest.get("tag") != evidence_tag or manifest.get("source_sha") != source or
            manifest.get("channel") != expected_channel or
            not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1 or
            plan.get("channel") != expected_channel or
            plan.get("tag") != evidence_tag or plan.get("source_sha") != source or
            plan.get("version") != evidence_tag[1:] or
            type(plan.get("build_number")) is not int or not 0 < plan["build_number"] <= 2100000000):
        raise error("Official release tag and version plan do not match")
    name = VARIANTS[opts["variant"]]
    apk = asset(name)
    entries = manifest.get("assets", [])
    found = [a for a in entries if isinstance(a, dict) and a.get("name") == name] if isinstance(entries, list) else []
    if (len(found) != 1 or not isinstance(found[0].get("sha256"), str) or
            not HASH.fullmatch(found[0]["sha256"]) or found[0].get("size") != apk["size"] or apk["size"] > 128 * 1024 * 1024):
        raise error("Official APK size/hash does not match the release manifest")
    if promoted and compact_assets is not None:
        candidate_apk = asset(name, candidate, evidence_tag)
        if (compact_assets.get(name) != {key: found[0][key] for key in ("name", "size", "sha256")}
                or candidate_apk.get("digest") != "sha256:" + found[0]["sha256"]
                or apk.get("digest") != candidate_apk.get("digest") or candidate_apk["size"] != apk["size"]
                or tag_source(evidence_tag) != source):
            raise error("Official stable release differs from its RC source or assets")
    elif promoted:
        # Promotion copies the RC bytes, including its unchanged manifest. Keep
        # the stable download URL, but bind both publications to that evidence.
        manifest_content = digest(manifest)
        if manifest_content is None:
            raise error("Official stable release has invalid RC manifest content")
        candidate = fetch(API + "/releases/tags/" + evidence_tag)
        if (not isinstance(candidate, dict) or candidate.get("draft") is not False or
                candidate.get("prerelease") is not True or candidate.get("tag_name") != evidence_tag or
                not candidate.get("published_at") or not isinstance(candidate.get("assets"), list)):
            raise error("Official stable release has no published RC evidence")
        candidate_manifest = asset("release-manifest.json", candidate, evidence_tag)
        candidate_apk = asset(name, candidate, evidence_tag)
        manifest_digest = manifest_asset.get("digest")
        if (not isinstance(manifest_digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest_digest) or
                candidate_manifest.get("digest") != manifest_digest or candidate_manifest["size"] != manifest_asset["size"] or
                candidate_apk.get("digest") != "sha256:" + found[0]["sha256"] or
                apk.get("digest") != candidate_apk.get("digest") or candidate_apk["size"] != apk["size"] or
                digest(fetch(candidate_manifest["browser_download_url"])) != manifest_content or
                tag_source(evidence_tag) != source):
            raise error("Official stable release differs from its RC source or assets")
    versions = manifest.get("package_versions", [])
    def value(field, expected):
        rows = [row for row in versions if isinstance(row, dict) and row.get("path") == name and row.get("field") == field] if isinstance(versions, list) else []
        return (len(rows) == 1 and rows[0].get("version") == expected and
                (not promoted or rows[0].get("plan_sha256") == manifest["plan_sha256"]))
    if not value("versionCode", str(plan["build_number"])) or not value("version", tag[1:]):
        raise error("Official APK version metadata does not match its version plan")
    if compact_assets is not None and list(compact_assets.values()) != compact_payloads(manifest, error):
        raise error("Official stable release differs from its RC payload index")
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
        group_cancelled = getattr(client, "group_cancelled", None)
        cancellation = {} if group_cancelled is None else {"group_cancelled": group_cancelled}
        target = release_target(opts, error, deadline, **cancellation)
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
