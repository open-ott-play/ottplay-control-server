# Release-candidate fuzzing

Native Go fuzzing covers result binding and Android-agent input parsing with
synthetic data. The targets never start the server, contact a player, run ADB,
install an APK, execute an operation, or mutate a device queue.

## Target scope

- `FuzzInspectResultBinding` exercises the production inspection-result
  validator. An accepted result must remain bound to its expected runtime,
  section and, when applicable, operation ID. Both positive and negative
  receipts are covered. Inputs retain the production byte budget.
- `FuzzBinaryManifest` exercises the production Android binary manifest parser
  with UTF-8/UTF-16 seeds, truncations and arbitrary bytes below 2 MiB. This tests
  parser robustness before APK identity checks, not an APK-signature trust claim.
- `FuzzNativeParameters` exercises the production operation-envelope parser with
  arbitrary inputs up to 64 KiB. Accepted parameters round-trip, duplicate keys
  are rejected, and trailing JSON cannot smuggle a second operation.

The first two targets already existed as local fuzz tests; the manifest now
includes them in the shared RC qualification instead of leaving them dormant.

## Local reproduction

Use the Go version in the relevant `go.mod` and run:

```sh
go mod download
go -C native/android-agent mod download
go test -race ./internal/control
go -C native/android-agent test -race ./...
go test ./internal/control -run='^$' -fuzz='^FuzzInspectResultBinding$' -fuzztime=60s -parallel=2
go -C native/android-agent test . -run='^$' -fuzz='^FuzzBinaryManifest$' -fuzztime=60s -parallel=2
go -C native/android-agent test . -run='^$' -fuzz='^FuzzNativeParameters$' -fuzztime=60s -parallel=2
```

The setup manifest also precompiles instrumented test binaries with `go test -c
-fuzz=Fuzz -o /dev/null` to keep compilation outside the mutation budget. This
compilation does not execute fuzz cases. The root and native-agent modules keep
their separate lockfiles and package paths.

## Release policy and evidence

Mutation fuzzing runs only for an explicit **release candidate** request from
the default branch of this public repository, through the shared
`venus-os-ci-toolkit` release pipeline. Pull requests, pushes, betas and nightly
builds do not run mutation campaigns. The shared workflow rejects private
repositories and non-RC events. This is project-operated testing in GitHub
Actions; it does not claim enrollment in Google's hosted OSS-Fuzz service.

The manifest [`.github/fuzz.json`](../.github/fuzz.json) owns the project-specific
commands. The shared runner owns isolation, budgets and retained diagnostics.
Each of the three targets receives 60 seconds with two workers: a total of
180 seconds of mutation time, plus bounded preparation/compilation. Dependency
download and instrumented compilation happen during setup; the campaigns run
without network access. A missing target or failed campaign cannot qualify an RC.

The README **Fuzzing: RC only** badge describes this policy, not the last result.
Inspect the exact release Actions run and its retained logs/report for actual
source-bound results. Fuzz failures block candidate publication. Fix the cause,
review and merge the change, then request a new RC. Stable promotion reuses the
accepted RC's source and artifact evidence without another campaign or rebuild.
Ordinary tests continue replaying checked-in seed/regression cases on PRs and
betas; that deterministic replay does not enable random mutation there.

## Findings and maintenance

Go reports the minimized failing input path under `testdata/fuzz/<FuzzName>/`
and a command to replay it. Keep that file with the corrected code, run the
printed regression command, then the regular tests and a fresh campaign. The
shared workflow retains failing inputs and logs so a hosted failure can be
reproduced locally. Do not put real tokens, account identifiers, private captures
or device data into a public corpus or report. Use [SECURITY.md](../SECURITY.md)
for confidential vulnerabilities.

New targets should call application boundaries and check independent properties,
not only re-test the standard library. Keep inputs bounded and execution
deterministic, with no network or physical device operations. Update the manifest
and this document together. See [Go's fuzzing guide](https://go.dev/doc/security/fuzz/)
for corpus format and minimization. Passing these campaigns is additional test
evidence, not proof that every possible input is safe or hardware acceptance.
