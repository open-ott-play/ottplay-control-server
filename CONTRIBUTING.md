# Contributing

Use [GitHub issues](https://github.com/open-ott-play/ottplay-control-server/issues) for non-sensitive bug reports, questions and
feature proposals. Include the exact version/commit, environment, expected and
actual behavior, and a minimal sanitized reproduction. Check existing issues
first and keep follow-up evidence in the original thread. For vulnerabilities,
use the [private security process](SECURITY.md).

Submit a focused pull request against `main`. Describe the user-visible problem,
the resulting behavior, compatibility implications and checks performed. Preserve
existing authorship and third-party license/provenance records. Discuss changes
to protocols, storage, device safety or dependency/runtime requirements before
making an incompatible change. English is the common language for code review
and project documentation.

## Development and validation

```sh
go vet ./...
go test -race ./...
python3 -m unittest discover -s .github/release-tests
python3 scripts/check_deployment.py
```

Use Go 1.26.9 and Python 3.12 as documented in docs/development.md. Native builds, container smoke, deployment contracts and release receipts are distinct checks. Queue acknowledgement is not durable exactly-once delivery.

The [CI workflow](.github/workflows/ci.yml) is the authoritative list of required jobs.
Use isolated test data and temporary outputs. Never run a device write, unlock,
deployment or publication command merely to validate a documentation change.

## Test and review policy

Changes to behavior must add or update automated tests that fail for the old
defect and cover the new boundary; regression fixes should include the relevant
failure case. If automation is infeasible, explain why in the PR and document
the reproducible manual procedure and limits. Update user/API documentation and
release notes for user-visible changes. Keep compiler, lint, static-analysis and
test assertions enabled, resolve new warnings, and document any remaining
warning with its reason and scope. Do not suppress a real security finding to
obtain a passing check. Wait for required checks and independent review before
merging; do not use an administrator bypass.

### Automatic pull request reviews

The installed Greptile GitHub App reviews eligible pull requests when opened,
updated with new commits, or rebased. The repository policy lives in
[`.greptile/config.json`](.greptile/config.json); it enables review checks and
status comments and keeps the pull request description unchanged. This native
integration requires no GitHub Actions workflow or API secret.

Review eligibility still inherits the Greptile dashboard's author, label and
base-branch filters, draft settings and review tier. The dashboard currently
excludes `californiantiramisu` as an author. If a review does not appear, check
those settings, repository access and the app's status comment first.

To verify automatic review, open an eligible non-draft pull request and confirm
that the `Greptile Review` check comes from `greptile-apps` and completes for the
pull request's latest head SHA. Push a follow-up commit and repeat this check;
a completed review of an earlier SHA does not cover the updated diff. Verify
these events without manually requesting a review.

The Greptile check is advisory, not a required merge check. Completion does not
mean there are no findings: read its review and resolve applicable feedback.
The existing CI and independent-review requirements still apply.

## Workflow validator dependency

The workflow validator installs PyYAML from
`.github/requirements-workflow-contracts.txt` with `--require-hashes` and
`--only-binary=:all:`. When updating the version, review its PyPI release and
replace the SHA-256 wheel list for all supported Python/platform builds; do not
remove hash verification. Run `scripts/workflow_contracts.py` with the new lock.

## Fuzz regression tests

See [the fuzzing guide](docs/fuzzing.md) for target scope and local commands. Keep
new seeds synthetic, preserve minimized regressions with fixes, and keep mutation
campaigns restricted to public release candidates.

## Coding standards and community

Python follows PEP 8 and explicit error handling. Go follows `gofmt` and `go vet` where applicable. JavaScript uses the existing module format, explicit bounds and the repository's lint/test conventions. Shell uses quoted expansions and `set -euo pipefail` for Bash entry points. Preserve third-party notices and the supported runtime versions. These are written standards; not every style rule is automatically enforced.

See [governance](GOVERNANCE.md), the [Code of Conduct](CODE_OF_CONDUCT.md), [roadmap](ROADMAP.md) and [quality limits](docs/quality.md).

## Python quality diagnostics

Install `.github/requirements-lint.txt` with `python3 -m pip install --require-hashes --only-binary=:all: -r .github/requirements-lint.txt`, then run `python3 -m ruff check --select E9,F63,F7,F82 cli scripts tests native`. CI runs these practical error checks; they complement the vulnerability analysis.

The maintainer updates the repository front page to link newly recognized project achievements within 48 hours. The OpenSSF image links directly to this project's live assessment and updates with its awarded level. Do not copy another project's achievement.

For JavaScript, use the file's ESM/CommonJS format consistently, two-space indentation, semicolons and strict equality; keep compatibility-oriented upstream styles intact. These supplement PEP 8 for Python, `gofmt`/Effective Go for Go, and quoted expansions in Bash.
