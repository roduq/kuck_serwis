from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
_LINTER_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "linter.yml"

_BASE_ACTION_PINS = {
	"actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
	"actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
}
_NODE_ACTION_PIN = "actions/setup-node@53b83947a5a98c8d113130e565377fae1a50d02f"
_PURE_MODULES = (
	"kuck_serwis.tests.test_audit_passive_db_preflight",
	"kuck_serwis.tests.test_audit_passive_probe",
	"kuck_serwis.tests.test_audit_probe_evidence",
	"kuck_serwis.tests.test_audit_readiness",
	"kuck_serwis.tests.test_ci_contract",
	"kuck_serwis.tests.test_contact_update",
	"kuck_serwis.tests.test_operational_policy_v1",
	"kuck_serwis.tests.test_repair_photo_content",
	"kuck_serwis.tests.test_repair_photo_decode_process",
	"kuck_serwis.tests.test_repair_photo_evidence_store",
	"kuck_serwis.tests.test_repair_photo_inventory",
	"kuck_serwis.tests.test_repair_photo_metadata",
	"kuck_serwis.tests.test_repair_photo_policy",
	"kuck_serwis.tests.test_repair_photo_retention",
	"kuck_serwis.tests.test_repair_photo_storage",
)


def _read_workflow(path: Path) -> str:
	return path.read_text(encoding="utf-8")


class TestCIContract(unittest.TestCase):
	def test_workflows_are_least_privilege_and_exactly_pinned(self) -> None:
		for path in (_CI_WORKFLOW, _LINTER_WORKFLOW):
			with self.subTest(path=path.name):
				text = _read_workflow(path)
				self.assertIn("permissions:\n  contents: read\n", text)
				self.assertNotIn("write-all", text)
				self.assertNotRegex(text, r"(?m)^\s+[a-z-]+:\s+write\s*$")
				self.assertIn("python-version: '3.14.6'", text)

				uses = re.findall(r"(?m)^\s+uses:\s+([^\s#]+)", text)
				expected = [f"{action}@{revision}" for action, revision in _BASE_ACTION_PINS.items()]
				if path == _CI_WORKFLOW:
					expected.extend(
						[f"{action}@{revision}" for action, revision in _BASE_ACTION_PINS.items()]
					)
					expected.append(_NODE_ACTION_PIN)
				self.assertEqual(uses, expected)

	def test_ci_runs_the_exact_hermetic_module_set(self) -> None:
		text = _read_workflow(_CI_WORKFLOW)
		discovered = tuple(re.findall(r"(?m)^\s{10}(kuck_serwis\.tests\.test_[a-z0-9_]+)\s*$", text))
		self.assertEqual(discovered, _PURE_MODULES)
		self.assertIn("Pillow==12.2.0", text)
		self.assertIn("python -m compileall -q kuck_serwis", text)

	def test_fullstack_job_uses_exact_public_dependencies(self) -> None:
		text = _read_workflow(_CI_WORKFLOW)
		self.assertIn("name: ci/frappe-fullstack", text)
		self.assertNotIn("ci/frappe-fullstack (BLOCKED)", text)
		self.assertIn("frappe-bench==5.29.1", text)
		self.assertIn("python-version: '3.14.6'", text)
		self.assertIn("node-version: '24.14.0'", text)
		self.assertIn("FRAPPE_SHA: 5c16f12192815204a7eda2b2ab365a557a6e7def", text)
		self.assertIn("ERPNEXT_SHA: 6bcab7cfc8df98660a6b61422bc8765cf58552f1", text)
		self.assertIn("mariadb:11.8.8@sha256:", text)
		self.assertIn("valkey/valkey:9.0.5-alpine@sha256:", text)
		self.assertIn("bench --site test_site install-app erpnext", text)
		self.assertIn("bench --site test_site install-app kuck_serwis", text)
		self.assertIn("bench --site test_site migrate", text)
		self.assertIn("bench --site test_site run-tests --app kuck_serwis", text)
		self.assertIn("secrets.token_bytes(32)", text)
		self.assertIn('set-config encryption_key "$site_encryption_key"', text)
		self.assertIn("unset site_encryption_key", text)
		self.assertNotRegex(text, r"(?m)^\s+encryption_key:\s+\S+")
		for forbidden in ("kuck_shop", "webshop", "payments", "allow_guest", "--force"):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, text)

	def test_linter_checks_the_exact_candidate_delta(self) -> None:
		text = _read_workflow(_LINTER_WORKFLOW)
		self.assertIn("fetch-depth: 0", text)
		self.assertIn("PRE_COMMIT_BASE:", text)
		self.assertIn('git cat-file -e "${base}^{commit}"', text)
		self.assertIn('pre-commit run --from-ref "$base" --to-ref "$GITHUB_SHA"', text)
		self.assertNotIn("pre-commit run --all-files", text)

	def test_push_scope_is_only_version_16(self) -> None:
		for path in (_CI_WORKFLOW, _LINTER_WORKFLOW):
			with self.subTest(path=path.name):
				text = _read_workflow(path)
				self.assertEqual(text.count("branches:\n      - version-16"), 1)


if __name__ == "__main__":
	unittest.main()
