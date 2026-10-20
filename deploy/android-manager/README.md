# One Android USB manager

`android-manager` replaces per-tablet ADB toolbox containers. A foreground ADB
server and the existing OTT boot-recovery state machine share one container.
The small host service owns USB discovery and recreates that **one** container
when an approved device is re-enumerated. Tablet applications keep running when
ADB reconnects. This is boot recovery, not a frozen-video watchdog.

The recovery algorithm was extracted from the deployed
`android_usb_supervisor.py` in local commit
`41a02519222222a8d6619f711dfdeb03490bb8eb`; the old Docker lifecycle and unrelated
OTT changes are not imported. Secure/unknown locks are refused, an existing
boot is armed without waking the screen, recovery requires a new completed
boot, and only a verified foreground application marks that boot recovered.

## Build and provision

Build this directory as the Docker context. The base is an immutable CPython 3.12.14 / Debian
Trixie **amd64** manifest; Debian package repositories provide signed package
metadata and security updates. Record the resulting image digest and package
inventory, scan it, and deploy that exact digest. A fresh build can include
newer Debian security packages; it must be qualified as a new image.

```sh
docker build --cpu-shares 128 --memory 512m -t android-manager:candidate deploy/android-manager
```

1. Preserve old container inspections, both original ADB identity pairs,
   supervisor configuration/state and boot/service files in a root-only backup.
   Keep the original containers stopped for rollback; do not prune their layers.
2. Copy `config.example.json` to a private configuration. Define every tablet by
   unique USB serial, vendor, model, SDK and a second Android identity. Product
   and product ID may be null only where they have not been recorded; the
   verified Android identity still applies. Duplicate serials are rejected.
   **A3/K960 with the same serial must not be enrolled together.** They remain
   outside this registry until a physical-path identity policy is implemented.
3. Provision `key_dir/fire/{adbkey,adbkey.pub}` and
   `key_dir/a1/{adbkey,adbkey.pub}` from their original containers. Missing keys
   are fatal; the manager never intentionally generates replacement trust.
   Directories are root-owned mode 0700, keys/config mode 0600. The Fire primary
   key is copied to temporary memory and the separate a1 key is supplied via
   `ADB_VENDOR_KEYS`. The persistent key mounts are read-only.
4. Copy each existing boot state to `state_dir/ALIAS/state.json` **before**
   starting the manager. For k8a this means copying the exact bytes from
   `/var/lib/ott-k8a-recovery/state.json` to `state_dir/k8a/state.json` and
   verifying the hash. This prevents a new manager from waking/relaunching the
   already-running tablet boot.
5. Install `manager.py`, `recovery.py`, the unit and private config under
   `/usr/local/etc/android-manager/`. Install the boot wrapper under
   `/usr/local/etc/rc.d/`, and `android-managerctl` under `/usr/local/bin/`.
6. With no active installation, stop the old supervisor service and its
   transport container; start the new service. Only after acceptance disable
   the old unit and remove its boot wrapper from the active boot directory,
   retaining both in the private backup.

The runtime has no network, published port, Docker socket, added capability,
privileged mode or writable root filesystem. It runs as root with all
capabilities dropped because Synology's USB nodes are root-only. Access is
restricted to currently matched USB nodes. Memory is 256 MiB, CPU shares 128;
`/tmp` is a 128 MiB tmpfs. The target DSM kernel has no PIDs controller, so no PID cap is requested or
claimed. Process isolation comes from the container; this is not a defense
against malicious authorized ADB operators. The host service holds the same filesystem lock as
maintenance and recovery, so hotplug recreation waits for a current command.
ADB forwards are ephemeral and disappear on a USB-set change. Clients must
create a fresh forward for each CDP session and remove it afterwards.

## Operations

```sh
# On the NAS (or use tools/android_manager_remote.py from a workstation):
sudo /usr/local/bin/android-managerctl status
sudo /usr/local/bin/android-managerctl exec k8a get-state
sudo /usr/local/bin/android-managerctl exec k8a shell getprop ro.product.model
```

Every operation selects a current numeric USB transport ID after checking that
exactly one matching serial is authorized. It then verifies model, SDK and the
second Android identity. No global `ANDROID_SERIAL` is used. Unknown aliases,
ambiguous serials, global server resets and cross-device forwarding ports fail
closed. Operator shell/install access still has Android's normal ADB privileges;
this is a targeting guard, not a sandbox for untrusted operators.

Use each device's configured `forward_port`; global `forward --remove-all`,
`kill-server`, network ADB and serial-only selection are not exposed. The host
service is the only lifecycle owner. Update the private config atomically and
restart that service to change enrollment. Do not run old onboarding/reconnect
scripts; migrate clients to `android-managerctl exec ALIAS ...`.

## Acceptance and rollback

Test identity refusal, duplicate serials, secure-lock refusal, retry limits,
foreground verification, hotplug reconciliation, child failure and bounded
shutdown with `python3 -m unittest discover -s tests -p test_android_manager.py`.
Use live read-only identity/boot-ID/foreground checks before and after a manager
recreation. Confirm the original key fingerprints and completed boot remain
unchanged. Do not reboot a production tablet just to test this migration.
Physical replug, a1 plus Fire simultaneous operation, and a later natural tablet
reboot are separate hardware qualifications; do not claim them from unit tests.

Rollback: stop the new host service, stop `android-manager`, restore the old
k8a configuration and boot-state files, restore/enable the original unit and
boot wrapper, and start the old host service. It resolves k8a's current USB node
and reuses its preserved identity. The three old onboarding containers may have
stale USB paths; do not blindly start them. Their writable layers and keys are
retained as evidence/recovery material, not as an automatic fallback.

ADB targeting and key handling follow the [official ADB manual](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md).
