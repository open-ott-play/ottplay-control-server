# Development and releases

Use Go 1.26.8 for reproducible release builds, Python 3.12 or later for release tooling, Helm 3, and Docker Buildx for containers. The runtime uses only Go's standard library. Legacy ES5 compatibility belongs to the separate player client; this native server does not execute browser JavaScript.

```sh
go vet ./...
go test -race ./...
go run golang.org/x/vuln/cmd/govulncheck@v1.8.0 ./...
python3 scripts/build.py darwin-arm64
python3 scripts/smoke_binary.py dist/ottplay-control-server-darwin-arm64
python3 scripts/check_deployment.py
python3 -m unittest discover -s .github/release-tests
```

Replace `darwin-arm64` with the desired target. Available targets are `linux-amd64`, `linux-arm64`, `linux-armv7`, `darwin-amd64`, `darwin-arm64`, `windows-amd64`, and `windows-arm64`. Builds use CGO disabled, baseline x86-64, ARMv7 where selected, embedded version/source/date, and stripped symbols. Local builds from a modified checkout are development evidence; only CI assets are published releases.

The required **CI gate** combines native tests, static checks, reachable-vulnerability scans with pinned govulncheck 1.8.0, restricted-container smoke, deployment contracts, release-tooling contracts, and CodeQL. Pull requests target `main`; `automerge` requests merging only after required checks pass. Release tags cannot be rewritten or deleted.

The release pipeline follows the organization's frozen version-plan workflow. Every build consumes one allocated identity and stages receipts for its outputs. Published assets include seven executables, per-binary version/digest metadata, matching native archives, a multi-platform OCI archive, a Helm chart, deployment resources, and the release manifest. Helm chart versions and embedded image tags retain the full candidate identity. The promote-bytes policy stamps RC executables with the base version so stable promotion can copy them unchanged; a promoted deployment archive retains its verified RC image tag, whose image bytes are identical to the stable tag.

Set repository variable `RELEASE_CHANNELS_ENABLED=true` only when publication is intended. Main pushes then create beta candidates; manual dispatch supports beta/RC, and the separate release environment gates stable promotion. GHCR publication verifies completed release evidence and copies the OCI archive without rebuilding. Registry tags include the leading `v`; there are no mutable `latest` aliases. A matching existing registry digest is an idempotent retry; a conflicting digest is rejected.

Release infrastructure is vendored from `victron-venus/venus-os-ci-toolkit`; preserve its license and update it through that toolkit. Project-specific build, smoke, deployment and GHCR verification scripts live alongside it.
