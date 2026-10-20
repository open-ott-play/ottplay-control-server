# Android USB transport image

[English](README.md) · [Русский](README.ru.md)

This directory contains the deployment templates and the
[`Dockerfile`](Dockerfile) for the shared manager's transport container.
The Python manager runs on the USB host. The image supplies ADB, Python and
tools used by the atomic maintenance adapter; it does not contain a second
device registry or recovery daemon. See the
[operator guide](../../docs/android-usb-manager.md) for provisioning,
commands, migration, recovery limits and rollback.

Without a Git checkout, download `ottplay-control-server-deployment.tar.gz`
and `release-manifest.json` from the same
[release](https://github.com/open-ott-play/ottplay-control-server/releases).
Verify the archive digest and extract it into a new directory as described in
[Get the deployment package](../../docs/android-usb-manager.md#get-the-deployment-package).
The package includes this recipe and the six host/remote scripts under
`tools/`; keep its directory structure. Older packages, including
`v0.1.5-beta.6`, lack those scripts and require the matching source archive
instead. The package does not include a built USB transport image. The
control-server OCI release asset serves a different purpose.

## Source and artifact provenance

The base is `python:3.12.14-slim-trixie`, pinned to
`sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`.
The base, APT instruction and hash-locked `websocket-client==1.9.0` install
were reviewed in [PR 128 at revision b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager).
Their instruction text is retained to permit reuse of an available build
cache. The optional WebSocket package supports existing generic maintenance
clients; the migrated CDP adapters can also use only the Python standard
library.

This recipe removes the installed `pip` package after installing the locked
wheel, excludes PR 128's manager and recovery code, starts ADB in the
foreground, and disables inherited health checks. Removing the runtime
installer reduces its available attack surface; it is not evidence of a
clean vulnerability scan.

The reviewed PR 128 artifact was
`sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`.
It contains a different entrypoint and an incompatible heartbeat health
check. It is a reference artifact, not a build of this Dockerfile. Do not
attribute its image ID, source provenance or scan results to this recipe.

The APT repositories are not snapshot-pinned. A clean future rebuild can
therefore select newer OS packages even with the same base digest and Python
wheel hash. Record the exact source revision, builder/platform, resulting
image ID, package versions and scan when qualifying a build. Production
configuration must use that qualified immutable image ID or digest.

## Build without disturbing a busy USB host

Prefer building and scanning on a separate builder. The target host only
needs the qualified image, not package downloads or a build toolchain.

When reusing an already prepared local builder, first verify that the exact
base image is available and that the APT and wheel-install layers are cached.
Loading only the final PR 128 image does not establish either condition.
From the repository root or the extracted deployment directory, a deliberately
offline attempt is:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` applies to build steps; it is not a general promise that a
builder will never contact a registry while resolving a base. Confirm the
builder's local base resolution before using this procedure on a constrained
host. If an APT or wheel layer is missing, stop the attempt instead of
silently allowing a network rebuild on the USB host.

BuildKit includes the build step's network mode in its cache key. A cache
created with the default network mode may therefore miss during the offline
attempt above. Reuse that cache only after checking its exact base and
instruction chain, and stop if either package-install step executes instead
of reporting `CACHED`. The final `pip` removal explicitly uses
`RUN --network=none`; it needs no network even when earlier cached steps use
their original mode.

Before changing production configuration, check Python and ADB versions,
scan the exact resulting image, verify binary file transfer and APK staging,
then perform the guide's device-identity, key authorization, sleep and boot
acceptance checks. ADB 34 talking to an older Android device must be tested;
the version number alone does not establish KitKat or Fire compatibility.
Keep the current image and private deployment backup until rollback is no
longer needed.

## Runtime budget

The host manager, not this Dockerfile, applies 256 MiB memory, 256 MiB
memory-plus-swap and CPU shares 128, with no hard CPU or PID quota.
It enforces `--no-healthcheck`, `--network none`, a read-only root filesystem,
dropped capabilities and exact USB-node mappings. `/tmp` is a bounded tmpfs
that consumes the same memory budget. The staging adapter uses 1 MiB writes
and limits a single payload to 128 MiB; operations share the manager lock.

Docker documents the distinction between
[memory, swap and CPU-share controls](https://docs.docker.com/engine/containers/resource_constraints/)
and the [`--no-healthcheck` option](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
