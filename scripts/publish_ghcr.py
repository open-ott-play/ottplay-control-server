#!/usr/bin/env python3
"""Copy one verified published beta, RC or stable OCI archive to its exact GHCR tag.

This project adapter does not modify the vendored release toolkit. Downloads are
data only; neither release artifacts nor their scripts are executed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote
import zipfile

import release_control as rc
from publish_verified import verified_assets
from version_plan import plan_digest, validate_plan
from version_receipt import verify_declared_artifacts, verify_receipts

REPOSITORY = "open-ott-play/ottplay-control-server"
IMAGE = "ghcr.io/" + REPOSITORY
ARCHIVE = "ottplay-control-server-container.oci.tar"
TAG = re.compile(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-(?:beta|rc)\.[1-9]\d*)?", re.ASCII)


class RegistryError(rc.ReleaseError):
    """A static operator-facing diagnostic, never raw network output."""


def registry_require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def validate_candidate(raw: bytes, tag: str) -> dict:
    """Extend the toolkit's RC verifier to frozen beta plans without rewriting evidence."""
    rc.require(TAG.fullmatch(tag), "An exact beta, RC or stable release tag is required")
    if "-rc." in tag:
        return rc.validate_manifest(raw, REPOSITORY, tag)
    rc.require("-beta." in tag and len(raw) <= 2_000_000, "Expected a bounded beta manifest")
    m = rc.parse_json(raw, rc.MANIFEST)
    rc.require(isinstance(m, dict) and type(m.get("schema")) is int and m["schema"] == 1, "Invalid manifest schema")
    rc.require(m.get("repository") == REPOSITORY and m.get("channel") == "beta" and m.get("tag") == tag, "Manifest repository/channel/tag mismatch")
    rc.require(isinstance(m.get("source_sha"), str) and rc.SHA_RE.fullmatch(m["source_sha"]), "Invalid source SHA")
    rc.require(m.get("workflow_path") == rc.WORKFLOW, "Unexpected release workflow")
    rc.positive(m.get("run_id"), "run ID")
    rc.positive(m.get("run_attempt"), "run attempt")
    rc.validate_policy_snapshot(m.get("source_policy"), REPOSITORY)
    plan = validate_plan(m.get("version_plan"), m["source_policy"]["data"], m["source_sha"])
    rc.require(plan["tag"] == tag and plan["channel"] == "beta" and plan["base_version"] == m.get("version") and plan_digest(plan) == m.get("plan_sha256"), "Manifest differs from frozen plan")
    assets = m.get("assets")
    rc.require(isinstance(assets, list) and bool(assets), "Missing manifest assets")
    names = set()
    for item in assets:
        rc.require(isinstance(item, dict), "Invalid asset entry")
        name = item.get("name")
        rc.require(isinstance(name, str) and rc.NAME_RE.fullmatch(name) and name.casefold() != rc.MANIFEST.casefold() and name.casefold() not in names, "Unsafe or duplicate asset name")
        names.add(name.casefold())
        rc.require(type(item.get("size")) is int and item["size"] >= 0 and isinstance(item.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), "Invalid asset size/digest")
    return m


