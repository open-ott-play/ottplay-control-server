# Deployment

Use an exact published release tag such as `v0.1.0-beta.1`. The examples use `RELEASE_TAG` as a placeholder: replace it with a tag actually present in Releases and GHCR. Images contain both Linux amd64 and arm64 variants. On other server targets, install the native release archive.

## Choose and verify the installation

Download assets from the same [release](https://github.com/open-ott-play/ottplay-control-server/releases) and verify their digests against its release manifest before installation:

- The native `ottplay-control-server-OS-ARCH.tar.gz` or Windows `.zip` contains the executable, README, example configuration and licenses. Extract it and change into its `ottplay-control-server-OS-ARCH` directory before using `./ottplay-control-server` below. Supported targets are Linux amd64/arm64/armv7, Darwin amd64/arm64, and Windows amd64/arm64.
- `ottplay-control-server-deployment.tar.gz` supplies `deploy/` and the documentation used by the service examples. It does not contain the native executable. Extract it separately when using Compose, systemd or launchd without a source checkout.
- The Helm chart is a separate `.tgz` asset. Use it in place of `./charts/ottplay-control-server` in the Helm command below when installing from release assets.
- The Python `ott` remote is installed separately from the source checkout; neither archive contains `cli/`. Follow the [CLI installation guide](cli.md#installation-and-connection), including its sibling Python files. The remote can run on a different computer from the server.

Keep the private configuration outside an unpacked release directory so an update cannot replace it. `config.example.json` contains placeholders and cannot be used unchanged. Back up the real configuration privately; it contains the administrator and device credentials.

## Foreground startup and server commands

Create the private directory first. On macOS/Linux, this example initializes one device and starts a server for local testing:

```sh
mkdir -p "$HOME/.config/ottplay-control-server"
chmod 700 "$HOME/.config/ottplay-control-server"
./ottplay-control-server version
./ottplay-control-server init --config "$HOME/.config/ottplay-control-server/config.json" --device-id living-room
./ottplay-control-server validate --config "$HOME/.config/ottplay-control-server/config.json"
./ottplay-control-server serve --config "$HOME/.config/ottplay-control-server/config.json" --listen 127.0.0.1:8081
```

`init` requires a device ID and a new file path; it refuses to overwrite a file or symlink. Edit `allowed_origins` and other settings in that private file, then run `validate` before starting or restarting. Without `--listen`, the configuration supplies the bind address; a newly initialized configuration uses `0.0.0.0:8081`. Use that address, or a specific LAN interface, when TVs must connect. In players, enter the server's reachable IP/hostname, never `0.0.0.0`.

In another terminal, probe the listener:

```sh
./ottplay-control-server healthcheck --url http://127.0.0.1:8081/readyz
./ottplay-control-server serve --help
```

The executable has exactly five subcommands: `init`, `validate`, `serve`, `version`, and `healthcheck`. Each accepts `--help`. `healthcheck` defaults to `http://127.0.0.1:8081/healthz`, uses a five-second timeout, and succeeds only on HTTP 200 without following redirects. `/healthz` and `/readyz` report that the server responds; they do not prove a player is connected or playing.

For direct TLS, pass both `--tls-cert /private/server.crt` and `--tls-key /private/server.key` to `serve`, or terminate TLS at a reverse proxy. HTTPS players and diagnostic clients need an HTTPS endpoint with a trusted certificate. Keep the proxy's externally visible base path in the CLI/player address and route it to the server API without dropping or duplicating the prefix.

Use Ctrl-C to stop a foreground process. On Unix, SIGTERM also requests a graceful shutdown with a ten-second deadline. There is no server `restart`, `reload`, `register` or `remove` subcommand: use your process manager for restarts and the CLI procedures for [registration](cli.md#installation-and-connection) or [disconnection and credential revocation](cli.md#disconnect-deregister-or-revoke-a-player). `ott PLAYER restart` reloads that player, not the command server.

Configuration is read only at startup. This includes tokens, device entries, CORS origins and diagnostic operators. Every server restart discards queued commands, pending request/response exchanges, pairing requests and all diagnostic state, including the previous `server_epoch`; it does not remove credentials saved in configuration. Keep one replica.

## Docker and Compose

Generate a private configuration using the downloaded executable, edit `allowed_origins`, and make the file readable by container group 65532 without making it world-readable:

```sh
./ottplay-control-server init --config /absolute/private/config.json --device-id living-room
sudo chgrp 65532 /absolute/private/config.json
chmod 640 /absolute/private/config.json
export CONTROL_SERVER_VERSION=RELEASE_TAG
export CONTROL_SERVER_CONFIG=/absolute/private/config.json
export CONTROL_SERVER_BIND_IP=0.0.0.0
docker compose -f deploy/compose.yml up -d
```

Compose publishes port 8081 on all IPv4 interfaces by default. Set `CONTROL_SERVER_BIND_IP` to a specific LAN address to select one interface, or to `127.0.0.1` for host-only access. Docker Desktop file-sharing ownership behavior differs from Linux: verify that UID/GID 65532 can read the mounted file, while other local users cannot. The image runs without root, a writable root filesystem, Linux capabilities, or a shell. Its built-in healthcheck uses the executable itself.

Direct Docker use:

```sh
docker run -d --name ottplay-control-server --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 64m --cpus 0.5 -p 0.0.0.0:8081:8081 \
  --mount type=bind,src=/absolute/private/config.json,dst=/etc/ottplay-control-server/config.json,readonly \
  ghcr.io/open-ott-play/ottplay-control-server:RELEASE_TAG
```

Check, stop and restart the Compose service with the same configuration environment variables as at startup:

```sh
docker compose -f deploy/compose.yml ps
docker compose -f deploy/compose.yml logs --tail=100 control-server
docker compose -f deploy/compose.yml stop control-server
docker compose -f deploy/compose.yml start control-server
```

After editing the configuration, validate it with the downloaded executable and recreate the container so a file replaced by an editor or `ott add` is mounted again:

```sh
./ottplay-control-server validate --config "$CONTROL_SERVER_CONFIG"
docker compose -f deploy/compose.yml up -d --force-recreate control-server
```

To upgrade, back up the configuration, set `CONTROL_SERVER_VERSION` to the new verified tag, run `docker compose -f deploy/compose.yml pull`, then the same `up -d --force-recreate` command. Check `ps` and the health probe afterwards. Roll back by selecting the previously recorded tag/digest and recreating with a configuration compatible with that version. For a direct `docker run` installation, use `docker logs`, `docker stop`, and `docker start` for inspection and lifecycle; changing the image or replacing the bind-mounted file requires recreating that container with its original options.

## Kubernetes and k3s

The Helm chart uses one replica with the `Recreate` strategy because pending commands are in memory. It requires an existing Secret; Helm values and release archives never contain credentials. The Pod uses a read-only root filesystem, non-root UID/GID 65532, a restricted capability set, no API service-account token, resource limits, and readiness/liveness probes.

```sh
kubectl create namespace ottplay-control
kubectl -n ottplay-control create secret generic ottplay-control-server \
  --from-file=config.json=/absolute/private/config.json
helm upgrade --install ottplay-control-server ./charts/ottplay-control-server \
  --namespace ottplay-control --set image.tag=RELEASE_TAG
kubectl -n ottplay-control rollout status deployment/ottplay-control-server
```

Use the chart archive from the same release if deploying without a source checkout. Release charts include their exact candidate image tag as the default. Stable byte promotion preserves the accepted RC archive and its RC image tag; that tag contains the same image bytes as the stable tag. An explicit `--set image.tag=RELEASE_TAG` can select the published stable alias. Add `--set image.digest=sha256:...` to pin the verified multi-platform manifest digest as well as the release tag. The service defaults to ClusterIP. For local verification:

```sh
kubectl -n ottplay-control port-forward service/ottplay-control-server 8081:8081
./ottplay-control-server healthcheck --url http://127.0.0.1:8081/readyz
```

For TVs on the same LAN, add `--address 0.0.0.0` to `port-forward` and use
the forwarding machine's LAN IP with port 8081. Add each LAN player origin to
`allowed_origins`; forwarding does not bypass credentials or CORS checks.

For k3s, add `-f deploy/k3s-values.yaml`. It selects Traefik when Ingress is enabled; configure an actual reachable `ingress.host`, DNS, and optionally `ingress.tlsSecretName`. A trusted LAN HTTP endpoint is supported. HTTPS-hosted players need an HTTPS server endpoint. No hostname, public ingress, NodePort, or LoadBalancer is created by default. The release deployment archive also includes rendered `deploy/kubernetes.yaml` with the exact release tag, usable with `kubectl apply` after creating the namespace and Secret.

For a configuration edit, validate the private file, update the Secret's `config.json` entry, then restart and wait for the Deployment. The server does not reread a changed Secret automatically:

```sh
./ottplay-control-server validate --config /absolute/private/config.json
kubectl -n ottplay-control create secret generic ottplay-control-server \
  --from-file=config.json=/absolute/private/config.json --dry-run=client -o yaml \
  | kubectl -n ottplay-control apply -f -
kubectl -n ottplay-control rollout restart deployment/ottplay-control-server
kubectl -n ottplay-control rollout status deployment/ottplay-control-server
kubectl -n ottplay-control logs deployment/ottplay-control-server --tail=100
```

Use this manual Secret update only when it is the intended complete configuration and no other operator is changing it. The CLI's configured Kubernetes `ott add` flow checks for concurrent changes and handles registration/restart itself; see [registration](cli.md#installation-and-connection). Do not print Secret contents into support logs.

Pending commands are discarded during restart or replacement. Do not scale replicas independently or add an HPA. Before upgrading, capture the actual running image tag/digest, scheduling settings, Helm values and private configuration. Reconcile any changes made outside Helm: an old Helm revision may not describe the working deployment. Upgrade with the verified chart/image and preserve required node selectors and other site settings. Use `helm rollback` only after reviewing that revision's complete values; explicitly restore configuration if it changed. Queue contents cannot be recovered.

To uninstall a Helm-managed service, use `helm uninstall ottplay-control-server --namespace ottplay-control`. The separately created Secret remains; preserve it for reinstall or deliberately remove it when retiring the controller. Do not delete a shared namespace as part of removing this service.

## Linux systemd

Create a dedicated `ottplay-control` system account, install the matching executable at `/usr/local/bin/ottplay-control-server`, and create `/etc/ottplay-control-server/config.json` readable only by that account. Install `deploy/systemd/ottplay-control-server.service` under `/etc/systemd/system/`, then run `systemctl daemon-reload` and `systemctl enable --now ottplay-control-server`. New configurations bind all IPv4 interfaces; the service preserves any explicit listen address in an existing configuration.

Run these lifecycle commands with administrator privileges:

```sh
sudo systemctl status ottplay-control-server
sudo journalctl -u ottplay-control-server -n 100 --no-pager
sudo systemctl stop ottplay-control-server
sudo systemctl start ottplay-control-server
sudo -u ottplay-control /usr/local/bin/ottplay-control-server validate --config /etc/ottplay-control-server/config.json
sudo systemctl restart ottplay-control-server
```

To update the native executable, stop the service, retain the old verified executable, install the new one at the same path, validate the existing configuration with it, then start and probe the service. For removal, `sudo systemctl disable --now ottplay-control-server` stops it and disables boot startup; remove the installed unit and run `sudo systemctl daemon-reload` if uninstalling completely. Retain the private configuration until you deliberately retire its credentials.

## macOS

Use the Darwin arm64 or amd64 archive matching the machine. The example LaunchAgent is `deploy/launchd/org.open-ott-play.control-server.plist`. Adjust its binary/configuration paths, make the configuration readable only by the login account, place the plist in `~/Library/LaunchAgents/`, and load it with `launchctl bootstrap gui/$(id -u) PATH_TO_PLIST`. It starts as that user and restarts after an unsuccessful exit. For a machine-wide service, create a dedicated account and an appropriately reviewed LaunchDaemon configuration.

After installing the customized plist at the path below:

```sh
plutil -lint "$HOME/Library/LaunchAgents/org.open-ott-play.control-server.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.open-ott-play.control-server.plist"
launchctl print "gui/$(id -u)/org.open-ott-play.control-server"
launchctl kickstart -k "gui/$(id -u)/org.open-ott-play.control-server"
```

`kickstart -k` restarts a loaded agent after configuration changes. To stop/unload it, use `launchctl bootout "gui/$(id -u)/org.open-ott-play.control-server"`. To update the executable or plist, unload first, replace the verified executable or edit the plist, validate the configuration, and bootstrap again. The example does not configure log files; for startup failures, run its exact `serve` command in a terminal after unloading the agent. Removing the plist after `bootout` prevents that LaunchAgent from starting on the next login.

## Windows

Use the Windows amd64 or arm64 ZIP and run `ottplay-control-server.exe init`, `validate`, and `serve` with the same arguments shown in the main README. Restrict the configuration's NTFS ACL to the service account and administrators. For background startup, use Task Scheduler with a dedicated account, the executable as the program, `serve --config C:\private\config.json` as arguments, and an explicit working directory. Configure restart-on-failure. The executable is a console application, not a Windows SCM service binary; `sc.exe create` is not supported.

Use **Run**, **End**, and **Disable** in Task Scheduler to start, stop, or prevent future scheduled starts. Choose the task you created for this executable. Before replacing the executable, end the task and ensure no instance is still running; preserve the private configuration and its ACL, validate it with the new executable, and run the task again. Inspect **Last Run Result** and run the configured command in a console when diagnosing startup. Ctrl-C stops a foreground instance; Task Scheduler's **End** is not a promise of graceful shutdown.

## Troubleshooting

- **Executable missing or wrong architecture:** select the native archive for the host OS/CPU, extract it, and run `version`. The deployment archive is not a server executable or CLI installer. On macOS/Linux, check the executable permission and use `./ottplay-control-server` or the installed absolute path.
- **Cannot create/open configuration:** `init` never overwrites an existing file and does not create parent directories. Use `validate` for an existing configuration. Check that the service account or container UID/GID 65532 can traverse the private directory and read the file. Do not fix this by making credentials world-readable.
- **Validation fails:** use real, distinct generated tokens; placeholder examples are intentionally invalid. Check JSON syntax, supported fields, exact origins, unique device IDs and diagnostic device/operator scopes. Removing a device also requires removing its references from operator `device_ids`; an operator cannot retain an empty scope. See [device revocation](cli.md#disconnect-deregister-or-revoke-a-player) and [diagnostics configuration](remote-diagnostics.md#enablement-and-credentials).
- **Server failed to listen or serve:** verify that the port is unused, the bind address belongs to the host, and both TLS paths exist and are readable when TLS is enabled. Check your process manager's status and ensure a foreground process is not already occupying the port.
- **Healthy locally but the player cannot connect:** confirm the LAN/DNS address, firewall and port mapping/Ingress; the player cannot reach the server through its own `127.0.0.1`. Check the exact player page origin, HTTPS mixed-content rules, trusted TLS chain and reverse-proxy base path. `allow_null_origin` applies only to designated device routes and does not grant administrator/operator access.
- **401/403 or a player never appears:** use the device token in the player and the separate administrator credentials for legacy `ott`; diagnostics need a separately scoped operator token. A changed configuration is inactive until the server restarts. `ott devices` reports polling activity, while `ott PLAYER` requires a player with the request/response extension; a healthy server alone proves neither.
- **Discovery/pairing returns 404:** the optional `discovery` block must be configured and loaded at startup. Check the [discovery guide](discovery.md), advertised controller URL and proxy route. Restarting expires pending approvals; create and approve a fresh request rather than reusing an old code.
- **Diagnostics unavailable, expired or missing after restart:** enable the device and operator scopes, use HTTPS and current local player consent, then discover fresh runtime/consent/server epochs. See the [diagnostic CLI guide](diagnostics-cli.md). Server restart invalidates prior sessions and runtime credentials. Do not blindly repeat a repair or invent a new idempotency key after an uncertain reply.
- **Kubernetes Pod not ready:** inspect `kubectl -n ottplay-control describe pod POD_NAME` for scheduling, image-pull or mount errors. Check the exact published image tag/digest, CPU support, Secret name/key and file permissions. Preserve any required node selector during upgrades; a locally cached image on one node does not prove another node can pull it.
- **Commands disappear or time out:** queues and replies are bounded and in memory, and command expiry defaults to 60 seconds. Server restart discards them. Give each player its own device entry/token and keep one server replica; shared tokens or independent replicas can deliver a command to the wrong active instance. An acknowledgement is not proof of recovered playback.

## Acceptance boundaries

A cross-compiled binary is not proof of execution on every OS/CPU. CI executes tests on Linux amd64/arm64, macOS arm64, and Windows amd64, and builds all seven target binaries. CI also starts a restricted Linux container. Local chart rendering validates packaging; a real cluster rollout and physical TV behavior are separate acceptance steps.
