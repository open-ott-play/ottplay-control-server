"""Exercise the repository's trusted metadata callers without GitHub access."""

import ast
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = "victron-venus/venus-os-ci-toolkit/.github/workflows/"
APPROVAL_AUTHORS = {"4alvit", "dependabot[bot]", "renovate[bot]"}
MERGE_AUTHORS = APPROVAL_AUTHORS | {"californiantiramisu"}


def expression_value(source, context):
    """Interpret only the boolean/string expressions used by these callers."""
    source = re.sub(
        r"contains\(fromJSON\('([^']+)'\), github.event.pull_request.user.login\)",
        lambda match: repr(context["github.event.pull_request.user.login"] in json.loads(match[1])),
        source,
    )
    source = re.sub(
        r"contains\(github.event.pull_request.labels\.\*\.name, '([^']+)'\)",
        lambda match: repr(match[1] in context["labels"]),
        source,
    )
    source = re.sub(r"\bgithub(?:\.[A-Za-z_]+)+\b", lambda match: repr(context[match[0]]), source)
    source = re.sub(r"!(?!=)", "not ", source.replace("&&", "and").replace("||", "or"))

    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool, int)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            result = visit(node.values[0])
            for value in node.values[1:]:
                if isinstance(node.op, ast.And) and not result:
                    return result
                if isinstance(node.op, ast.Or) and result:
                    return result
                result = visit(value)
            return result
        raise AssertionError(f"Unsupported caller expression: {ast.dump(node)}")

    return visit(ast.parse("(" + source.strip() + ")", mode="eval").body)


def context(author="4alvit", event="pull_request", **changes):
    values = {
        "github.workflow": "Review automation",
        "github.event_name": event,
        "github.event.action": "synchronize",
        "github.event.changes.base": False,
        "github.repository": "open-ott-play/ottplay-control-server",
        "github.event.repository.default_branch": "main",
        "github.event.pull_request.head.repo.full_name": "open-ott-play/ottplay-control-server",
        "github.event.pull_request.base.ref": "main",
        "github.event.pull_request.user.login": author,
        "github.event.pull_request.draft": False,
        "github.event.pull_request.number": 42,
        "github.run_id": 101,
        "github.actor": "untrusted-labeler",
        "labels": ["automerge"],
    }
    values.update(changes)
    return values


class ReviewAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {}
        for name in ("auto-approve", "auto-merge"):
            path = ROOT / ".github/workflows" / f"{name}.yml"
            cls.workflows[name] = yaml.load(path.read_text(), Loader=yaml.BaseLoader)

    def test_author_event_matrix_has_one_route_and_no_owned_bot_approval(self):
        for name, workflow in self.workflows.items():
            authors = APPROVAL_AUTHORS if name == "auto-approve" else MERGE_AUTHORS
            condition = workflow["jobs"][name]["if"]
            for author in MERGE_AUTHORS | {"attacker", "github-actions[bot]"}:
                eligible_events = []
                for event in ("pull_request", "pull_request_target", "workflow_dispatch", "push"):
                    with self.subTest(workflow=name, author=author, event=event):
                        actual = bool(expression_value(condition, context(author, event)))
                        expected_event = "pull_request_target" if author == "dependabot[bot]" else "pull_request"
                        self.assertEqual(actual, author in authors and event == expected_event)
                        if actual:
                            eligible_events.append(event)
                self.assertEqual(len(eligible_events), int(author in authors))
        supplied = self.workflows["auto-approve"]["jobs"]["auto-approve"]["with"]["authors"]
        self.assertEqual(set(json.loads(supplied)), APPROVAL_AUTHORS)

    def test_forks_wrong_branch_drafts_and_removed_labels_never_run(self):
        invalid = (
            {"github.event.pull_request.head.repo.full_name": "attacker/fork"},
            {"github.event.pull_request.base.ref": "release"},
            {"github.event.pull_request.draft": True},
            {"labels": []},
            {"labels": ["auto-approve"]},
        )
        for name, workflow in self.workflows.items():
            for author in MERGE_AUTHORS:
                event = "pull_request_target" if author == "dependabot[bot]" else "pull_request"
                for mutation in invalid:
                    with self.subTest(workflow=name, author=author, mutation=mutation):
                        self.assertFalse(expression_value(workflow["jobs"][name]["if"], context(author, event, **mutation)))

    def test_metadata_edits_cannot_cancel_meaningful_pending_work(self):
        for name, workflow in self.workflows.items():
            condition = workflow["jobs"][name]["if"]
            group = workflow["concurrency"]["group"]

            def render(values, template=group):
                return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(expression_value(match[1], values)), template)

            original = context()
            title_edit = context(**{"github.event.action": "edited", "github.run_id": 102})
            base_edit = {**title_edit, "github.event.changes.base": True}
            self.assertTrue(expression_value(condition, original))
            self.assertFalse(expression_value(condition, title_edit))
            self.assertTrue(expression_value(condition, base_edit))
            self.assertNotEqual(render(original), render(title_edit))
            self.assertEqual(render(original), render(base_edit))
            self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "true")

    def test_callers_delegate_only_metadata_with_minimum_permissions(self):
        events = {"opened", "synchronize", "reopened", "ready_for_review", "labeled", "unlabeled", "edited", "converted_to_draft"}
        revisions = set()
        for name, workflow in self.workflows.items():
            self.assertEqual(set(workflow["on"]), {"pull_request", "pull_request_target"})
            for trigger in workflow["on"].values():
                self.assertEqual(set(trigger["types"]), events)
            self.assertEqual(workflow["permissions"], {"contents": "read"})
            self.assertEqual(set(workflow["jobs"]), {name})
            job = workflow["jobs"][name]
            self.assertEqual(set(job), {"permissions", "if", "uses", "with", "secrets"})
            self.assertEqual(job["permissions"], {"contents": "read", "pull-requests": "write"})
            shared = "auto-approve-reusable" if name == "auto-approve" else name
            reference = re.fullmatch(re.escape(f"{TOOLKIT}{shared}.yml@") + r"([0-9a-f]{40})", job["uses"])
            self.assertIsNotNone(reference, "Automation must use the expected workflow at a full commit SHA")
            revisions.add(reference.group(1))
            expected_secrets = {"BOT_PAT": "${{ secrets.BOT_PAT }}"}
            if name == "auto-approve":
                expected_secrets["APPROVAL_PAT"] = "${{ secrets.APPROVAL_PAT }}"
            self.assertEqual(job["secrets"], expected_secrets)
        self.assertEqual(len(revisions), 1, "Approval and merge callers must use the same toolkit revision")

    def test_merge_requires_review_successful_gate_and_only_dependabot_actions_review(self):
        options = self.workflows["auto-merge"]["jobs"]["auto-merge"]["with"]
        self.assertEqual(options, {
            "reviewed-merge": "true",
            "required-status-checks": "CI gate",
            "allow-dependabot-actions-review": "true",
            "allow-owned-bot-actions-review": "false",
        })
        gate = yaml.load((ROOT / ".github/workflows/quality-gate.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(gate["jobs"]["gate"]["name"], options["required-status-checks"])


if __name__ == "__main__":
    unittest.main()