def verify_release(gh, tag: str, directory: Path) -> dict:
    """Check public bytes, original source policy, successful gate and immutable evidence."""
    rc.require(gh.repo == REPOSITORY and TAG.fullmatch(tag), "Untrusted repository or tag")
    if "-" not in tag:
        manifest = verified_assets(gh, tag, directory)
    else:
        ref, release, assets = rc.release_snapshot(gh, tag)
        manifests = [a for a in assets if a["name"] == rc.MANIFEST]
        rc.require(len(manifests) == 1, "Release needs exactly one manifest")
        raw = gh.binary(f"releases/assets/{rc.positive(manifests[0]['id'], 'manifest asset ID')}")
        manifest = validate_candidate(raw, tag)
        rc.require(ref["object"]["sha"] == manifest["source_sha"], "Release tag source mismatch")
        policy = rc.source_policy_snapshot(gh, manifest["source_sha"])
        rc.require(policy == manifest["source_policy"], "Source policy snapshot changed")
        run = gh.api(f"actions/runs/{manifest['run_id']}")
        rc.require(run.get("id") == manifest["run_id"], "Source run ID mismatch")
        if manifest["channel"] == "rc":
            rc.require(run.get("event") == "workflow_dispatch", "RC must originate from a manual dispatch")
        rc.validate_run(gh, run, rc.repository_info(gh), manifest["source_sha"], manifest["run_attempt"], completed=True)
        rc.verify_evidence(gh, manifest, raw)
        expected = {item["name"]: item for item in manifest["assets"]}
        rc.require(len(assets) == len(expected) + 1 and {a["name"] for a in assets} == set(expected) | {rc.MANIFEST}, "Public release inventory differs from evidence")
        for asset in assets:
            if asset["name"] == rc.MANIFEST:
                continue
            item = expected[asset["name"]]
            content = gh.binary(f"releases/assets/{rc.positive(asset['id'], 'asset ID')}")
            rc.require(len(content) == item["size"] and rc.digest(content) == item["sha256"], "Release payload bytes changed")
            rc.require(asset.get("digest") == "sha256:" + item["sha256"], "GitHub asset digest mismatch")
            (directory / item["name"]).write_bytes(content)
    policy = manifest["source_policy"]["data"]
    rc.require(policy.get("container_assets") == {ARCHIVE: IMAGE}, "Container destination mapping is not the approved project mapping")
    plan = validate_plan(manifest.get("version_plan"), policy, manifest["source_sha"])
    receipts = verify_receipts(directory, plan, manifest["assets"], policy)
    rc.require(manifest.get("build_receipts") == [{"name": item["name"], "sha256": item["sha256"]} for item in receipts], "Manifest receipt inventory differs from payload receipts")
    verify_declared_artifacts(directory, policy, plan)
    rc.require((directory / ARCHIVE).is_file(), "Verified OCI archive is missing")
    return manifest


def tag_for_run(gh, run_id: int) -> str | None:
    """Discover candidate evidence or a stable promotion, then verify it separately."""
    run = gh.api(f"actions/runs/{run_id}")
    rc.require(run.get("id") == run_id, "Trigger run identity mismatch")
    rc.validate_run(gh, run, rc.repository_info(gh), run.get("head_sha", ""), run.get("run_attempt"), completed=True, gate=False)
    artifacts = gh.pages(f"actions/runs/{run_id}/artifacts", "artifacts")
    evidence = [a for a in artifacts if a.get("name") == "release-evidence"]
    if evidence:
        rc.require(len(evidence) == 1 and not evidence[0].get("expired"), "Ambiguous or expired release evidence")
        artifact = evidence[0]
        rc.require(artifact.get("workflow_run", {}).get("id") == run_id and artifact.get("workflow_run", {}).get("head_sha") == run["head_sha"], "Trigger evidence provenance mismatch")
        raw = gh.binary(f"actions/artifacts/{rc.positive(artifact.get('id'), 'artifact ID')}/zip")
        rc.require(artifact.get("digest") == "sha256:" + rc.digest(raw), "Trigger evidence digest mismatch")
        with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
            entries = [entry for entry in zipped.infolist() if not entry.is_dir()]
            rc.require(len(entries) == 1 and entries[0].filename in (rc.MANIFEST, str(rc.EVIDENCE)) and entries[0].file_size <= 2_000_000, "Unexpected evidence archive contents")
            manifest = rc.parse_json(zipped.read(entries[0]), rc.MANIFEST)
        rc.require(manifest.get("run_id") == run_id and manifest.get("run_attempt") == run["run_attempt"] and manifest.get("source_sha") == run["head_sha"], "Trigger manifest provenance mismatch")
        tag = manifest.get("tag", "")
        if manifest.get("channel") == "nightly":
            return None
        rc.require(TAG.fullmatch(tag), "Unsupported published tag")
        return tag
    # Byte promotion reuses the original RC evidence. Its public release body
    # contains the promotion run URL; this is discovery, never authorization.
    marker = f"Promotion: https://github.com/{REPOSITORY}/actions/runs/{run_id}"
    releases = [r for r in gh.pages("releases") if marker in (r.get("body") or "").splitlines() and r.get("draft") is False and r.get("prerelease") is False]
    rc.require(len(releases) <= 1, "Ambiguous stable promotion")
    return releases[0]["tag_name"] if releases else None


def command(args: list[str], operation: str = "registry operation", **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, check=False, **kwargs)
    registry_require(result.returncode == 0, "Registry command failed during " + operation)
    return result


