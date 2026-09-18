# Deployment

Use an exact published release tag such as `v0.1.0-beta.1`. The examples use `RELEASE_TAG` as a placeholder: replace it with a tag actually present in Releases and GHCR. Images contain both Linux amd64 and arm64 variants. On other server targets, install the native release archive.

## Docker and Compose

Generate a private configuration using the downloaded executable, edit `allowed_origins`, and make the file readable by container group 65532 without making it world-readable:

```sh
./ottplay-control-server init --config /absolute/private/config.json --device-id living-room
sudo chgrp 65532 /absolute/private/config.json
chmod 640 /absolute/private/config.json
export CONTROL_SERVER_VERSION=RELEASE_TAG
export CONTROL_SERVER_CONFIG=/absolute/private/config.json
export CONTROL_SERVER_BIND_IP=127.0.0.1
docker compose -f deploy/compose.yml up -d
```

For LAN access, explicitly change `CONTROL_SERVER_BIND_IP` to the host's LAN address. Docker Desktop file-sharing ownership behavior differs from Linux: verify that UID/GID 65532 can read the mounted file, while other local users cannot. The image runs without root, a writable root filesystem, Linux capabilities, or a shell. Its built-in healthcheck uses the executable itself.

Direct Docker use:

```sh
docker run -d --name ottplay-control-server --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 64m --cpus 0.5 -p 127.0.0.1:8081:8081 \
  --mount type=bind,src=/absolute/private/config.json,dst=/etc/ottplay-control-server/config.json,readonly \
  ghcr.io/open-ott-play/ottplay-control-server:RELEASE_TAG
```

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

For k3s, add `-f deploy/k3s-values.yaml`. It selects Traefik when Ingress is enabled; configure an actual reachable `ingress.host`, DNS, and optionally `ingress.tlsSecretName`. A trusted LAN HTTP endpoint is supported. HTTPS-hosted players need an HTTPS server endpoint. No hostname, public ingress, NodePort, or LoadBalancer is created by default. The release deployment archive also includes rendered `deploy/kubernetes.yaml` with the exact release tag, usable with `kubectl apply` after creating the namespace and Secret.

Configuration changes require a restart: `kubectl -n ottplay-control rollout restart deployment/ottplay-control-server`. Pending commands are discarded during restart or replacement. Do not scale replicas independently or add an HPA. Capture the current release tag/digest and private configuration before upgrading; use `helm rollback` to restore the prior image and explicitly restore configuration if it changed. Queue contents cannot be recovered.

## Linux systemd

Create a dedicated `ottplay-control` system account, install the matching executable at `/usr/local/bin/ottplay-control-server`, and create `/etc/ottplay-control-server/config.json` readable only by that account. Install `deploy/systemd/ottplay-control-server.service` under `/etc/systemd/system/`, then run `systemctl daemon-reload` and `systemctl enable --now ottplay-control-server`. The service preserves the configuration's listen address; it does not automatically expose all interfaces.

## macOS

Use the Darwin arm64 or amd64 archive matching the machine. The example LaunchAgent is `deploy/launchd/org.open-ott-play.control-server.plist`. Adjust its binary/configuration paths, make the configuration readable only by the login account, place the plist in `~/Library/LaunchAgents/`, and load it with `launchctl bootstrap gui/$(id -u) PATH_TO_PLIST`. It starts as that user and restarts after an unsuccessful exit. For a machine-wide service, create a dedicated account and an appropriately reviewed LaunchDaemon configuration.

## Windows

Use the Windows amd64 or arm64 ZIP and run `ottplay-control-server.exe init`, `validate`, and `serve` with the same arguments shown in the main README. Restrict the configuration's NTFS ACL to the service account and administrators. For background startup, use Task Scheduler with a dedicated account, the executable as the program, `serve --config C:\private\config.json` as arguments, and an explicit working directory. Configure restart-on-failure. The executable is a console application, not a Windows SCM service binary; `sc.exe create` is not supported.

## Acceptance boundaries

A cross-compiled binary is not proof of execution on every OS/CPU. CI executes tests on Linux amd64/arm64, macOS arm64, and Windows amd64, and builds all seven target binaries. CI also starts a restricted Linux container. Local chart rendering validates packaging; a real cluster rollout and physical TV behavior are separate acceptance steps.
