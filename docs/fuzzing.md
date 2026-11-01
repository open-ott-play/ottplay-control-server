# Release-candidate fuzzing

See the [illustrated RC fuzzing process and verified rollout results](https://github.com/victron-venus/venus-os-ci-toolkit/blob/main/docs/rc-fuzzing-results.md)
for the shared release flow, evidence requirements and a dated cross-project
snapshot. The project-specific scope and reproduction guidance below still apply.

> **Local packaging:** `python3 scripts/release.py package` is unsupported in this repository because no shell packaging adapter is installed. Use the [project commands below](#local-release-packaging); the generic example in `release-workflow.md` does not apply here.

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

## Local release packaging

Use Python 3.12+ and the Go version declared by `go.mod` from the repository root:

```sh
go mod download
python3 scripts/build.py linux-amd64
```

This builds and packages the selected portable server target under `dist/`. Select another target supported by `scripts/build.py` when needed. The complete release matrix, OCI archive and deployment-resource receipts are assembled by `.github/workflows/release-build.yml`; the one-target command does not publish or deploy them. Use a clean disposable checkout and a new output directory.

To reproduce an already allocated beta or RC, first check out the exact source
commit from its published `release-manifest.json`, restore that manifest's
`version_plan` object as `.release-plan.json`, and apply its version overlay:

```sh
python3 scripts/version_plan.py sync --root . --plan .release-plan.json
```

Then run the project commands above. Keep the source, plan and channel together;
do not invent a release sequence or edit version fields by hand. Local packaging
does not create a qualified release: the hosted pipeline still verifies the full
build inventory, provenance and, for an RC, its own isolated fuzz report.

## Release artifact selection and retries

Fresh release package uploads and RC fuzz evidence use
`-run-<run_id>-attempt-<run_attempt>` names. Candidate and final collection
select only matching artifacts from the current run and attempt.

Before publication starts, use **Re-run all jobs** only if the allocator and
release policy permit reuse of that run's reservation. Recreate every required
input in the same attempt; failed-only or individual retries can leave inputs
missing, and older artifacts must not fill those gaps.

For a fresh candidate or final build, once publication has started
(`begin_publication`), or its external publication result is partial or uncertain,
inspect the existing release, tags and assets before retrying. A consumed build
number requires a new workflow run and reservation authorized by the release
policy; an existing immutable tag may also require a new base version. Never
reset the release ledger, overwrite assets or retag to force a retry. Historical
byte promotion of an accepted RC keeps its reserved number and follows the
existing promotion policy.

Mutation remains limited to an explicit RC dispatch on the public default
branch. A report describes one source revision and run, not complete platform
coverage or device acceptance. Ordinary receipt verification and project
acceptance remain separate release prerequisites. See the shared
[evidence contract](https://github.com/victron-venus/venus-os-ci-toolkit/blob/main/docs/rc-fuzzing.md).

### Package receipt identity

Fresh publication verifies each staged package receipt against the independently
supplied repository, source commit, workflow run and attempt. Receipts created
before this migration, or offline without an Actions identity, cannot qualify a
fresh publication. Run all required producers in one complete workflow attempt
after migration; an earlier attempt cannot fill missing inputs.

Historical promotion and retained-evidence validation keep their existing checks.
Receipt coverage validates the staged files; the expected packages and platforms
still require a separate completeness check. Existing release approval and device
acceptance policies continue to apply.

See the shared [receipt migration guide](https://github.com/victron-venus/venus-os-ci-toolkit/blob/main/docs/release-workflow.md#migrate-package-receipt-identity-without-replacing-the-release-engine).

### Complete package producer inventory

`versioning.required_build_receipts` declares the exact package and signature
receipt filenames required by this project's final publisher. Each authenticated
receipt must match the repository, source commit, workflow run and attempt. A
missing producer or an unexpected receipt stops fresh publication, even when the
remaining packages are valid. RC fuzz evidence is checked separately; pre-signing
checks do not require a signature receipt that has not been created yet.

Before publication begins, rerun all required producers only when the allocator
and release policy permit reusing that reservation. After publication begins or
its outcome is uncertain, inspect existing tags, releases and assets before a
policy-authorized new run; never reset the ledger or mix earlier-attempt packages.
Historical RC promotion keeps its accepted source policy. Keep this explicit
inventory aligned with producer changes; it does not replace package-format,
signature or device acceptance checks.