def publish_archive(archive: Path, tag: str, execute: bool) -> dict:
    """Retain every manifest digest and refuse conflicting exact version tags."""
    rc.require(TAG.fullmatch(tag), "Invalid immutable image tag")
    local = command(["skopeo", "inspect", "--raw", "oci-archive:" + str(archive)], operation="verified archive inspection").stdout
    rc.require(bool(local), "OCI archive has no root manifest")
    expected = "sha256:" + rc.digest(local)
    reference = IMAGE + ":" + tag
    inventory = subprocess.run(["skopeo", "list-tags", "docker://" + IMAGE], capture_output=True, check=False)
    if inventory.returncode != 0:
        error = inventory.stderr.decode("utf-8", errors="replace").lower()
        missing = "name_unknown" in error or "name unknown:" in error or "repository does not exist" in error
        forbidden = "403 forbidden" in error
        # A new GHCR namespace can deny pull-scope token requests before its
        # first push. A 403 is NOT proof that a version tag is absent. Create
        # only content-addressed objects, verify them, then require readable
        # inventory before any named-tag write. Existing tags remain untouched.
        if execute and (missing or forbidden):
            immutable = "docker://" + IMAGE + "@" + expected
            command(["skopeo", "copy", "--all", "--preserve-digests", "oci-archive:" + str(archive), immutable], operation="immutable namespace bootstrap")
            remote = command(["skopeo", "inspect", "--raw", immutable], operation="immutable bootstrap verification").stdout
            registry_require("sha256:" + rc.digest(remote) == expected, "Immutable bootstrap digest differs from verified archive")
            inventory = subprocess.run(["skopeo", "list-tags", "docker://" + IMAGE], capture_output=True, check=False)
            registry_require(inventory.returncode == 0, "Registry inventory remains unreadable after immutable bootstrap; no version tag was written")
        elif not (missing and not execute):
            raise RegistryError("Cannot establish registry tag inventory; no version tag was written")
    if inventory.returncode == 0:
        data = json.loads(inventory.stdout)
        registry_require(isinstance(data, dict) and "Tags" in data, "Invalid registry tag inventory")
        tags = [] if data["Tags"] is None else data["Tags"]
        registry_require(isinstance(tags, list) and all(isinstance(tag, str) for tag in tags), "Invalid registry tag inventory")
        exists = tag in tags
    else:
        # Only the read-only, explicit NAME_UNKNOWN branch reaches this point.
        exists = False
    if exists:
        remote = command(["skopeo", "inspect", "--raw", "docker://" + reference], operation="existing version inspection").stdout
        registry_require("sha256:" + rc.digest(remote) == expected, "Existing registry tag conflicts with verified release bytes")
        state = "already-published"
    elif execute:
        command(["skopeo", "copy", "--all", "--preserve-digests", "oci-archive:" + str(archive), "docker://" + reference], operation="new version publication")
        remote = command(["skopeo", "inspect", "--raw", "docker://" + reference], operation="published version verification").stdout
        registry_require("sha256:" + rc.digest(remote) == expected, "Published registry digest differs from verified archive")
        state = "published"
    else:
        state = "verified-only"
    return {"image": reference, "digest": expected, "state": state}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--tag")
    selection.add_argument("--run-id", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    phase = "release discovery"
    try:
        gh = rc.GitHub(REPOSITORY)
        tag = args.tag or tag_for_run(gh, rc.positive(args.run_id, "run ID"))
        if tag is None:
            print(json.dumps({"state": "skipped", "reason": "no published beta, RC or stable release"}))
            return 0
        with tempfile.TemporaryDirectory(prefix="ottplay-ghcr-") as temporary:
            directory = Path(temporary)
            phase = "release provenance and payload verification"
            manifest = verify_release(gh, tag, directory)
            if args.execute:
                phase = "registry authentication"
                rc.require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and os.environ.get("GH_TOKEN") and os.environ.get("GITHUB_ACTOR"), "Authenticated project Actions context is required for publication")
                os.environ["REGISTRY_AUTH_FILE"] = str(directory / "registry-auth.json")
                command(["skopeo", "login", "--username", os.environ["GITHUB_ACTOR"], "--password-stdin", "ghcr.io"], operation="registry authentication", input=os.environ["GH_TOKEN"].encode())
            phase = "verified OCI publication"
            result = publish_archive(directory / ARCHIVE, tag, args.execute)
            result.update({"tag": tag, "source_sha": manifest["source_sha"], "release_run_id": manifest["run_id"]})
            print(json.dumps(result, sort_keys=True))
        return 0
    except RegistryError as error:
        print(f"GHCR publication failed: {error}", file=sys.stderr)
        return 1
    except (rc.ReleaseError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        # Network tools can include bearer credentials or signed redirect URLs.
        print(f"GHCR publication failed during {phase}; no unchecked image will be copied.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
